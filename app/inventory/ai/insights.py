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

from ..domain.models import AssignableCause, BackflushLine, Item, VarianceLine
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


#: Comment lire les chiffres d'une campagne. Partagé par les trois prompts.
#:
#: Il était sous-entendu, et le modèle l'a donc deviné. Les propositions se
#: rabattaient sur « écart de comptage » parce que c'est la cause qu'on devine
#: sans rien savoir du site : le backflush — le mécanisme qui explique la plus
#: grosse part des dérives d'un stock déduit de la production, et qui a son
#: propre code de cause — n'était nulle part dans ce qu'on lui donnait à lire.
_DOMAIN = """\
Contexte du site : usine de moteurs électriques, ERP Dynamics 365, stock géré \
par emplacement (WMS) sur certains entrepôts seulement.

Vocabulaire des chiffres qu'on te donne :

- « stock ERP » est la photo du système gelée juste avant le comptage. C'est la \
référence, et elle ne bouge plus.
- « écart » = stock physique − stock ERP. Positif : on a trouvé plus que ce que \
le système annonce. Négatif : il manque.
- « ajusté » est ce qui a bougé *après* le comptage (réception tardive, \
recomptage, sortie postée depuis). L'écart en tient compte ; \
« écartAvantAjustementsValeur » est ce que le comptage seul avait trouvé.
- Le comptage se fait en trois sections. « Bord de ligne » : le composant est \
compté tel quel. « WIP (à éclater) » : un en-cours non déclaré à l'ERP, éclaté \
en nomenclature pour se ramener à des composants. « WIP assemblé » : un \
ensemble déjà déclaré, compté tel quel. Une part éclatée importante veut dire \
que la quantité vient d'un calcul de nomenclature, pas d'un décompte direct.

Le backflush, et pourquoi il compte ici :

La production ne saisit pas ses sorties de composants ligne à ligne : elles \
sont déduites de la quantité déclarée produite, au prorata de la nomenclature. \
Cette déduction suppose que la consommation réelle égale la théorique, et \
l'écart backflush mesure exactement cet écart-là :

    écart backflush = consommation théorique − consommation réelle

Un backflush **positif** veut dire que la déduction a pris moins que la théorie : \
la pièce est sortie du magasin sans que l'ERP l'enregistre, donc le stock \
système est surévalué et le comptage trouvera moins. C'est pourquoi il entre \
dans le raisonnement d'inventaire avec le signe inversé — c'est ce que dit \
« partBackflush ».

« inexpliqué » = écart − part backflush : ce que la production **n'explique \
pas**, et donc ce qui reste à diagnostiquer. « tauxExplication » vaut 1 quand \
le backflush explique tout l'écart, 0 quand il n'apporte rien, et devient \
négatif quand en tenir compte creuse l'écart au lieu de le combler — ce dernier \
cas est un signal, pas une erreur de calcul.

« backflushMesure » à faux veut dire que la période ne porte aucune ligne pour \
cet article : la production ne l'a pas touché. Ce n'est pas la même chose \
qu'un écart backflush nul mesuré, et il ne faut pas conclure de l'un à l'autre."""

_CAUSE_SYSTEM = f"""\
Tu es un expert en gestion de stock industriel (WMS, ERP Dynamics 365, \
procure-to-pay) sur un site de production de moteurs électriques.

{_DOMAIN}

On te donne des écarts d'inventaire et un référentiel de causes standard. Pour \
chaque article, tu proposes la cause la plus probable, en t'appuyant \
uniquement sur les faits fournis.

Règles :
- Tu choisis un code de cause dans la liste fournie, jamais un autre.
- Tu regardes le backflush avant de conclure : un écart que la part backflush \
explique en grande partie n'est pas une erreur de comptage, et le référentiel \
porte une cause pour cela.
- À l'inverse, un écart que le backflush n'explique pas se diagnostique sur ce \
qu'il en reste — l'inexpliqué — et non sur l'écart brut.
- Ta confiance reflète honnêtement la force des indices : au-dessous de 0.5 \
quand plusieurs causes sont également plausibles.
- Ta justification est factuelle, en une ou deux phrases, et cite les chiffres \
qui la fondent.
- Tu ne proposes aucune action corrective ici : uniquement le diagnostic.

Tu réponds exclusivement en JSON valide."""

_NARRATIVE_SYSTEM = f"""\
Tu es responsable inventaire d'un site industriel. Tu rédiges la synthèse de \
campagne destinée au comité de direction.

{_DOMAIN}

Règles :
- Tu t'appuies exclusivement sur les chiffres fournis ; tu n'inventes aucun \
montant, aucun pourcentage, aucun article.
- Tu écris en français, dans un style factuel et direct, sans superlatif.
- Tu distingues explicitement la fiabilité nette (écarts compensés) de la \
fiabilité brute (somme des écarts absolus), car elles ne racontent pas la \
même chose.
- Tu distingues de même la part que la production explique — le backflush — de \
ce qui reste inexpliqué. Ce sont deux conclusions opposées : la première mène à \
un chantier sur le mécanisme de déduction, la seconde à une enquête terrain.
- Tu es explicite sur ce qui reste inexpliqué."""

_EXPLAIN_SYSTEM = f"""\
Tu es analyste inventaire sur un site industriel. On te soumet **un** article, \
et tu expliques son écart à quelqu'un qui devra aller vérifier sur le terrain.

{_DOMAIN}

Règles :
- Tu t'appuies exclusivement sur les faits fournis ; tu n'inventes ni chiffre, \
ni emplacement, ni mouvement.
- Tu ouvres par ce que le backflush explique — ou ne recouvre pas — avant toute \
autre hypothèse : c'est ce qui départage une dérive du mécanisme de déduction \
d'une erreur de comptage ou de flux.
- Quand la quantité vient d'un WIP éclaté, tu le dis : elle est calculée depuis \
une nomenclature, et une nomenclature fausse s'y voit comme un écart de stock.
- Tu écris en français, factuel et direct, sans introduction ni conclusion."""


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
        backflush: dict[str, Any] | None = None,
    ) -> str:
        """A directors'-committee summary grounded in the supplied figures.

        :param backflush: ce que la production explique à l'échelle de la
            campagne. Sans ce bloc, la synthèse présentait l'écart entier comme
            restant à élucider — ce qui est faux dès que le backflush est chargé,
            et mène le comité vers une enquête terrain là où le chantier est sur
            le mécanisme de déduction.
        """
        facts: dict[str, Any] = {
            "campagne": campaign_label,
            "dateComptage": count_date,
            "kpis": kpis.as_dict(),
            "topEcarts": [
                {
                    "cle": g.key,
                    "ecartValeur": float(g.variance_value),
                    "ecartAbsolu": float(g.abs_variance_value),
                    "stockErp": float(g.book_value),
                    "lignes": g.line_count,
                }
                for g in top_variances[:15]
            ],
            "parEntrepot": [
                {
                    "entrepot": g.key,
                    "stockErp": float(g.book_value),
                    "ecartValeur": float(g.variance_value),
                    "ecartAbsolu": float(g.abs_variance_value),
                }
                for g in by_warehouse
            ],
            "controles": control_summary,
            "repartitionCauses": cause_split or {},
        }
        if backflush:
            facts["backflush"] = dict(backflush)
        user = (
            "Voici les chiffres consolidés de la campagne :\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}\n\n"
            "Rédige une synthèse structurée en markdown avec ces sections :\n"
            "## Message clé (2 phrases maximum)\n"
            "## Chiffres de la campagne\n"
            "## Principaux contributeurs à l'écart\n"
            "## Points de vigilance et contrôles\n"
            "## Actions recommandées (3 à 5, priorisées, avec l'enjeu en €)\n"
            "\n"
            "Les chiffres de la campagne et les principaux contributeurs se "
            "présentent en **tableau** (format GitHub, colonnes de chiffres "
            "alignées à droite avec `---:`) : ce sont des mesures comparables "
            "ligne à ligne, et une énumération en prose se relit mal en comité.\n"
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
        backflush: BackflushLine | None = None,
        counting: dict[str, Any] | None = None,
        thresholds: dict[str, Any] | None = None,
    ) -> str:
        """A short, focused explanation of one article's variance.

        :param backflush: the frozen production figures for this article. Not a
            duplicate of what the line already carries: the line holds the net,
            and the diagnosis needs what the net is made of — 40 de
            sous-consommation contre 38 de sur-consommation ne se lit pas comme
            2, et ne mène pas à la même vérification.
        :param counting: how the quantity was obtained — les zones, et la part
            comptée telle quelle contre la part reconstituée par nomenclature.
        :param thresholds: ce que « significatif » veut dire sur cette campagne,
            pour que le modèle ne le devine pas à l'échelle de ses exemples.
        """
        facts: dict[str, Any] = {
            "article": _variance_payload(line, item, None),
            "compositionWip": [dict(b) for b in wip_breakdown[:20]],
            "mouvements": [dict(m) for m in movements[:30]],
        }
        if backflush is not None:
            facts["backflush"] = _backflush_payload(backflush)
        if counting:
            facts["comptage"] = dict(counting)
        if thresholds:
            facts["seuilsDeMaterialite"] = dict(thresholds)
        user = (
            f"{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}\n\n"
            "Explique cet écart en 3 à 5 puces factuelles, puis propose la "
            "vérification terrain ou informatique la plus rentable à mener en "
            "premier. Pas d'introduction, pas de conclusion."
        )
        try:
            return self._client.complete(
                system=_EXPLAIN_SYSTEM, user=user, max_tokens=900, temperature=0.1
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
        "stockErpQte": float(line.book_qty),
        "stockErpValeur": float(line.book_value),
        "compteQte": float(line.counted_qty),
        "ecartQte": float(line.variance_qty),
        "ecartValeur": float(line.variance_value),
        "ajusteQte": float(line.adjusted_qty),
        "écartAvantAjustementsValeur": float(line.counted_variance_value),
        "compteSansStockErp": line.counted_only,
        "stockErpNonCompte": line.book_only,
        # Le backflush, dans la convention d'inventaire — c'est-à-dire signe
        # déjà retourné. Il manquait entièrement, et le modèle diagnostiquait
        # donc des écarts de consommation comme des erreurs de comptage : le
        # référentiel porte pourtant une cause « Écart consommation
        # (backflush) », qu'il n'avait aucun moyen de choisir.
        "backflushMesure": line.backflush_measured,
        "partBackflushQte": float(line.backflush_share_qty),
        "partBackflushValeur": float(line.backflush_share_value),
        "inexpliqueQte": float(line.unexplained_qty),
        "inexpliqueValeur": float(line.unexplained_value),
        "tauxExplication": (
            None if line.explanation_rate is None else float(line.explanation_rate)
        ),
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


def _backflush_payload(line: BackflushLine) -> dict[str, Any]:
    """Ce dont le net est fait, et sur quelle période il a été mesuré.

    La période est là parce qu'un écart de consommation ne se lit pas sans
    elle : le même chiffre sur une semaine et sur un trimestre ne mène pas à la
    même conclusion, et sans la borne le modèle la supposait.
    """
    return {
        "periodeDebut": str(line.period_start),
        "periodeFin": str(line.period_end),
        "semaines": line.week_count,
        "assemblagesConcernes": line.parent_count,
        "consoTheoriqueQte": float(line.theoretical_qty),
        "consoReelleQte": float(line.actual_qty),
        "ecartNetQte": float(line.net_qty),
        "sousConsommeQte": float(line.under_consumed_qty),
        "surConsommeQte": float(line.over_consumed_qty),
        "partInventaireQte": float(line.inventory_share_qty),
    }


def _confidence(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.5
