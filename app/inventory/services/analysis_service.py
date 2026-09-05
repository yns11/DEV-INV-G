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
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pandas as pd

from ..db import new_id
from ..domain.consolidation import ZoneCounts, resolve_zone_quantities
from ..domain.controls import (
    check_book_stock,
    check_items,
    check_referentials,
    check_stock_import,
    check_variances,
    check_zones,
    group_findings,
    summarise,
)
from ..domain.enums import (
    AuditAction,
    CountSection,
    ItemType,
    JournalStatus,
)
from ..domain.models import (
    AdjustmentLine,
    Campaign,
    VarianceAnalysis,
    VarianceLine,
    Zone,
)
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


#: Le nom des sections tel que la décomposition l'affiche. Les mêmes mots que
#: l'écran, sans quoi la fenêtre qui explique un chiffre parlerait une autre
#: langue que la colonne qui le porte.
_SECTION_LABELS = {
    "LINE_SIDE": "Bord de ligne",
    "WIP_OK": "WIP assemblé",
    "WIP": "WIP (à éclater)",
}

__all__ = ["AnalysisService"]

#: Dimensions the analysis screens may group by.
DIMENSIONS = ("item", "warehouse", "location", "item_type", "category", "program")

#: How a movement names itself in a breakdown. The enum value would do, but
#: « Ajustement ADJUSTMENT » is not a sentence anybody wrote on purpose.
ADJUSTMENT_ORIGINS = {
    "COUNT": "Mouvement de comptage",
    "ADJUSTMENT": "Ajustement saisi",
    "RECOUNT": "Recomptage",
    "OTHER": "Autre mouvement",
}


class AnalysisService:
    """Reconciliation and analysis of a campaign."""

    def __init__(self, ctx: ServiceContext) -> None:
        from .insight_service import InsightService

        self.ctx = ctx
        self._variance_cache: dict[tuple[str, str], list[VarianceLine]] = {}
        #: Ce qu'on montre au modèle, et ce qu'il en propose. Composition, pas
        #: héritage : les chiffres restent ici, le dossier envoyé est là-bas.
        self.insights = InsightService(ctx, self)

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
        counted = self._with_live_generic(campaign, counted)
        lines = build_variances(
            campaign=campaign,
            book_stock=ctx.book_stock.list(campaign.id),
            counted=counted,
            items=ctx.referentials.items_by_number(campaign.id),
            locations=ctx.referentials.locations_by_key(campaign.id),
            adjustments=ctx.adjustments.list(campaign.id),
            backflush=ctx.backflush.by_item(campaign.id),
            granularity=granularity,
        )
        self._variance_cache[key] = lines
        return lines

    def _with_live_generic(
        self, campaign: Campaign, counted: list[CountedQty]
    ) -> list[CountedQty]:
        """Replace the GENERIQUE counts by what the sheets say right now.

        A quantity written on a GENERIQUE sheet reached the variance only after
        somebody ran the consolidation, so the figure on screen lagged behind
        the counting by hours — and a team that had just finished a zone saw no
        movement at all. The consolidation is cheap and pure, so it runs on
        every read instead: the sheets are the source, the variance follows.

        Once the journal is **posted** the provisional view stands down. Posting
        is what the ERP will be adjusted by; recomputing over it would let a late
        sheet edit silently contradict a figure somebody has already signed off.
        """
        warehouse = campaign.config.generic_warehouse
        location = campaign.config.generic_location
        ctx = self.ctx

        generic_key = campaign.config.generic_key
        journal = next(
            (j for j in ctx.journals.list(campaign.id) if j.key == generic_key), None
        )
        if journal is not None and journal.status is JournalStatus.POSTED:
            return counted

        from .consolidation_service import ConsolidationService

        result = ConsolidationService(ctx).consolidate(campaign, preview=True, provisional=True)
        kept = [
            c for c in counted
            if not (c.warehouse_id == warehouse and c.location_id == location)
        ]
        kept.extend(
            CountedQty(
                item_number=line.item_number,
                warehouse_id=warehouse,
                location_id=location,
                qty=line.qty,
            )
            for line in result.lines
        )
        return kept

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
                "physicalQty": float(line.physical_qty),
                "physicalValue": float(line.physical_value),
                "adjustedValue": float(line.adjusted_value),
                "countedVarianceQty": float(line.counted_variance_qty),
                "countedVarianceValue": float(line.counted_variance_value),
                "backflushQty": float(line.backflush_qty),
                "backflushShareQty": float(line.backflush_share_qty),
                "backflushShareValue": float(line.backflush_share_value),
                "unexplainedQty": float(line.unexplained_qty),
                "unexplainedValue": float(line.unexplained_value),
                "explanationRate": (
                    None if (rate := line.explanation_rate) is None else float(rate)
                ),
                "backflushMeasured": line.backflush_measured,
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

    def transfers(
        self, campaign: Campaign, *, limit: int = 100
    ) -> dict[str, Any]:
        """How much of the site's variance is a move between bins, not a loss.

        The per-location view answers "which bin do I go and recount?", and it
        is the one the IRA is built on — so a pallet moved from one bin to
        another shows up twice, once short and once over, and drags the
        indicator down. That is a *location* accuracy problem, not a stock one.

        The honest financial question is the per-reference one: what did the
        site actually lose or gain, offsets allowed? This method measures the
        gap between the two readings:

        * ``netValue``      Σ |variance| per **reference** — the real exposure;
        * ``grossValue``    Σ |variance| per reference **and location**;
        * ``transferValue`` the difference, i.e. the part that cancels out
          between two locations of the same reference.

        A high transfer share means the count is disagreeing with the ERP about
        *where* the stock is, not about how much of it there is. Worth fixing,
        but not the same alarm — which is exactly why the per-reference view is
        the one the analysis screen opens on.
        """
        by_item = {
            line.item_number: line
            for line in self.variances(campaign, granularity="item")
        }
        gross_by_item: dict[str, float] = {}
        locations_by_item: dict[str, int] = {}
        for line in self.variances(campaign, granularity="item_location"):
            if line.variance_value == 0:
                continue
            gross_by_item[line.item_number] = gross_by_item.get(
                line.item_number, 0.0
            ) + abs(float(line.variance_value))
            locations_by_item[line.item_number] = (
                locations_by_item.get(line.item_number, 0) + 1
            )

        items = self.ctx.referentials.items_by_number(campaign.id)
        rows: list[dict[str, Any]] = []
        net_total = gross_total = 0.0
        for item_number, gross in gross_by_item.items():
            line = by_item.get(item_number)
            net = abs(float(line.variance_value)) if line else 0.0
            transfer = max(0.0, gross - net)
            net_total += net
            gross_total += gross
            if transfer <= 0:
                continue
            item = items.get(item_number)
            rows.append({
                "itemNumber": item_number,
                "name": item.name if item else "",
                "netValue": round(net, 2),
                "grossValue": round(gross, 2),
                "transferValue": round(transfer, 2),
                "transferShare": round(transfer / gross, 4) if gross else 0.0,
                "locations": locations_by_item.get(item_number, 0),
            })
        rows.sort(key=lambda r: -r["transferValue"])
        transfer_total = max(0.0, gross_total - net_total)
        return {
            "netValue": round(net_total, 2),
            "grossValue": round(gross_total, 2),
            "transferValue": round(transfer_total, 2),
            "transferShare": (
                round(transfer_total / gross_total, 4) if gross_total else 0.0
            ),
            "itemCount": len(rows),
            "rows": rows[:limit],
        }

    def pareto(
        self, campaign: Campaign, *, coverage: float = 0.8
    ) -> list[dict[str, Any]]:
        groups = aggregate_by(
            self.variances(campaign, granularity="item"), "item", campaign=campaign
        )
        return [_group_payload(g) for g in pareto(groups, coverage=Decimal(str(coverage)))]

    # ---------------------------------------------------------------- controls

    # ------------------------------------------------------------- backflush

    def backflush(self, campaign: Campaign) -> dict[str, Any]:
        """The backflush view: one line per article, and what it explains.

        Sorted by unexplained value rather than by backflush variance. A large
        backflush that the count confirms is *good news* — it was measured and it
        matched; what deserves the top of the list is what nobody can account
        for. Sorting on the raw variance would put the best-understood articles
        first and bury the problems.

        Articles the campaign excludes are left out, per the guide: an article
        removed from the referential's scope has no inventory variance to explain,
        so a backflush figure attached to it would be an orphan.
        """
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        lines = ctx.backflush.list(campaign.id)
        period = ctx.backflush.period(campaign.id) or {}
        variances = {
            line.item_number: line
            for line in self.variances(campaign, granularity="item")
        }

        rows: list[dict[str, Any]] = []
        for line in lines:
            item = items.get(line.item_number)
            if item is not None and item.excluded_everywhere:
                continue
            variance = variances.get(line.item_number)
            unit_cost = float(item.std_price) if item else 0.0
            share = float(line.inventory_share_qty)
            row = {
                "itemNumber": line.item_number,
                "name": item.name if item else "",
                "itemType": str(item.item_type) if item else "UNKNOWN",
                "category": item.category if item else "",
                "program": item.program if item else "",
                "unit": line.unit,
                "unitCost": unit_cost,
                "netQty": float(line.net_qty),
                "underConsumedQty": float(line.under_consumed_qty),
                "overConsumedQty": float(line.over_consumed_qty),
                "theoreticalQty": float(line.theoretical_qty),
                "actualQty": float(line.actual_qty),
                "parentCount": line.parent_count,
                "weekCount": line.week_count,
                "backflushShareQty": share,
                "backflushShareValue": share * unit_cost,
                "typeEcart": _backflush_label(line.net_qty),
                # The inventory half is only there once the article has been
                # counted. Reported as null rather than zero: « not compared »
                # and « compared, and it agrees » are different answers.
                "varianceQty": None,
                "varianceValue": None,
                "unexplainedQty": None,
                "unexplainedValue": None,
                "explanationRate": None,
                "compared": False,
            }
            if variance is not None:
                rate = variance.explanation_rate
                row.update({
                    "varianceQty": float(variance.variance_qty),
                    "varianceValue": float(variance.variance_value),
                    "unexplainedQty": float(variance.unexplained_qty),
                    "unexplainedValue": float(variance.unexplained_value),
                    "explanationRate": None if rate is None else float(rate),
                    "compared": True,
                })
            rows.append(row)

        rows.sort(key=lambda r: abs(r["unexplainedValue"] or 0.0), reverse=True)
        return {
            "period": _period_payload(period),
            "kpis": self.kpis(campaign).as_dict(),
            "rows": rows,
        }

    def suggested_backflush_period(self, campaign: Campaign) -> dict[str, str]:
        """A period the screen can propose, computed from the campaign dates.

        The previous campaign is the one with the closest earlier *count* date —
        never the closest earlier creation date. Two campaigns created in one
        order and counted in the other exist, and it is the count that bounds the
        period production ran over.
        """
        from .import_service import suggested_period

        earlier = [
            other for other in self.ctx.campaigns.list()
            if other.id != campaign.id and other.count_date < campaign.count_date
        ]
        previous = max(earlier, key=lambda c: c.count_date).count_date if earlier else None
        start, end = suggested_period(campaign.count_date, previous=previous)
        return {"periodStart": start.isoformat(), "periodEnd": end.isoformat()}

    def controls(self, campaign: Campaign) -> dict[str, Any]:
        """Every control applicable to the campaign's current data."""
        findings = self._all_findings(campaign)
        return {
            "summary": summarise(findings),
            # One entry per control, in reading order; the screen opens a group
            # by filtering `findings` on its code.
            "groups": [g.to_summary() for g in group_findings(findings)],
            "findings": [f.model_dump(mode="json") for f in findings],
        }

    def _all_findings(self, campaign: Campaign) -> list[Any]:
        """The control run itself, shared by the screen and by the badge.

        Two callers, one computation: a badge announcing a number the screen
        then contradicts is worse than no badge.
        """
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        bom_links = ctx.referentials.list_bom_links(campaign.id)
        findings = check_referentials(items=items, bom_links=bom_links)
        findings += check_items(items=items)

        # Ce que le dernier chargement de stock a écarté. Lu du rapport du lot
        # et non des lignes chargées : ce sont précisément les lignes qui n'y
        # sont pas. Hors de la garde `if book_stock`, pour la même raison — un
        # chargement dont *toutes* les lignes ont été écartées ne laisse rien
        # derrière lui, et c'est le cas où le constat compte le plus.
        latest = {
            row["target"]: row for row in ctx.imports.latest_per_target(campaign.id)
        }
        findings += check_stock_import(
            report=(latest.get("book_stock") or {}).get("report")
        )

        zones = ctx.sheets.list_zones(campaign.id)
        if zones:
            findings += check_zones(
                zones=zones,
                sheets=ctx.sheets.list_sheets(campaign.id),
                lines_by_sheet=ctx.sheets.lines_by_sheet(campaign.id),
                # Le référentiel est déjà chargé ici : c'est lui qui dit qu'un
                # article compté sur une feuille est hors périmètre, et donc que
                # sa quantité n'entrera dans aucun écart.
                items=items,
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
        return findings

    # ------------------------------------------------------------ breakdowns

    #: The figures a screen can ask "where does this come from?" about.
    BREAKDOWN_ASPECTS = (
        "book", "counted", "physical", "line_side", "wip_ok", "wip", "variance",
        "generic",
    )

    def breakdown(
        self,
        campaign: Campaign,
        item_number: str,
        aspect: str,
        *,
        warehouse_id: str = "",
        location_id: str = "",
    ) -> dict[str, Any]:
        """Where one figure comes from, in one shape whatever the figure is.

        The WIP column was explorable and the others were not, so a quantity one
        could not explain was a quantity one could only believe. Every column now
        answers the same question the same way — origin, place, detail, quantity,
        value — which is also what lets a single dialog serve all of them instead
        of six that would drift apart.

        Totals are computed from the rows returned, not fetched separately: a
        drill-down whose total disagrees with its own lines is worse than none.
        """
        if aspect not in self.BREAKDOWN_ASPECTS:
            raise ValidationError(
                f"Décomposition inconnue : {aspect!r}.",
                allowed=list(self.BREAKDOWN_ASPECTS),
            )
        item_number = item_number.strip().upper()
        ctx = self.ctx
        item = ctx.referentials.items_by_number(campaign.id).get(item_number)
        if item is None:
            raise NotFoundError(
                f"{item_number} est absent du référentiel de cette campagne.",
                itemNumber=item_number,
            )
        unit_cost = float(item.std_price)

        rows = {
            "book": self._book_rows,
            "counted": self._counted_rows,
            "line_side": lambda c, i: self._sheet_rows(c, i, "LINE_SIDE"),
            "wip_ok": lambda c, i: self._sheet_rows(c, i, "WIP_OK"),
            "wip": self._wip_rows,
            "generic": self._generic_rows,
            "variance": self._variance_rows_for,
            "physical": self._physical_rows,
        }[aspect](campaign, item_number)

        if warehouse_id:
            rows = [r for r in rows if r.get("warehouseId", warehouse_id) == warehouse_id]
        if location_id:
            rows = [r for r in rows if r.get("locationId", location_id) == location_id]

        for row in rows:
            row.setdefault("value", row["qty"] * unit_cost)
        # Les lignes nulles ne sont pas montrées. La décomposition répond à
        # « d'où vient ce chiffre ? », et une ligne à zéro n'en vient pas : sur
        # une référence listée dans quarante zones et trouvée dans deux, elle
        # noie les deux qui expliquent le total sous trente-huit qui ne
        # l'expliquent pas. Écartées **après** le calcul de la valeur et
        # **avant** les totaux, pour que le total reste la somme de ce qui est
        # affiché — une fenêtre dont le total contredit ses propres lignes est
        # pire que pas de fenêtre.
        rows = [r for r in rows if r["qty"] or r["value"]]
        return {
            "itemNumber": item_number,
            "name": item.name,
            "aspect": aspect,
            "unit": item.unit,
            "unitCost": unit_cost,
            "total": sum(r["qty"] for r in rows),
            "totalValue": sum(r["value"] for r in rows),
            "rows": rows,
        }

    def _book_rows(self, campaign: Campaign, item_number: str) -> list[dict[str, Any]]:
        return [
            {
                "origin": "Stock ERP",
                "where": f"{line.warehouse_id} / {line.location_id}",
                "warehouseId": line.warehouse_id,
                "locationId": line.location_id,
                "detail": "",
                "qty": float(line.qty),
                "value": float(line.value),
            }
            for line in self.ctx.book_stock.list(campaign.id)
            if line.item_number == item_number
        ]

    def _counted_rows(self, campaign: Campaign, item_number: str) -> list[dict[str, Any]]:
        """One row per journal, and the GENERIQUE one split by zone.

        GENERIQUE is a single ERP location covering dozens of physical areas, so
        "counted 1 240 in GENERIQUE" explains nothing on its own. Its share is
        broken down by the zones that produced it — which is the only form in
        which somebody can go and check.
        """
        ctx = self.ctx
        generic = campaign.config.generic_key
        out: list[dict[str, Any]] = []
        for row in ctx.journals.counted_quantities(campaign.id):
            if row["item_number"] != item_number:
                continue
            key = (row["warehouse_id"], row["location_id"])
            if key == (generic.warehouse_id, generic.location_id):
                continue
            out.append({
                "origin": "Journal de comptage",
                "where": f"{row['warehouse_id']} / {row['location_id']}",
                "warehouseId": row["warehouse_id"],
                "locationId": row["location_id"],
                "detail": "",
                "qty": float(row["qty"]),
            })

        from .consolidation_service import ConsolidationService

        result = ConsolidationService(ctx).consolidate(
            campaign, preview=True, provisional=True
        )
        line = next(
            (l for l in result.lines if l.item_number == item_number), None
        )
        if line is not None:
            for label, qty_part in (
                ("Bord de ligne", line.qty_line_side),
                ("WIP assemblé", line.qty_wip_ok),
                ("WIP éclaté en composants", line.qty_wip_exploded),
            ):
                if qty_part == 0:
                    continue
                out.append({
                    "origin": label,
                    "where": f"{generic.warehouse_id} / {generic.location_id}",
                    "warehouseId": generic.warehouse_id,
                    "locationId": generic.location_id,
                    "detail": ", ".join(line.zone_codes[:8]),
                    "qty": float(qty_part),
                })
        return out

    def _retained_by_zone(
        self, campaign: Campaign
    ) -> list[tuple[Zone, dict[tuple[str, CountSection], Decimal], set[tuple[str, str]]]]:
        """Ce que chaque zone retient, zone par zone — et ce qui reste à trancher.

        **La décomposition doit dire la même chose que le journal**, sinon elle
        n'explique rien : elle listait une ligne par feuille, donc deux fois la
        même quantité sur une zone à double comptage, et elle affichait les deux
        chiffres bruts là où la consolidation, elle, n'en retient qu'un — celui
        sur lequel les deux passages s'accordent, ou celui qu'un arbitrage a
        tranché. Un total de 60 050 se décomposait ainsi en deux lignes de
        60 050.

        C'est donc la **même fonction** que la consolidation qui répond ici.
        ``provisional`` pour qu'une zone dont l'arbitrage traîne montre quand
        même son chiffre le plus probable plutôt qu'un trou ; les clés encore
        ouvertes sont renvoyées à part, et la fenêtre le dit.
        """
        ctx = self.ctx
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        sheets = ctx.sheets.list_sheets(campaign.id)
        out = []
        for zone in ctx.sheets.list_zones(campaign.id):
            counts = ZoneCounts(
                zone=zone,
                sheets=[s for s in sheets if s.zone_id == zone.id],
                lines_by_sheet=lines_by_sheet,
            )
            retained, findings = resolve_zone_quantities(
                counts,
                arbitration_tolerance=campaign.config.arbitration_tolerance,
                provisional=True,
            )
            pending = {
                (f.item_number, str(f.context.get("section", "")))
                for f in findings
                if f.code == "ARBITRATION_PENDING" and f.item_number
            }
            out.append((zone, retained, pending))
        return out

    def _sheet_rows(
        self, campaign: Campaign, item_number: str, section: str
    ) -> list[dict[str, Any]]:
        """Ce que chaque zone GENERIQUE apporte à un total de section."""
        generic = campaign.config.generic_key
        wanted = CountSection(section)
        out: list[dict[str, Any]] = []
        for zone, retained, pending in self._retained_by_zone(campaign):
            qty = retained.get((item_number, wanted))
            if qty is None:
                continue
            unresolved = (item_number, section) in pending
            out.append({
                "origin": zone.label or zone.code,
                "where": f"{generic.warehouse_id} / {generic.location_id}",
                "warehouseId": generic.warehouse_id,
                "locationId": generic.location_id,
                "detail": (
                    f"zone {zone.code}"
                    + (" · arbitrage en attente" if unresolved else "")
                ),
                "qty": float(qty),
            })
        return out

    def _generic_rows(
        self, campaign: Campaign, item_number: str
    ) -> list[dict[str, Any]]:
        """La part GENERIQUE d'une référence, et **elle seule**.

        Le total d'une ligne du journal consolidé ouvrait la décomposition du
        stock compté de toute la campagne : les treize pièces d'un autre
        emplacement s'y affichaient à côté des soixante mille de GENERIQUE,
        alors que le journal consolidé, lui, ne les compte pas — et à raison.
        Une fenêtre qui explique un chiffre par des quantités qui n'y sont pas
        est pire qu'aucune fenêtre.

        Les deux règles que la consolidation applique le sont ici aussi, sans
        quoi le total afficherait des lignes que le journal écarte : un produit
        fini n'entre que par la porte du WIP, et un article exclu du périmètre
        GENERIQUE sort après l'éclatement.
        """
        items = self.ctx.referentials.items_by_number(campaign.id)
        item = items.get(item_number)
        if item is not None and item.excluded_from_generic:
            return []
        finished = item is not None and item.item_type is ItemType.FINISHED
        rows: list[dict[str, Any]] = []
        if not finished:
            for section in ("LINE_SIDE", "WIP_OK"):
                label = _SECTION_LABELS[section]
                for row in self._sheet_rows(campaign, item_number, section):
                    rows.append({**row, "detail": f"{row['detail']} · {label}"})
        rows.extend(self._wip_rows(campaign, item_number))
        return rows

    def _wip_rows(self, campaign: Campaign, item_number: str) -> list[dict[str, Any]]:
        generic = campaign.config.generic_key
        return [
            {
                "origin": str(row.get("parent_item", "")),
                "where": f"{generic.warehouse_id} / {generic.location_id}",
                "warehouseId": generic.warehouse_id,
                "locationId": generic.location_id,
                "detail": (
                    f"zone {row.get('zone_code', '')} · "
                    f"{row.get('parent_qty', '')} × {row.get('qty_per', '')}"
                ),
                "qty": float(row.get("child_qty", 0) or 0),
            }
            for row in self.ctx.consolidation.wip_breakdown(
                campaign.id, child_item=item_number
            )
        ]

    def _variance_rows_for(
        self, campaign: Campaign, item_number: str
    ) -> list[dict[str, Any]]:
        """The gap, place by place — where the money actually went."""
        return [
            {
                "origin": "Écart",
                "where": f"{line.warehouse_id} / {line.location_id}",
                "warehouseId": line.warehouse_id,
                "locationId": line.location_id,
                "detail": (
                    f"physique {_plain(line.physical_qty)} − ERP "
                    f"{_plain(line.book_qty)}"
                    + (f" (dont ajust. {_plain(line.adjusted_qty)})"
                       if line.adjusted_qty else "")
                ),
                "qty": float(line.variance_qty),
                "value": float(line.variance_value),
            }
            for line in self.variances(campaign, granularity="item_location")
            if line.item_number == item_number and line.variance_qty != 0
        ]

    def _physical_rows(
        self, campaign: Campaign, item_number: str
    ) -> list[dict[str, Any]]:
        """The physical stock: what was counted, then what moved afterwards.

        Replaces the former « résiduel » decomposition, which subtracted the
        adjustments from the variance. They are added to the *count* now, because
        an adjustment is a real movement — so this reads as one column of stock
        rather than as a correction applied to a gap.
        """
        out = self._counted_rows(campaign, item_number)
        for adjustment in self.ctx.adjustments.list(campaign.id):
            if adjustment.item_number != item_number:
                continue
            out.append({
                "origin": ADJUSTMENT_ORIGINS.get(
                    str(adjustment.kind), "Ajustement"
                ),
                "where": f"{adjustment.warehouse_id} / {adjustment.location_id}",
                "warehouseId": adjustment.warehouse_id,
                "locationId": adjustment.location_id,
                "detail": adjustment.journal_number or adjustment.comment,
                # Signé, et repris tel quel : un mouvement négatif retire du
                # stock physique, un positif en ajoute.
                "qty": float(adjustment.qty),
                "value": float(adjustment.value),
            })
        return out

    def alert_counts(self, campaign: Campaign) -> dict[str, int]:
        """One number per screen that carries a badge.

        Each is a count of *distinct* controls, which is what a badge can
        usefully carry: it answers "is there something new here?" and stays
        readable, where a raw occurrence count reads as noise the moment one
        control fires on four hundred articles.
        """
        from .consolidation_service import ConsolidationService

        controls = len(group_findings(self._all_findings(campaign)))
        consolidation = 0
        if self.ctx.sheets.list_zones(campaign.id):
            result = ConsolidationService(self.ctx).consolidate(campaign, preview=True)
            consolidation = len(group_findings(result.findings))
        return {"controls": controls, "consolidation": consolidation}

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
                # Toute la population, pas les cinq cents premiers. Le segment AZ
                # — forte valeur, faible fiabilité — est celui qu'on vient
                # chercher, et il n'a aucune raison de tomber dans les premières
                # lignes d'un classement fait sur la valeur.
                "items": _records(segmentation.frame),
            },
            "pareto": _records(pareto_frontier(frame)),
            "anomalies": {
                "method": anomalies.method,
                "contamination": anomalies.contamination,
                "features": anomalies.feature_names,
                "flagged": _records(
                    anomalies.frame[anomalies.frame["is_anomaly"]]
                    .sort_values("anomaly_score", ascending=False)
                ),
            },
            "clusters": {
                "n": clusters.n_clusters,
                "silhouette": clusters.silhouette,
                "profiles": _records(clusters.profiles),
                # Les articles avec leur profil : un graphique de profils sans
                # la liste de ce qu'il y a dedans se regarde et ne se travaille
                # pas. C'est la liste qu'on emporte en réunion.
                "items": _records(
                    clusters.frame[[
                        c for c in (
                            "item_number", "warehouse_id", "location_id", "cluster",
                            "item_type", "category", "program", "book_value",
                            "variance_value", "abs_variance_value", "variance_ratio",
                        )
                        if c in clusters.frame.columns
                    ]]
                ) if clusters.n_clusters > 0 else [],
            },
            # 500 et non 50 : la liste sert à décider où envoyer les équipes,
            # et une équipe qui a fini ses cinquante lignes doit trouver la
            # suite ici plutôt que de redemander l'analyse.
            "recountPriority": _records(recount_priority(anomalies.frame, top_n=500)),
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
    #
    # Trois façades d'une ligne. Ce sont les noms que l'API et les contrôles
    # connaissent, et la dernière fois qu'un découpage en a oublié une, tout
    # chargement de fichier a répondu 500 en production pendant deux jours.

    def suggest_causes(self, campaign: Campaign, *, max_items: int = 40) -> int:
        return self.insights.suggest_causes(campaign, max_items=max_items)

    def narrative(self, campaign: Campaign) -> str:
        return self.insights.narrative(campaign)

    def explain(self, campaign: Campaign, item_number: str) -> dict[str, Any]:
        return self.insights.explain(campaign, item_number)

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

    def causes(self) -> list[Any]:
        """Le référentiel des causes standard, commun à toutes les campagnes."""
        return self.ctx.analysis.list_causes()

    def adjustments(self, campaign: Campaign, *, limit: int = 1000) -> list[Any]:
        """Les mouvements et ajustements saisis sur la campagne."""
        return self.ctx.adjustments.list(campaign.id, limit=limit)

    def upsert_adjustments(
        self, campaign: Campaign, rows: Sequence[Any]
    ) -> dict[str, int]:
        """Enregistrer des ajustements saisis à la main.

        ``source="MANUAL"`` est posé ici et nulle part ailleurs : c'est ce qui
        distingue une ligne tapée par quelqu'un d'une ligne venue d'un
        chargement, et laisser l'appelant le choisir reviendrait à permettre à
        une saisie de se faire passer pour une lecture ERP.

        Un identifiant absent en fait une création : la grille édite et crée
        dans la même vue, et exiger deux appels obligerait l'écran à savoir
        lesquelles de ses lignes existent déjà.
        """
        ctx = self.ctx
        ctx.guard(campaign, "adjustments")
        lines = [
            AdjustmentLine(
                id=row.id or new_id(),
                campaign_id=campaign.id,
                item_number=row.item_number,
                warehouse_id=row.warehouse_id,
                location_id=row.location_id,
                kind=row.kind,
                qty=row.qty,
                unit=row.unit,
                value=row.value,
                journal_number=row.journal_number,
                physical_date=row.physical_date,
                reason_code=row.reason_code,
                comment=row.comment,
                source="MANUAL",
            )
            for row in rows
        ]
        written = ctx.adjustments.upsert(lines, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="adjustment_line",
            summary=f"{len(lines)} ajustement(s) enregistré(s) manuellement",
            after={"count": len(lines)},
        )
        return {"written": written}

    def delete_adjustment(self, campaign: Campaign, line_id: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "adjustments")
        ctx.adjustments.delete(campaign.id, line_id, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="adjustment_line",
            entity_id=line_id,
            summary="Suppression logique d'un ajustement",
        )

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

#: The guide's 0.5-unit threshold. Below it the two consumptions agree as far as
#: anybody can tell, and labelling a rounding difference « surconsommation »
#: would put a page of noise in front of the cases that matter.
_BACKFLUSH_TOLERANCE = Decimal("0.5")


def _backflush_label(net: Decimal) -> str:
    """« Non-consommation » / « Surconsommation » / « Conforme »."""
    if net > _BACKFLUSH_TOLERANCE:
        return "Non-consommation"
    if net < -_BACKFLUSH_TOLERANCE:
        return "Surconsommation"
    return "Conforme"



def _period_payload(period: dict[str, Any]) -> dict[str, Any] | None:
    """The period header, or ``None`` when nothing has been read yet.

    Both timestamps are carried: the freshness of the gold table at read time,
    and the instant of the read. Either alone is not enough to replay a figure —
    the first says which version of the source was seen, the second when.
    """
    if not period or not period.get("period_start"):
        return None

    def iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "periodStart": iso(period["period_start"]),
        "periodEnd": iso(period["period_end"]),
        "weeks": (period["period_end"] - period["period_start"]).days // 7,
        "sourceLoadedAt": iso(period.get("source_loaded_at")),
        "refreshedAt": iso(period.get("refreshed_at")),
        "items": int(period.get("items") or 0),
    }


def _plain(value: Any) -> str:
    """A quantity written as somebody would say it.

    ``Decimal`` renders its scale — ``40.000000`` for forty screws — and a
    breakdown that reads "ERP 40.000000 − compté 0.000000" makes the reader
    work out what it is looking at before it can read it.
    """
    number = float(value)
    return f"{number:,.0f}".replace(",", " ") if number == int(number) else (
        f"{number:,.3f}".replace(",", " ").rstrip("0").rstrip(".")
    )


def _group_payload(group: VarianceSet) -> dict[str, Any]:
    return {
        "key": group.key,
        "bookQty": float(group.book_qty),
        "bookValue": float(group.book_value),
        "varianceQty": float(group.variance_qty),
        "varianceValue": float(group.variance_value),
        "absVarianceQty": float(group.abs_variance_qty),
        "absVarianceValue": float(group.abs_variance_value),
        "countedVarianceValue": float(group.counted_variance_value),
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
