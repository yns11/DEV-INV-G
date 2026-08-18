"""Reconciling two campaigns through the flows of the period between them.

Two inventories bracket a stretch of time. In between, an article's stock did
not move at random: it was received, produced, shipped, consumed and scrapped in
quantities the plant can put a number on. So the question this module answers is
a closed one — starting from the *counted* stock of the first inventory and
applying the period's flows, do we land on the *counted* stock of the second?

    stock attendu = compté(campagne initiale)
                  + réceptions          (chargées)
                  + production parent   (lue dans le backflush, dédoublonnée)
                  − expéditions         (chargées)
                  − consommation théo.  (lue dans le backflush)
                  − rebuts              (chargés, étape facultative)

What the gap between expected and counted measures is what none of those flows
explains. That is a different question from an inventory variance, and worth
keeping separate: an inventory variance compares a count to the ERP at one
instant, this compares two counts through everything that happened between them.

Three design decisions carry the weight.

**The earlier campaign is the earlier one by count date.** Never by creation
date. Campaigns created in one order and counted in the other exist, and it is
the count that bounds the period.

**Both ERP measures are frozen with the run.** The gold table is rebuilt every
night, so a past week can change; a report that cannot be replayed identically
does not survive its first review meeting.

**A reference missing from one of the two counts is not a zero.** It is a hole
in the comparison, and reading it as a zero would manufacture a variance the
size of the whole stock. Those lines are reported apart, never summed in.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction, FlowKind
from ..domain.models import (
    Campaign,
    StockFlowErp,
    StockFlowLine,
    StockFlowRun,
)
from ..domain.quantities import ZERO, quantize_money, quantize_qty
from ..errors import NotFoundError, ValidationError
from .context import ServiceContext
from .import_service import ImportOutcome, monday_of

log = logging.getLogger(__name__)

__all__ = ["StockFlowService", "FLOW_LABELS"]

#: How each loaded step names itself on screen and in the audit trail.
FLOW_LABELS = {
    FlowKind.RECEIPT: "réceptions",
    FlowKind.SHIPMENT: "expéditions",
    FlowKind.SCRAP: "rebuts",
}


class StockFlowService:
    """Compare two campaigns through the flows of the period between them."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------- runs

    def comparable_campaigns(self, campaign: Campaign) -> list[dict[str, Any]]:
        """The campaigns this one can be compared against.

        Only those counted *earlier*: the comparison walks forward through time,
        and offering a later campaign as a starting point would produce a period
        running backwards. Ordered by count date, most recent first — the
        previous inventory is the one people mean nine times out of ten.
        """
        earlier = [
            other for other in self.ctx.campaigns.list(limit=200)
            if other.id != campaign.id and other.count_date < campaign.count_date
        ]
        earlier.sort(key=lambda c: c.count_date, reverse=True)
        return [
            {
                "id": other.id,
                "code": other.code,
                "label": other.label,
                "countDate": other.count_date.isoformat(),
                "status": str(other.status),
                "weeks": (
                    monday_of(campaign.count_date) - monday_of(other.count_date)
                ).days // 7,
            }
            for other in earlier
        ]

    def open_run(self, campaign: Campaign, baseline_id: str) -> StockFlowRun:
        """Start — or re-open — the comparison with one earlier campaign.

        Idempotent on the pair: choosing the same baseline twice is somebody
        coming back to their comparison, not starting a second one. A second row
        would split the loaded quantities across two runs, of which the screen
        would only ever show one.
        """
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        baseline = self._baseline(campaign, baseline_id)

        start = monday_of(baseline.count_date)
        end = monday_of(campaign.count_date)
        if end <= start:
            raise ValidationError(
                f"« {baseline.code} » et « {campaign.code} » ont été comptées la "
                "même semaine : il n'y a pas de période entre les deux.",
                debut=baseline.count_date.isoformat(),
                fin=campaign.count_date.isoformat(),
            )

        run = ctx.stock_flow.upsert_run(
            StockFlowRun(
                id=new_id(),
                campaign_id=campaign.id,
                baseline_campaign_id=baseline.id,
                period_start=start,
                period_end=end,
            ),
            actor=ctx.actor,
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.CREATE,
            entity_type="stock_flow_run",
            entity_id=run.id,
            summary=(
                f"Comparaison avec « {baseline.code} », "
                f"du {start:%d/%m/%Y} au {end:%d/%m/%Y} (exclu)"
            ),
            after={
                "baseline": baseline.code,
                "periodStart": start.isoformat(),
                "periodEnd": end.isoformat(),
            },
        )
        return run

    def list_runs(self, campaign: Campaign) -> list[dict[str, Any]]:
        codes = {c.id: c for c in self.ctx.campaigns.list(limit=200)}
        out = []
        for run in self.ctx.stock_flow.list_runs(campaign.id):
            baseline = codes.get(run.baseline_campaign_id)
            out.append({
                **_run_payload(run),
                "baselineCode": baseline.code if baseline else "",
                "baselineLabel": baseline.label if baseline else "",
            })
        return out

    def delete_run(self, campaign: Campaign, run_id: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)
        ctx.stock_flow.delete_run(run.id)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="stock_flow_run",
            entity_id=run.id,
            summary="Comparaison supprimée",
        )

    # -------------------------------------------------------- loaded quantities

    def load_inputs(
        self, campaign: Campaign, run_id: str, kind: FlowKind, **kwargs: Any
    ) -> ImportOutcome:
        """Load one of the three steps: receipts, shipments or scrap.

        Scoped to its own step. The three loads are three separate gestures, and
        a user correcting their shipments must not lose the receipts they entered
        ten minutes earlier.
        """
        from ..ingest import map_stock_flow_inputs
        from .import_service import ImportService, _base_outcome

        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)

        _, parsed = ImportService(ctx).parse("stock_flow", **kwargs)
        outcome = _base_outcome("stock_flow", parsed)
        items = ctx.referentials.items_by_number(campaign.id)
        lines, errors = map_stock_flow_inputs(
            run.id, parsed.rows, kind=kind, items=items
        )
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_inputs(run.id, kind, lines, conn=conn)
            if kind is FlowKind.SCRAP:
                ctx.stock_flow.mark_scrap_loaded(run.id, actor=ctx.actor)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="stock_flow_input",
                entity_id=run.id,
                summary=f"{written} ligne(s) de {FLOW_LABELS[kind]} chargée(s)",
                after={"kind": str(kind), "rows": written},
                conn=conn,
            )

        outcome.rows_accepted = written
        outcome.details.update({
            "kind": str(kind),
            "totalQty": float(sum(line.qty for line in lines)),
            "outOfScope": max(0, len(parsed.rows) - written - len(errors)),
        })
        return outcome

    def skip_scrap(self, campaign: Campaign, run_id: str) -> StockFlowRun:
        """Record that the scrap step was deliberately left out.

        Written down rather than inferred from an empty table: « no scrap » and
        « scrap not entered » are the same zero and two different readings of the
        report, and only one of them is a complete comparison.
        """
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)
        with ctx.db.transaction() as conn:
            ctx.stock_flow.replace_inputs(run.id, FlowKind.SCRAP, [], conn=conn)
        ctx.stock_flow.mark_scrap_loaded(run.id, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="stock_flow_run",
            entity_id=run.id,
            summary="Étape rebuts ignorée volontairement",
        )
        return self._run(campaign, run_id)

    # ------------------------------------------------------------- ERP figures

    def refresh_erp(self, campaign: Campaign, run_id: str) -> dict[str, Any]:
        """Read production and theoretical consumption, and freeze them.

        Both come from the backflush fact table, at two different grains: an
        article is produced *as a parent* and consumed *as a component*, and a
        sub-assembly is legitimately both. The production half is collapsed to
        one row per parent and week before being summed — the fact table repeats
        that quantity on every component line, so summing it raw multiplies the
        output by the size of the bill of materials.
        """
        from ..ingest.erp import ErpReader

        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)

        reader = ErpReader()
        rows = reader.fetch_stock_flow(
            period_start=run.period_start,
            period_end=run.period_end,
            limit=ctx.settings.max_import_rows,
        )
        loaded_at = reader.backflush_loaded_at(
            period_start=run.period_start, period_end=run.period_end
        )
        items = ctx.referentials.items_by_number(campaign.id)
        lines = [
            StockFlowErp(
                run_id=run.id,
                item_number=row["item_number"],
                produced_qty=row["produced_qty"],
                consumed_qty=row["consumed_qty"],
            )
            for row in rows
            if row["item_number"] in items
        ]

        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_erp(run.id, lines, conn=conn)
            ctx.stock_flow.upsert_run(
                run.model_copy(update={
                    "source_loaded_at": loaded_at,
                    "erp_refreshed_at": dt.datetime.now(dt.UTC),
                }),
                actor=ctx.actor,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="stock_flow_erp",
                entity_id=run.id,
                summary=(
                    f"Production et consommation théorique figées : "
                    f"{written} article(s)"
                ),
                after={"items": written},
                conn=conn,
            )

        return {
            "items": written,
            "outOfScope": len(rows) - written,
            "producedQty": float(sum(line.produced_qty for line in lines)),
            "consumedQty": float(sum(line.consumed_qty for line in lines)),
            "sourceLoadedAt": loaded_at.isoformat() if loaded_at else None,
        }

    # ----------------------------------------------------------------- report

    def report(self, campaign: Campaign, run_id: str) -> dict[str, Any]:
        """The whole comparison: header, KPIs, aggregates and one line per article."""
        ctx = self.ctx
        run = self._run(campaign, run_id)
        baseline = ctx.campaigns.get(run.baseline_campaign_id)
        items = ctx.referentials.items_by_number(campaign.id)

        opening = _counted_by_item(ctx, baseline.id)
        closing = _counted_by_item(ctx, campaign.id)
        erp = {row.item_number: row for row in ctx.stock_flow.list_erp(run.id)}
        loaded: dict[FlowKind, dict[str, Decimal]] = {k: {} for k in FlowKind}
        for entry in ctx.stock_flow.list_inputs(run.id):
            loaded[entry.kind][entry.item_number] = entry.qty

        universe = (
            set(opening) | set(closing) | set(erp)
            | {item for step in loaded.values() for item in step}
        )
        lines: list[StockFlowLine] = []
        for item_number in sorted(universe):
            item = items.get(item_number)
            # An article the campaign excludes has no comparison to make: the
            # inventory deliberately does not hold it, so a variance on it would
            # be an artefact of the exclusion, not a finding.
            if item is not None and item.excluded_everywhere:
                continue
            flow = erp.get(item_number)
            lines.append(
                StockFlowLine(
                    item_number=item_number,
                    name=item.name if item else "",
                    unit=item.unit if item else "PCE",
                    unit_cost=item.std_price if item else ZERO,
                    opening_qty=opening.get(item_number, ZERO),
                    received_qty=loaded[FlowKind.RECEIPT].get(item_number, ZERO),
                    produced_qty=flow.produced_qty if flow else ZERO,
                    shipped_qty=loaded[FlowKind.SHIPMENT].get(item_number, ZERO),
                    consumed_qty=flow.consumed_qty if flow else ZERO,
                    scrapped_qty=loaded[FlowKind.SCRAP].get(item_number, ZERO),
                    closing_qty=closing.get(item_number, ZERO),
                    counted_opening=item_number in opening,
                    counted_closing=item_number in closing,
                )
            )

        return {
            "run": {
                **_run_payload(run),
                "baselineCode": baseline.code,
                "baselineLabel": baseline.label,
                "baselineCountDate": baseline.count_date.isoformat(),
                "campaignCode": campaign.code,
                "campaignCountDate": campaign.count_date.isoformat(),
            },
            "steps": self._steps(run, loaded, erp),
            "kpis": _kpis(lines),
            "chain": _chain(lines),
            "rows": [_line_payload(line) for line in lines],
        }

    def _steps(
        self,
        run: StockFlowRun,
        loaded: dict[FlowKind, dict[str, Decimal]],
        erp: dict[str, StockFlowErp],
    ) -> list[dict[str, Any]]:
        """What has been provided so far, step by step.

        The screen needs to distinguish three states, not two: not provided,
        provided and empty, provided with content. Only the scrap step can
        legitimately be « deliberately empty », and only because somebody said so.
        """
        return [
            {
                "kind": str(kind),
                "label": FLOW_LABELS[kind].capitalize(),
                "items": len(loaded[kind]),
                "totalQty": float(sum(loaded[kind].values())),
                "loaded": bool(loaded[kind]) or (
                    kind is FlowKind.SCRAP and run.scrap_loaded
                ),
                "optional": kind is FlowKind.SCRAP,
            }
            for kind in (FlowKind.RECEIPT, FlowKind.SHIPMENT, FlowKind.SCRAP)
        ] + [
            {
                "kind": "ERP",
                "label": "Production et consommation théorique",
                "items": len(erp),
                "totalQty": float(sum(e.produced_qty for e in erp.values())),
                "loaded": bool(erp),
                "optional": False,
            }
        ]

    # ---------------------------------------------------------------- helpers

    def _run(self, campaign: Campaign, run_id: str) -> StockFlowRun:
        run = self.ctx.stock_flow.get_run(run_id)
        if run is None or run.campaign_id != campaign.id:
            raise NotFoundError(
                "Cette comparaison n'existe pas, ou appartient à une autre "
                "campagne.",
                runId=run_id,
            )
        return run

    def _baseline(self, campaign: Campaign, baseline_id: str) -> Campaign:
        baseline = self.ctx.campaigns.get(baseline_id)
        if baseline.count_date >= campaign.count_date:
            raise ValidationError(
                f"« {baseline.code} » a été comptée le "
                f"{baseline.count_date:%d/%m/%Y}, soit après « {campaign.code} » "
                f"({campaign.count_date:%d/%m/%Y}). Le stock initial est celui de "
                "la campagne la plus ancienne par date d'inventaire.",
                baseline=baseline.code,
            )
        return baseline


# --------------------------------------------------------------------------- #
# Computation
# --------------------------------------------------------------------------- #

def _counted_by_item(ctx: ServiceContext, campaign_id: str) -> dict[str, Decimal]:
    """Counted stock of one campaign, collapsed to the article.

    Locations are collapsed on purpose: between two inventories a pallet moves,
    and comparing bin by bin would report every move as a variance. What the
    period's flows act on is the article's total.
    """
    totals: dict[str, Decimal] = {}
    for row in ctx.journals.counted_quantities(campaign_id):
        qty = row["qty"]
        value = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        totals[row["item_number"]] = totals.get(row["item_number"], ZERO) + value
    return {item: quantize_qty(value) for item, value in totals.items()}


def _kpis(lines: list[StockFlowLine]) -> dict[str, Any]:
    """Headline figures, computed over the articles both campaigns counted.

    The incomplete ones are counted and reported, never summed: an article
    missing from one of the two counts would contribute a variance the size of
    its whole stock, and a single such reference can dominate the total.
    """
    complete = [line for line in lines if line.is_complete]
    net = sum((line.variance_value for line in complete), ZERO)
    gross = sum((line.abs_variance_value for line in complete), ZERO)
    expected_value = sum(
        (quantize_money(line.expected_qty * line.unit_cost) for line in complete), ZERO
    )
    closing_value = sum(
        (quantize_money(line.closing_qty * line.unit_cost) for line in complete), ZERO
    )
    base = abs(expected_value)
    return {
        "lineCount": len(lines),
        "completeCount": len(complete),
        "incompleteCount": len(lines) - len(complete),
        "matchedCount": sum(1 for line in complete if line.variance_qty == 0),
        "expectedValue": float(expected_value),
        "closingValue": float(closing_value),
        "netVarianceValue": float(net),
        "grossVarianceValue": float(gross),
        # Same reading as the campaign's own reliability: offsets allowed for the
        # first, absolute errors for the second. `None` rather than 0 when there
        # is no base — « n/a » is the honest answer, 100 % is not.
        "netReliability": float(1 - abs(net) / base) if base else None,
        "grossReliability": float(1 - gross / base) if base else None,
    }


#: The chain, in the order the flows happen. Rendered as a waterfall by the
#: screen, which is the only shape in which six terms and a residual read as one
#: story rather than seven numbers.
#: Les libellés sont courts parce qu'ils sont écrits sous une barre de moins de
#: cent pixels : « Consommation théorique » y arrive tronqué, et un axe dont on
#: doit deviner les catégories ne se lit pas.
_CHAIN_STEPS = (
    ("opening", "Stock initial", "opening_qty", 1),
    ("received", "Réceptions", "received_qty", 1),
    ("produced", "Production", "produced_qty", 1),
    ("shipped", "Expéditions", "shipped_qty", -1),
    ("consumed", "Conso. théo.", "consumed_qty", -1),
    ("scrapped", "Rebuts", "scrapped_qty", -1),
)


def _chain(lines: list[StockFlowLine]) -> list[dict[str, Any]]:
    """The six terms, then the expected stock and what was actually counted."""
    complete = [line for line in lines if line.is_complete]

    def total(attribute: str, sign: int) -> tuple[Decimal, Decimal]:
        qty = sum((getattr(line, attribute) for line in complete), ZERO)
        value = sum(
            (quantize_money(getattr(line, attribute) * line.unit_cost)
             for line in complete),
            ZERO,
        )
        return quantize_qty(qty * sign), quantize_money(value * sign)

    out = []
    for key, label, attribute, sign in _CHAIN_STEPS:
        qty, value = total(attribute, sign)
        out.append({
            "key": key, "label": label, "qty": float(qty), "value": float(value),
            "sign": sign, "terminal": False,
        })

    expected_qty = sum((line.expected_qty for line in complete), ZERO)
    expected_value = sum(
        (quantize_money(line.expected_qty * line.unit_cost) for line in complete), ZERO
    )
    closing_qty = sum((line.closing_qty for line in complete), ZERO)
    closing_value = sum(
        (quantize_money(line.closing_qty * line.unit_cost) for line in complete), ZERO
    )
    out.append({
        "key": "expected", "label": "Stock attendu",
        "qty": float(quantize_qty(expected_qty)),
        "value": float(quantize_money(expected_value)),
        "sign": 0, "terminal": True,
    })
    out.append({
        "key": "closing", "label": "Stock compté",
        "qty": float(quantize_qty(closing_qty)),
        "value": float(quantize_money(closing_value)),
        "sign": 0, "terminal": True,
    })
    return out


def _line_payload(line: StockFlowLine) -> dict[str, Any]:
    ratio = line.variance_ratio
    return {
        "itemNumber": line.item_number,
        "name": line.name,
        "unit": line.unit,
        "unitCost": float(line.unit_cost),
        "openingQty": float(line.opening_qty),
        "receivedQty": float(line.received_qty),
        "producedQty": float(line.produced_qty),
        "shippedQty": float(line.shipped_qty),
        "consumedQty": float(line.consumed_qty),
        "scrappedQty": float(line.scrapped_qty),
        "expectedQty": float(line.expected_qty),
        "closingQty": float(line.closing_qty),
        "varianceQty": float(line.variance_qty),
        "varianceValue": float(line.variance_value),
        "varianceRatio": None if ratio is None else float(ratio),
        "countedOpening": line.counted_opening,
        "countedClosing": line.counted_closing,
        "complete": line.is_complete,
    }


def _run_payload(run: StockFlowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "campaignId": run.campaign_id,
        "baselineCampaignId": run.baseline_campaign_id,
        "periodStart": run.period_start.isoformat(),
        "periodEnd": run.period_end.isoformat(),
        "weeks": (run.period_end - run.period_start).days // 7,
        "scrapLoaded": run.scrap_loaded,
        "sourceLoadedAt": (
            run.source_loaded_at.isoformat() if run.source_loaded_at else None
        ),
        "erpRefreshedAt": (
            run.erp_refreshed_at.isoformat() if run.erp_refreshed_at else None
        ),
    }
