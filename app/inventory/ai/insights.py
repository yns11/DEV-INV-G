"""AI-assisted analysis: root-cause proposals and campaign narratives.

Two firm rules govern everything here.

**The AI proposes, a human decides.** A suggested cause is written to the
``ai_suggested_cause`` columns, never to ``cause_code``. The UI shows it as a
proposal with its confidence and its rationale, and an analyst has to accept it.

**The AI never sees more than it needs.** Prompts carry aggregated figures and
article identifiers, not the whole campaign; that keeps token cost bounded, the
latency inside the platform's 120 s request budget, and the blast radius of a
prompt-injection attempt through an imported file as small as possible.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..domain.models import AssignableCause, Item, VarianceLine
from ..domain.variance import KpiBlock, VarianceSet
from .client import LlmClient, get_llm_client

log = logging.getLogger(__name__)

__all__ = ["CauseSuggestion", "InsightEngine"]


@dataclass(slots=True)
class CauseSuggestion:
    """One AI proposal for the root cause of an article's variance."""

    item_number: str
    cause_code: str
    confidence: float
    rationale: str

    def as_tuple(self) -> tuple[str, str, float, str]:
        return (self.item_number, self.cause_code, self.confidence, self.rationale)


_CAUSE_SYSTEM = """\
Tu es un expert en gestion de stock industriel (WMS, ERP Dynamics 365, \
procure-to-pay) sur un site de production de moteurs électriques.

On te donne des écarts d'inventaire et un référentiel de causes standard. Pour \
chaque article, tu proposes la cause la plus probable, en t'appuyant \
uniquement sur les faits fournis (signe et taille de l'écart, type d'article, \
programme, emplacements concernés, présence de WIP, historique).

Règles :
- Tu choisis un code de cause dans la liste fournie, jamais un autre.
- Ta confiance reflète honnêtement la force des indices : au-dessous de 0.5 \
quand plusieurs causes sont également plausibles.
- Ta justification est factuelle, en une ou deux phrases, et cite les chiffres \
qui la fondent.
- Tu ne proposes aucune action corrective ici : uniquement le diagnostic.

Tu réponds exclusivement en JSON valide."""

_NARRATIVE_SYSTEM = """\
Tu es responsable inventaire d'un site industriel. Tu rédiges la synthèse de \
campagne destinée au comité de direction.

Règles :
- Tu t'appuies exclusivement sur les chiffres fournis ; tu n'inventes aucun \
montant, aucun pourcentage, aucun article.
- Tu écris en français, dans un style factuel et direct, sans superlatif.
- Tu distingues explicitement la fiabilité nette (écarts compensés) de la \
fiabilité brute (somme des écarts absolus), car elles ne racontent pas la \
même chose.
- Tu es explicite sur ce qui reste inexpliqué."""


class InsightEngine:
    """AI layer over the computed variances."""

    def __init__(self, client: LlmClient | None = None) -> None:
        self._client = client or get_llm_client()

    # -------------------------------------------------------- cause proposals

    def suggest_causes(
        self,
        *,
        variances: Sequence[VarianceLine],
        causes: Sequence[AssignableCause],
        items: dict[str, Item],
        features: dict[str, dict[str, Any]] | None = None,
        max_items: int = 40,
    ) -> list[CauseSuggestion]:
        """Propose a root cause for the largest variances.

        Only the top *max_items* by absolute value are sent: they carry the vast
        majority of the euro impact, and asking the model about a 3 € difference
        wastes tokens on a line nobody will ever act on.

        :param features: optional per-article signals computed by
            :mod:`inventory.analytics` (WIP share, anomaly score, digit
            preference…). They measurably sharpen the diagnosis.
        """
        ranked = sorted(
            (v for v in variances if v.variance_value != 0),
            key=lambda v: abs(v.variance_value),
            reverse=True,
        )[:max_items]
        if not ranked:
            return []

        cause_catalogue = "\n".join(
            f"- {c.code} : {c.label}" + (f" ({c.family})" if c.family else "")
            for c in causes
        )
        payload_lines = [
            _variance_payload(v, items.get(v.item_number), (features or {}).get(v.item_number))
            for v in ranked
        ]

        user = (
            "Référentiel des causes :\n"
            f"{cause_catalogue}\n\n"
            "Écarts à diagnostiquer :\n"
            f"{json.dumps(payload_lines, ensure_ascii=False, indent=1)}\n\n"
            'Renvoie : {"suggestions": [{"item_number": "...", "cause_code": "...", '
            '"confidence": 0.0, "rationale": "..."}]}'
        )

        try:
            payload, _ = self._client.complete_json(
                system=_CAUSE_SYSTEM, user=user, max_tokens=6000
            )
        except Exception:
            log.exception("Cause suggestion failed")
            return []

        valid_codes = {c.code for c in causes}
        known_items = {v.item_number for v in ranked}
        out: list[CauseSuggestion] = []
        for raw in payload.get("suggestions") or []:
            if not isinstance(raw, dict):
                continue
            item_number = str(raw.get("item_number") or "").strip().upper()
            code = str(raw.get("cause_code") or "").strip()
            # Reject anything outside what we asked about: a cause the site does
            # not use, or an article we never mentioned.
            if item_number not in known_items or code not in valid_codes:
                continue
            out.append(
                CauseSuggestion(
                    item_number=item_number,
                    cause_code=code,
                    confidence=_confidence(raw.get("confidence")),
                    rationale=str(raw.get("rationale") or "").strip()[:1000],
                )
            )
        return out

    # ------------------------------------------------------------- narratives

    def campaign_summary(
        self,
        *,
        campaign_label: str,
        count_date: str,
        kpis: KpiBlock,
        top_variances: Sequence[VarianceSet],
        by_warehouse: Sequence[VarianceSet],
        control_summary: dict[str, Any],
        cause_split: dict[str, Any] | None = None,
    ) -> str:
        """A directors'-committee summary grounded in the supplied figures."""
        facts = {
            "campagne": campaign_label,
            "dateComptage": count_date,
            "kpis": kpis.as_dict(),
            "topEcarts": [
                {
                    "cle": g.key,
                    "ecartValeur": float(g.variance_value),
                    "ecartAbsolu": float(g.abs_variance_value),
                    "stockLivre": float(g.book_value),
                    "lignes": g.line_count,
                }
                for g in top_variances[:15]
            ],
            "parEntrepot": [
                {
                    "entrepot": g.key,
                    "stockLivre": float(g.book_value),
                    "ecartValeur": float(g.variance_value),
                    "ecartAbsolu": float(g.abs_variance_value),
                }
                for g in by_warehouse
            ],
            "controles": control_summary,
            "repartitionCauses": cause_split or {},
        }
        user = (
            "Voici les chiffres consolidés de la campagne :\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}\n\n"
            "Rédige une synthèse structurée en markdown avec ces sections :\n"
            "## Message clé (2 phrases maximum)\n"
            "## Chiffres de la campagne\n"
            "## Principaux contributeurs à l'écart\n"
            "## Points de vigilance et contrôles\n"
            "## Actions recommandées (3 à 5, priorisées, avec l'enjeu en €)\n"
        )
        try:
            response = self._client.complete(
                system=_NARRATIVE_SYSTEM, user=user, max_tokens=2500, temperature=0.2
            )
            return response.text.strip()
        except Exception:
            log.exception("Campaign summary generation failed")
            return (
                "_La synthèse IA n'a pas pu être générée. Les indicateurs et les "
                "analyses chiffrées de cette page restent complets et utilisables._"
            )

    def explain_variance(
        self,
        *,
        line: VarianceLine,
        item: Item | None,
        wip_breakdown: Sequence[dict[str, Any]] = (),
        movements: Sequence[dict[str, Any]] = (),
    ) -> str:
        """A short, focused explanation of one article's variance."""
        facts = {
            "article": _variance_payload(line, item, None),
            "compositionWip": [dict(b) for b in wip_breakdown[:20]],
            "mouvements": [dict(m) for m in movements[:30]],
        }
        user = (
            f"{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}\n\n"
            "Explique cet écart en 3 à 5 puces factuelles, puis propose la "
            "vérification terrain ou informatique la plus rentable à mener en "
            "premier. Pas d'introduction, pas de conclusion."
        )
        try:
            return self._client.complete(
                system=_NARRATIVE_SYSTEM, user=user, max_tokens=900, temperature=0.1
            ).text.strip()
        except Exception:
            log.exception("Variance explanation failed")
            return "_Explication IA indisponible pour le moment._"


# --------------------------------------------------------------------------- #
# Payload shaping
# --------------------------------------------------------------------------- #

def _variance_payload(
    line: VarianceLine, item: Item | None, features: dict[str, Any] | None
) -> dict[str, Any]:
    """Compact, fact-only description of one variance line."""
    payload: dict[str, Any] = {
        "item_number": line.item_number,
        "designation": (item.name if item else "")[:80],
        "type": str(line.item_type),
        "categorie": line.category,
        "programme": line.program,
        "unite": line.unit,
        "stockLivreQte": float(line.book_qty),
        "stockLivreValeur": float(line.book_value),
        "compteQte": float(line.counted_qty),
        "ecartQte": float(line.variance_qty),
        "ecartValeur": float(line.variance_value),
        "ajusteQte": float(line.adjusted_qty),
        "residuelValeur": float(line.residual_value),
        "compteSansStockLivre": line.counted_only,
        "stockLivreNonCompte": line.book_only,
    }
    if line.warehouse_id:
        payload["entrepot"] = line.warehouse_id
        payload["emplacement"] = line.location_id
    if features:
        payload["signaux"] = {
            k: (float(v) if isinstance(v, (int, float, Decimal)) else v)
            for k, v in features.items()
        }
    return payload


def _confidence(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.5
