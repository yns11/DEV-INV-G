"""Variance reconciliation, KPIs, controls, analytics and AI assistance.

This is the module that replaces ``BILAN INVENTAIRE.xlsx`` — its thirteen tabs,
its 100 000 formula rows and its ``#REF!`` errors.

Everything it returns is derived: nothing here is a stored truth. Given the same
frozen snapshot, the same counts and the same adjustments, it recomputes exactly
the same figures — which is what makes a number quoted in a steering committee
defensible six months later.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import pandas as pd

from ..db import new_id
from ..domain.controls import (
    check_book_stock,
    check_referentials,
    check_variances,
    check_zones,
    summarise,
)
from ..domain.enums import AuditAction
from ..domain.models import Campaign, VarianceAnalysis, VarianceLine
from ..domain.variance import (
    CountedQty,
    KpiBlock,
    VarianceSet,
    aggregate_by,
    build_variances,
    compute_kpis,
    is_material,
    pareto,
)
from ..errors import NotFoundError, ValidationError
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["AnalysisService"]

#: Dimensions the analysis screens may group by.
DIMENSIONS = ("item", "warehouse", "location", "item_type", "category", "program")


class AnalysisService:
    """Reconciliation and analysis of a campaign."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx
        self._variance_cache: dict[tuple[str, str], list[VarianceLine]] = {}

    # ------------------------------------------------------- reconciliation

    def variances(
        self, campaign: Campaign, *, granularity: str = "item"
    ) -> list[VarianceLine]:
        """Reconcile book stock against counts, at the requested granularity.

        ``item`` is the financial view: a transfer between two bins is not a
        stock variance, so collapsing locations is the honest default for money.
        ``item_location`` is the operational view: it tells a team which bin to
        go and recount.

        Cached per (campaign, granularity) for the lifetime of the request, so a
        screen that shows KPIs, a Pareto chart and a table pays the query once.
        """
        key = (campaign.id, granularity)
        cached = self._variance_cache.get(key)
        if cached is not None:
            return cached

        ctx = self.ctx
        counted = [
            CountedQty(
                item_number=row["item_number"],
                warehouse_id=row["warehouse_id"],
                location_id=row["location_id"],
                qty=row["qty"] if isinstance(row["qty"], Decimal)
                else Decimal(str(row["qty"])),
            )
            for row in ctx.journals.counted_quantities(campaign.id)
        ]
        lines = build_variances(
            campaign=campaign,
            book_stock=ctx.book_stock.list(campaign.id),
            counted=counted,
            items=ctx.referentials.items_by_number(campaign.id),
            locations=ctx.referentials.locations_by_key(campaign.id),
            adjustments=ctx.adjustments.list(campaign.id),
            granularity=granularity,
        )
        self._variance_cache[key] = lines
        return lines

    def kpis(self, campaign: Campaign) -> KpiBlock:
        return compute_kpis(self.variances(campaign, granularity="item"), campaign=campaign)

    def aggregate(
        self, campaign: Campaign, dimension: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        if dimension not in DIMENSIONS:
            raise ValidationError(
                f"Dimension d'analyse inconnue : {dimension!r}.",
                allowed=list(DIMENSIONS),
            )
        granularity = "item_location" if dimension in ("warehouse", "location") else "item"
        groups = aggregate_by(
            self.variances(campaign, granularity=granularity),
            dimension,
            campaign=campaign,
        )
        return [_group_payload(g) for g in groups[:limit]]

    def top_variances(
        self,
        campaign: Campaign,
        *,
        limit: int = 100,
        material_only: bool = False,
        granularity: str = "item",
    ) -> list[dict[str, Any]]:
        """The exception list — the screen a manager actually works from."""
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        analyses = {a.item_number: a for a in ctx.analysis.list_analyses(campaign.id)}
        lines = self.variances(campaign, granularity=granularity)
        if material_only:
            lines = [
                l for l in lines
                if is_material(l, campaign.threshold_for(l.item_type))
            ]
        lines = sorted(lines, key=lambda l: abs(l.variance_value), reverse=True)[:limit]

        out: list[dict[str, Any]] = []
        for line in lines:
            item = items.get(line.item_number)
            analysis = analyses.get(line.item_number)
            out.append({
                "itemNumber": line.item_number,
                "name": item.name if item else "",
                "warehouseId": line.warehouse_id,
                "locationId": line.location_id,
                "itemType": str(line.item_type),
                "category": line.category,
                "program": line.program,
                "unit": line.unit,
                "unitCost": float(line.unit_cost),
                "bookQty": float(line.book_qty),
                "bookValue": float(line.book_value),
                "countedQty": float(line.counted_qty),
                "varianceQty": float(line.variance_qty),
                "varianceValue": float(line.variance_value),
                "adjustedQty": float(line.adjusted_qty),
                "residualQty": float(line.residual_qty),
                "residualValue": float(line.residual_value),
                "finalQty": float(line.final_qty),
                "countedOnly": line.counted_only,
                "bookOnly": line.book_only,
                "isMaterial": is_material(
                    line, campaign.threshold_for(line.item_type)
                ),
                "causeCode": analysis.cause_code if analysis else None,
                "comment": analysis.comment if analysis else "",
                "accepted": analysis.accepted if analysis else False,
                "aiSuggestedCause": analysis.ai_suggested_cause if analysis else None,
                "aiConfidence": analysis.ai_confidence if analysis else None,
                "aiRationale": analysis.ai_rationale if analysis else "",
            })
        return out

    def pareto(
        self, campaign: Campaign, *, coverage: float = 0.8
    ) -> list[dict[str, Any]]:
        groups = aggregate_by(
            self.variances(campaign, granularity="item"), "item", campaign=campaign
        )
        return [_group_payload(g) for g in pareto(groups, coverage=Decimal(str(coverage)))]

    # ---------------------------------------------------------------- controls

    def controls(self, campaign: Campaign) -> dict[str, Any]:
        """Every control applicable to the campaign's current data."""
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        bom_links = ctx.referentials.list_bom_links(campaign.id)
        findings = check_referentials(items=items, bom_links=bom_links)

        zones = ctx.sheets.list_zones(campaign.id)
        if zones:
            findings += check_zones(
                zones=zones,
                sheets=ctx.sheets.list_sheets(campaign.id),
                lines_by_sheet=ctx.sheets.lines_by_sheet(campaign.id),
            )

        book_stock = ctx.book_stock.list(campaign.id)
        if book_stock:
            findings += check_book_stock(
                book_stock=book_stock,
                items=items,
                locations=ctx.referentials.locations_by_key(campaign.id),
            )
            findings += check_variances(
                campaign=campaign, variances=self.variances(campaign, granularity="item")
            )
        return {
            "summary": summarise(findings),
            "findings": [f.model_dump(mode="json") for f in findings],
        }

    # ---------------------------------------------------------------- frames

    def frame(self, campaign: Campaign, *, granularity: str = "item") -> pd.DataFrame:
        """The analytic frame, with WIP and movement features attached."""
        from ..analytics import attach_movement_features, attach_wip_features, build_frame

        ctx = self.ctx
        frame = build_frame(
            self.variances(campaign, granularity=granularity),
            campaign=campaign,
            items=ctx.referentials.items_by_number(campaign.id),
        )
        frame = attach_wip_features(frame, ctx.consolidation.wip_breakdown(campaign.id))
        frame = attach_movement_features(frame, ctx.adjustments.list(campaign.id))
        return frame

    # -------------------------------------------------------------- analytics

    def analytics(self, campaign: Campaign) -> dict[str, Any]:
        """The full analytic pack for the analysis dashboard.

        Assembled in one call because every block shares the same frame; running
        them separately would recompute the reconciliation four times.
        """
        from ..analytics import (
            abc_xyz,
            benford_check,
            cluster_patterns,
            detect_anomalies,
            digit_preference,
            pareto_frontier,
            recount_priority,
        )

        frame = self.frame(campaign, granularity="item_location")
        if frame.empty:
            return {"available": False, "reason": "Aucun écart à analyser."}

        anomalies = detect_anomalies(frame)
        clusters = cluster_patterns(anomalies.frame)
        segmentation = abc_xyz(frame)
        counted = frame["counted_qty"].tolist()

        return {
            "available": True,
            "abcXyz": {
                "summary": _records(segmentation.summary),
                "items": _records(segmentation.frame.head(500)),
            },
            "pareto": _records(pareto_frontier(frame)),
            "anomalies": {
                "method": anomalies.method,
                "contamination": anomalies.contamination,
                "features": anomalies.feature_names,
                "flagged": _records(
                    anomalies.frame[anomalies.frame["is_anomaly"]]
                    .sort_values("anomaly_score", ascending=False)
                    .head(100)
                ),
            },
            "clusters": {
                "n": clusters.n_clusters,
                "silhouette": clusters.silhouette,
                "profiles": _records(clusters.profiles),
            },
            "recountPriority": _records(recount_priority(anomalies.frame, top_n=50)),
            "dataQuality": {
                "benford": benford_check(counted).as_dict(),
                "digitPreference": digit_preference(counted),
            },
        }

    def compare(self, campaign: Campaign, other_campaign_id: str) -> dict[str, Any]:
        """Compare this campaign to a previous one.

        When adjustment movements dated between the two count dates are loaded,
        the comparison also checks the bookkeeping identity
        ``book_now == book_then + movements_between`` and reports the drift.
        """
        from ..analytics import compare_campaigns

        ctx = self.ctx
        other = ctx.campaigns.get(other_campaign_id)
        current_frame = self.frame(campaign, granularity="item")
        other_service = AnalysisService(ctx)
        previous_frame = other_service.frame(other, granularity="item")

        low, high = sorted([campaign.count_date, other.count_date])
        movements = [
            {"item_number": a.item_number, "qty": float(a.qty)}
            for a in ctx.adjustments.list(other.id)
            if a.physical_date and low <= a.physical_date <= high
        ]
        movement_frame = pd.DataFrame(movements) if movements else None

        result = compare_campaigns(
            current_frame, previous_frame, movements_between=movement_frame
        )
        return {
            "current": {"code": campaign.code, "countDate": str(campaign.count_date)},
            "previous": {"code": other.code, "countDate": str(other.count_date)},
            "movementsLoaded": len(movements),
            "recurrenceSummary": (
                result["recurrence"].value_counts().to_dict()
                if "recurrence" in result else {}
            ),
            "rows": _records(result.head(500)),
        }

    # ------------------------------------------------------------------- AI

    def suggest_causes(self, campaign: Campaign, *, max_items: int = 40) -> int:
        """Ask the model to propose a root cause for the largest variances.

        Proposals are stored in the ``ai_*`` columns only. An analyst still has
        to accept one for it to become the campaign's answer.
        """
        from ..ai import InsightEngine

        ctx = self.ctx
        frame = self.frame(campaign, granularity="item")
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
            variances=self.variances(campaign, granularity="item"),
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

        kpis = self.kpis(campaign)
        lines = self.variances(campaign, granularity="item")
        top = aggregate_by(lines, "item", campaign=campaign)[:15]
        by_warehouse = aggregate_by(
            self.variances(campaign, granularity="item_location"),
            "warehouse",
            campaign=campaign,
        )
        return InsightEngine().campaign_summary(
            campaign_label=f"{campaign.code} — {campaign.label}",
            count_date=str(campaign.count_date),
            kpis=kpis,
            top_variances=top,
            by_warehouse=by_warehouse,
            control_summary=self.controls(campaign)["summary"],
            cause_split=self.cause_split(campaign),
        )

    def explain(self, campaign: Campaign, item_number: str) -> dict[str, Any]:
        """A focused explanation of one article's variance."""
        from ..ai import InsightEngine

        ctx = self.ctx
        line = next(
            (l for l in self.variances(campaign, granularity="item")
             if l.item_number == item_number),
            None,
        )
        if line is None:
            raise NotFoundError("Article introuvable dans les écarts.", item=item_number)

        breakdown = ctx.consolidation.wip_breakdown(campaign.id, child_item=item_number)
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
        )
        return {
            "itemNumber": item_number,
            "explanation": text,
            "wipBreakdown": breakdown,
            "movements": movements,
        }

    # ------------------------------------------------------- human analysis

    def save_analysis(
        self,
        campaign: Campaign,
        *,
        item_number: str,
        cause_code: str | None,
        comment: str = "",
        accepted: bool = False,
    ) -> VarianceAnalysis:
        ctx = self.ctx
        ctx.guard(campaign, "analysis")
        existing = {a.item_number: a for a in ctx.analysis.list_analyses(campaign.id)}
        previous = existing.get(item_number)
        analysis = VarianceAnalysis(
            id=previous.id if previous else new_id(),
            campaign_id=campaign.id,
            item_number=item_number,
            cause_code=cause_code,
            comment=comment,
            analyst=ctx.actor,
            accepted=accepted,
            ai_suggested_cause=previous.ai_suggested_cause if previous else None,
            ai_confidence=previous.ai_confidence if previous else None,
            ai_rationale=previous.ai_rationale if previous else "",
        )
        ctx.analysis.upsert_analysis(analysis, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="variance_analysis",
            entity_id=analysis.id,
            summary=f"{item_number} : cause {cause_code or '—'}",
            before=previous.model_dump(mode="json") if previous else None,
            after=analysis.model_dump(mode="json"),
        )
        return analysis

    def cause_split(self, campaign: Campaign) -> dict[str, Any]:
        """Variance value broken down by assigned root cause.

        Unassigned variance is reported explicitly rather than hidden: "how much
        do we still not understand?" is the question that drives the next
        campaign's action plan.
        """
        ctx = self.ctx
        analyses = {a.item_number: a for a in ctx.analysis.list_analyses(campaign.id)}
        causes = {c.code: c for c in ctx.analysis.list_causes(active_only=False)}
        buckets: dict[str, dict[str, Any]] = {}
        unassigned = {"code": None, "label": "Non affecté", "value": 0.0,
                      "absValue": 0.0, "items": 0}

        for line in self.variances(campaign, granularity="item"):
            if line.variance_value == 0:
                continue
            analysis = analyses.get(line.item_number)
            code = analysis.cause_code if analysis and analysis.cause_code else None
            target = (
                buckets.setdefault(code, {
                    "code": code,
                    "label": causes[code].label if code in causes else code,
                    "family": causes[code].family if code in causes else "",
                    "value": 0.0,
                    "absValue": 0.0,
                    "items": 0,
                })
                if code else unassigned
            )
            target["value"] += float(line.variance_value)
            target["absValue"] += abs(float(line.variance_value))
            target["items"] += 1

        rows = sorted(buckets.values(), key=lambda b: -b["absValue"])
        if unassigned["items"]:
            rows.append(unassigned)
        total_abs = sum(r["absValue"] for r in rows) or 1.0
        for row in rows:
            row["share"] = round(row["absValue"] / total_abs, 4)
        return {"rows": rows, "unassignedShare": round(
            unassigned["absValue"] / total_abs, 4
        )}


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #

def _group_payload(group: VarianceSet) -> dict[str, Any]:
    return {
        "key": group.key,
        "bookQty": float(group.book_qty),
        "bookValue": float(group.book_value),
        "varianceQty": float(group.variance_qty),
        "varianceValue": float(group.variance_value),
        "absVarianceQty": float(group.abs_variance_qty),
        "absVarianceValue": float(group.abs_variance_value),
        "residualValue": float(group.residual_value),
        "lineCount": group.line_count,
        "materialCount": group.material_count,
    }


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → JSON-safe records, with NaN mapped to ``None``."""
    if frame is None or frame.empty:
        return []
    return frame.replace({float("nan"): None}).where(pd.notna(frame), None).to_dict(
        orient="records"
    )
