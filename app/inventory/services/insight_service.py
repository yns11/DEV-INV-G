"""Ce que l'IA lit d'une campagne, et ce qu'elle en propose.

Trois usages, un seul dossier à composer : proposer une cause pour les plus gros
écarts, rédiger la synthèse de clôture, expliquer un article. Ils partagent la
même exigence, qui n'est pas celle du reste de l'analyse : **choisir ce que le
modèle voit**. Un prompt n'échoue pas quand il manque un fait — il devine, et
c'est ce qui est arrivé. Le contexte envoyé ignorait le backflush, si bien que
des écarts de consommation étaient diagnostiqués comme des erreurs de comptage,
alors que le référentiel du site porte une cause pour cela.

Ce module tient donc la composition de ce dossier — quels faits, dans quel
vocabulaire — pendant que :mod:`inventory.ai.insights` tient la conversation
avec le modèle et :mod:`inventory.services.analysis_service` les chiffres
eux-mêmes. Il ne calcule aucun écart : il en demande.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..domain.enums import AuditAction, ItemType
from ..domain.models import Campaign, ConsolidatedLine, VarianceLine
from ..domain.variance import aggregate_by
from ..errors import NotFoundError
from .context import ServiceContext

if TYPE_CHECKING:
    from .analysis_service import AnalysisService

log = logging.getLogger(__name__)

__all__ = ["InsightService"]


class InsightService:
    """L'IA au-dessus des écarts calculés.

    Composition, pas héritage : le service d'analyse porte les chiffres, celui-ci
    porte ce qu'on en montre au modèle. Les deux se lisent séparément, et
    l'ajout d'un fait au dossier ne traverse plus mille lignes de calcul.
    """

    def __init__(self, ctx: ServiceContext, analysis: AnalysisService) -> None:
        self.ctx = ctx
        self.analysis = analysis

    def suggest_causes(self, campaign: Campaign, *, max_items: int = 40) -> int:
        """Ask the model to propose a root cause for the largest variances.

        Proposals are stored in the ``ai_*`` columns only. An analyst still has
        to accept one for it to become the campaign's answer.
        """
        from ..ai import InsightEngine

        ctx = self.ctx
        ctx.guard(campaign, "analysis")
        frame = self.analysis.frame(campaign, granularity="item")
        features: dict[str, dict[str, Any]] = {}
        if not frame.empty:
            from ..analytics import detect_anomalies

            enriched = detect_anomalies(frame).frame
            for row in enriched.itertuples():
                features[row.item_number] = {
                    "wipShare": round(float(getattr(row, "wip_share", 0.0)), 4),
                    "varianceRatio": (
                        None if pd.isna(row.variance_ratio)
                        else round(float(row.variance_ratio), 4)
                    ),
                    "anomalyPercentile": round(
                        float(getattr(row, "anomaly_percentile", 0.0)), 4
                    ),
                    "movementCount": int(getattr(row, "movement_count", 0)),
                }

        suggestions = InsightEngine().suggest_causes(
            variances=self.analysis.variances(campaign, granularity="item"),
            causes=ctx.analysis.list_causes(),
            items=ctx.referentials.items_by_number(campaign.id),
            features=features,
            max_items=max_items,
        )
        if not suggestions:
            return 0
        ctx.analysis.save_ai_suggestions(
            campaign.id, [s.as_tuple() for s in suggestions]
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="variance_analysis",
            summary=f"{len(suggestions)} proposition(s) de cause générée(s) par l'IA",
            after={"count": len(suggestions)},
        )
        return len(suggestions)

    def narrative(self, campaign: Campaign) -> str:
        """Generate the campaign summary for the closing report."""
        from ..ai import InsightEngine

        kpis = self.analysis.kpis(campaign)
        lines = self.analysis.variances(campaign, granularity="item")
        top = aggregate_by(lines, "item", campaign=campaign)[:15]
        by_warehouse = aggregate_by(
            self.analysis.variances(campaign, granularity="item_location"),
            "warehouse",
            campaign=campaign,
        )
        return InsightEngine().campaign_summary(
            campaign_label=f"{campaign.code} — {campaign.label}",
            count_date=str(campaign.count_date),
            kpis=kpis,
            top_variances=top,
            by_warehouse=by_warehouse,
            control_summary=self.analysis.controls(campaign)["summary"],
            cause_split=self.analysis.cause_split(campaign),
            backflush=_backflush_totals(lines),
        )

    def explain(self, campaign: Campaign, item_number: str) -> dict[str, Any]:
        """A focused explanation of one article's variance."""
        from ..ai import InsightEngine

        ctx = self.ctx
        line = next(
            (l for l in self.analysis.variances(campaign, granularity="item")
             if l.item_number == item_number),
            None,
        )
        if line is None:
            raise NotFoundError("Article introuvable dans les écarts.", item=item_number)

        breakdown = ctx.consolidation.wip_breakdown(campaign.id, child_item=item_number)
        counted = next(
            (l for l in ctx.consolidation.current_lines(campaign.id)
             if l.item_number == item_number),
            None,
        )
        movements = [
            {
                "date": str(a.physical_date) if a.physical_date else None,
                "kind": str(a.kind),
                "qty": float(a.qty),
                "value": float(a.value),
                "location": f"{a.warehouse_id}/{a.location_id}",
                "journal": a.journal_number,
            }
            for a in ctx.adjustments.list(campaign.id)
            if a.item_number == item_number
        ]
        text = InsightEngine().explain_variance(
            line=line,
            item=ctx.referentials.items_by_number(campaign.id).get(item_number),
            wip_breakdown=breakdown,
            movements=movements,
            # Ce que la ligne d'écart ne porte pas : de quoi le net backflush est
            # fait, d'où vient la quantité comptée, et ce que « significatif »
            # veut dire ici. Le modèle devinait les trois.
            backflush=ctx.backflush.by_item(campaign.id).get(item_number),
            counting=_counting_context(counted),
            thresholds=_threshold_context(campaign, line.item_type),
        )
        return {
            "itemNumber": item_number,
            "explanation": text,
            "wipBreakdown": breakdown,
            "movements": movements,
        }


# --------------------------------------------------------------------------- #
# Ce qu'on met dans le dossier
# --------------------------------------------------------------------------- #

def _backflush_totals(lines: Sequence[VarianceLine]) -> dict[str, Any] | None:
    """Ce que la production explique, à l'échelle de la campagne.

    ``None`` quand aucune ligne n'a d'écart backflush mesuré : annoncer « 0 € de
    part production » sur une campagne où le backflush n'a pas été chargé se
    lirait comme un résultat, alors que c'est une absence de mesure.

    Les valeurs absolues, et pas la somme signée : deux articles qui se
    compensent expliquent chacun le sien, et une somme nette de zéro dirait le
    contraire.
    """
    measured = [line for line in lines if line.backflush_measured]
    if not measured:
        return None
    gap = sum(abs(line.variance_value) for line in lines)
    explained = sum(abs(line.backflush_share_value) for line in measured)
    unexplained = sum(abs(line.unexplained_value) for line in lines)
    return {
        "articlesMesures": len(measured),
        "articlesTotal": len(lines),
        "ecartAbsoluValeur": float(gap),
        "partProductionAbsolueValeur": float(explained),
        "inexpliqueAbsoluValeur": float(unexplained),
        "tauxExplication": (
            None if gap == 0 else float(round(1 - unexplained / gap, 4))
        ),
    }


def _counting_context(line: ConsolidatedLine | None) -> dict[str, Any] | None:
    """D'où vient la quantité comptée, et par qui.

    Une quantité obtenue en éclatant un en-cours par nomenclature ne se
    diagnostique pas comme un décompte direct : une nomenclature fausse s'y lit
    exactement comme un écart de stock. C'est une distinction que le modèle ne
    peut pas déduire du chiffre, et qui change la vérification recommandée.
    """
    if line is None:
        return None
    return {
        "quantiteTotale": float(line.qty),
        "dontBordDeLigne": float(line.qty_line_side),
        "dontWipAssemble": float(line.qty_wip_ok),
        "dontWipEclateParNomenclature": float(line.qty_wip_exploded),
        "zones": list(line.zone_codes),
    }


def _threshold_context(
    campaign: Campaign, item_type: ItemType
) -> dict[str, Any] | None:
    """Ce que « significatif » veut dire sur cette campagne, pour ce type.

    Sans cela, le modèle jugeait l'importance d'un écart à l'échelle de ses
    propres exemples : trois cents euros y passaient pour beaucoup sur un site
    dont le seuil est à mille.
    """
    threshold = next(
        (t for t in campaign.thresholds if t.item_type == item_type), None
    )
    if threshold is None:
        return None
    return {
        "typeArticle": str(item_type),
        "valeurAbsolueEuros": float(threshold.value_abs_eur),
        "ecartRelatifQte": (
            None if threshold.qty_relative is None else float(threshold.qty_relative)
        ),
        "regle": "significatif quand les deux seuils configurés sont franchis",
    }
