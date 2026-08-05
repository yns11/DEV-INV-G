"""The GENERIQUE location: zones, printed sheets, arbitration, consolidation.

This is the module that replaces ``Compil GENERIQUE.xlsx`` end to end — the
40 zone tabs, the five Power Query steps and the manual copy/paste into the ERP.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain.bom import BomIndex
from ..domain.consolidation import (
    ConsolidationInput,
    ConsolidationResult,
    ZoneCounts,
    build_arbitration_lines,
    consolidate_generic,
)
from ..domain.enums import (
    AuditAction,
    CountSection,
    DataSource,
    JournalStatus,
    SheetPass,
    SheetStatus,
)
from ..domain.models import (
    Campaign,
    CountJournalLine,
    CountSheet,
    CountSheetLine,
    Zone,
)
from ..domain.workflow import assert_sheet_transition, derive_zone_status
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ENGINE_VERSION, ServiceContext, utcnow

log = logging.getLogger(__name__)

__all__ = ["GenericService"]


class GenericService:
    """Use cases for the multi-zone GENERIQUE location."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    def list_zones(self, campaign: Campaign) -> list[dict[str, Any]]:
        """Zones with their derived status and per-pass progress."""
        ctx = self.ctx
        zones = ctx.sheets.list_zones(campaign.id)
        sheets = ctx.sheets.list_sheets(campaign.id)
        lines = ctx.sheets.lines_by_sheet(campaign.id)
        arbitrations = ctx.sheets.list_arbitrations(campaign.id)

        by_zone: dict[str, list[CountSheet]] = {}
        for sheet in sheets:
            by_zone.setdefault(sheet.zone_id, []).append(sheet)
        pending: dict[str, int] = {}
        for arb in arbitrations:
            if not arb.is_resolved and arb.qty_pass_1 != arb.qty_pass_2:
                pending[arb.zone_id] = pending.get(arb.zone_id, 0) + 1

        out: list[dict[str, Any]] = []
        for zone in zones:
            zone_sheets = by_zone.get(zone.id, [])
            status = derive_zone_status(
                zone_sheets,
                passes_required=campaign.config.generic_passes,
                pending_arbitrations=pending.get(zone.id, 0),
            )
            out.append({
                **zone.model_dump(mode="json"),
                "status": str(status),
                "pendingArbitrations": pending.get(zone.id, 0),
                "sheets": [
                    {
                        **sheet.model_dump(mode="json"),
                        "lineCount": len(lines.get(sheet.id, [])),
                        "countedLines": sum(
                            1 for l in lines.get(sheet.id, []) if l.is_counted
                        ),
                    }
                    for sheet in sorted(zone_sheets, key=lambda s: str(s.pass_no))
                ],
            })
        return out

    def get_sheet(self, campaign: Campaign, sheet_id: str) -> dict[str, Any]:
        ctx = self.ctx
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")
        items = ctx.referentials.items_by_number(campaign.id)
        lines = ctx.sheets.list_sheet_lines(sheet_id)
        return {
            "sheet": sheet.model_dump(mode="json"),
            "lines": [
                {
                    **line.model_dump(mode="json"),
                    "qty": float(line.qty) if line.is_counted else None,
                    "isCounted": line.is_counted,
                    "name": items[line.item_number].name
                    if line.item_number in items else "",
                    "known": line.item_number in items,
                }
                for line in lines
            ],
        }

    # ----------------------------------------------------------------- zones

    def create_zone(
        self,
        campaign: Campaign,
        *,
        code: str,
        label: str = "",
        sector: str = "",
        display_order: int = 0,
    ) -> Zone:
        """Create a zone and its counting sheets.

        Allowed in both PREPARATION and COUNTING: the specification explicitly
        keeps this open during counting, because a physical area nobody had
        listed is routinely discovered on the day.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        existing = {z.code for z in ctx.sheets.list_zones(campaign.id)}
        zone = Zone(
            id=new_id(),
            campaign_id=campaign.id,
            code=code,
            label=label,
            sector=sector,
            display_order=display_order,
        )
        if zone.code in existing:
            raise ConflictError(
                f"Une zone « {zone.code} » existe déjà dans cette campagne.",
                code=zone.code,
            )
        ctx.sheets.create_zone(zone, actor=ctx.actor)
        ctx.sheets.ensure_sheets(
            campaign.id, zone.id, _passes(campaign.config.generic_passes),
            actor=ctx.actor,
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.CREATE,
            entity_type="zone",
            entity_id=zone.id,
            summary=f"Création de la zone {zone.code}",
            after=zone.model_dump(mode="json"),
        )
        return zone

    def delete_zone(self, campaign: Campaign, zone_id: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        ctx.sheets.delete_zone(zone_id, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="zone",
            entity_id=zone_id,
            summary="Suppression logique d'une zone",
        )

    # ---------------------------------------------------------------- sheets

    def transition_sheet(
        self,
        campaign: Campaign,
        sheet_id: str,
        target: SheetStatus,
        *,
        counter_name: str | None = None,
    ) -> CountSheet:
        """Advance a sheet through PENDING → COUNTING → ENCODING → DONE."""
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        pass_1_status: SheetStatus | None = None
        if sheet.pass_no is SheetPass.PASS_2:
            siblings = ctx.sheets.list_sheets(campaign.id, zone_id=sheet.zone_id)
            first = next(
                (s for s in siblings if s.pass_no is SheetPass.PASS_1), None
            )
            pass_1_status = first.status if first else None

        assert_sheet_transition(sheet, target, pass_1_status=pass_1_status)

        started_at = utcnow() if target is SheetStatus.COUNTING else None
        ended_at = utcnow() if target is SheetStatus.ENCODING else None
        ctx.sheets.update_sheet(
            sheet_id,
            status=target,
            counter_name=counter_name,
            started_at=started_at,
            ended_at=ended_at,
            actor=ctx.actor,
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.STATUS_CHANGE,
            entity_type="count_sheet",
            entity_id=sheet_id,
            summary=f"Feuille {sheet.pass_no} : {sheet.status} → {target}",
            before={"status": str(sheet.status)},
            after={"status": str(target), "counterName": counter_name},
        )

        # Reaching DONE on the last pass is what makes an arbitration list
        # meaningful, so refresh it immediately rather than on the next page load.
        if target is SheetStatus.DONE:
            self.refresh_arbitrations(campaign, sheet.zone_id)
        return ctx.sheets.get_sheet(sheet_id)

    def upsert_sheet_lines(
        self,
        campaign: Campaign,
        sheet_id: str,
        rows: Sequence[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> int:
        """Create or update the lines of a sheet from grid edits or a paste."""
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        existing = {l.id: l for l in ctx.sheets.list_sheet_lines(sheet_id)}
        lines: list[CountSheetLine] = []
        for order, row in enumerate(rows):
            line_id = str(row.get("id") or "") or new_id()
            previous = existing.get(line_id)
            qty = row.get("qty")
            lines.append(
                CountSheetLine(
                    id=line_id,
                    sheet_id=sheet_id,
                    campaign_id=campaign.id,
                    item_number=str(row.get("item_number") or ""),
                    section=_section(row.get("section")),
                    # A value typed by a human always lands in qty_manual so the
                    # AI reading it replaced stays visible next to it.
                    qty_imported=previous.qty_imported if previous else None,
                    qty_manual=None if qty in (None, "") else Decimal(str(qty)),
                    unit=str(row.get("unit") or "PCE"),
                    source=DataSource.MANUAL,
                    confidence=previous.confidence if previous else None,
                    comment=str(row.get("comment") or ""),
                    display_order=int(row.get("display_order") or order),
                )
            )

        if replace:
            written = ctx.sheets.replace_sheet_lines(sheet_id, lines, actor=ctx.actor)
        else:
            written = ctx.sheets.upsert_sheet_lines(lines, actor=ctx.actor)

        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="count_sheet_line",
            entity_id=sheet_id,
            summary=f"{written} ligne(s) enregistrée(s) sur la feuille",
            after={"lines": written, "replace": replace},
        )
        return written

    def delete_sheet_line(self, campaign: Campaign, line_id: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        ctx.sheets.delete_sheet_line(line_id, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="count_sheet_line",
            entity_id=line_id,
            summary="Suppression logique d'une ligne de feuille",
        )

    # ---------------------------------------------------------- AI extraction

    def extract_from_scan(
        self,
        campaign: Campaign,
        sheet_id: str,
        *,
        payload: bytes,
        filename: str,
        content_type: str,
        storage_path: str | None = None,
    ) -> dict[str, Any]:
        """Read a scanned sheet with the vision model.

        The result lands in the grid as ``SCAN_AI`` values that a human reviews
        and validates; nothing is posted automatically. Sheets keep their
        pre-printed article list as the reference, which both improves accuracy
        and makes hallucinated references detectable.
        """
        from ..ai import SheetExtractor, render_pdf_pages

        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        expected_lines = ctx.sheets.list_sheet_lines(sheet_id)
        if not expected_lines:
            raise ValidationError(
                "Cette feuille n'a aucune ligne pré-imprimée. Créez d'abord sa "
                "liste d'articles (ou dupliquez-la d'une campagne précédente) "
                "avant d'importer un scan."
            )

        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == sheet.zone_id),
            None,
        )
        items = ctx.referentials.items_by_number(campaign.id)
        extractor = SheetExtractor()

        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            # Rasterised, not split: the endpoint accepts images only.
            images = render_pdf_pages(payload)
            mime = "image/png"
        else:
            images = [payload]
            mime = content_type or "image/png"

        result = extractor.extract(
            campaign_id=campaign.id,
            sheet_id=sheet_id,
            zone_label=(zone.label or zone.code) if zone else sheet.zone_id,
            pass_no=1 if sheet.pass_no is SheetPass.PASS_1 else 2,
            expected=extractor.expected_from_items(expected_lines, items),
            images=images,
            image_mime=mime,
            id_factory=new_id,
        )

        ctx.sheets.replace_sheet_lines(sheet_id, result.lines, actor=ctx.actor)
        ctx.sheets.update_sheet(
            sheet_id,
            status=SheetStatus.ENCODING,
            counter_name=result.counter_name or None,
            evidence_path=storage_path,
            extraction_confidence=result.mean_confidence,
            actor=ctx.actor,
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.IMPORT,
            entity_type="count_sheet",
            entity_id=sheet_id,
            summary=(
                f"Extraction IA du scan « {filename} » : {len(result.lines)} lignes, "
                f"confiance moyenne {result.mean_confidence or 0:.0%}."
            ),
            after=result.as_report(),
        )
        return {
            "report": result.as_report(),
            "sheet": ctx.sheets.get_sheet(sheet_id).model_dump(mode="json"),
        }

    # ----------------------------------------------------------- arbitration

    def refresh_arbitrations(
        self, campaign: Campaign, zone_id: str
    ) -> list[dict[str, Any]]:
        """Rebuild the pass-1 vs pass-2 comparison for a zone.

        Existing human decisions are preserved: recomputing the comparison must
        never erase an arbitration somebody already made.
        """
        ctx = self.ctx
        zone_counts = self._zone_counts(campaign, zone_id)
        lines = build_arbitration_lines(
            zone_counts, campaign_id=campaign.id, id_factory=new_id
        )
        if lines:
            ctx.sheets.upsert_arbitrations(lines)
        return self.list_arbitrations(campaign, zone_id)

    def list_arbitrations(
        self, campaign: Campaign, zone_id: str | None = None
    ) -> list[dict[str, Any]]:
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        tolerance = campaign.config.arbitration_tolerance
        out: list[dict[str, Any]] = []
        for line in ctx.sheets.list_arbitrations(campaign.id, zone_id=zone_id):
            q1, q2 = line.qty_pass_1, line.qty_pass_2
            divergent = q1 != q2
            if divergent and tolerance > 0 and q1 is not None and q2 is not None:
                base = max(abs(q1), abs(q2))
                divergent = base == 0 or abs(q2 - q1) / base > tolerance
            out.append({
                **line.model_dump(mode="json"),
                "name": items[line.item_number].name
                if line.item_number in items else "",
                "gap": float(line.gap),
                "divergent": divergent,
                "needsDecision": divergent and not line.is_resolved,
                "unitCost": float(items[line.item_number].std_price)
                if line.item_number in items else 0.0,
                "gapValue": float(
                    line.gap * items[line.item_number].std_price
                ) if line.item_number in items else 0.0,
            })
        out.sort(key=lambda r: (not r["needsDecision"], -abs(r["gapValue"])))
        return out

    def decide_arbitration(
        self,
        campaign: Campaign,
        arbitration_id: str,
        qty: Decimal,
        *,
        comment: str = "",
    ) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        if qty < 0:
            raise ValidationError("Une quantité arbitrée ne peut pas être négative.")
        ctx.sheets.decide_arbitration(
            arbitration_id, qty, actor=ctx.actor, comment=comment
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.ARBITRATE,
            entity_type="arbitration",
            entity_id=arbitration_id,
            summary=f"Arbitrage : quantité retenue {qty}",
            after={"qty": str(qty), "comment": comment},
        )

    def accept_pass_2(self, campaign: Campaign, zone_id: str) -> int:
        """Pre-fill every open arbitration of a zone with the pass-2 quantity.

        The specification asks for this one-click shortcut. It is a *decision*,
        not an automation: each line is stamped with the acting user and shows
        up in the audit trail exactly like a hand-typed arbitration.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        decided = 0
        for line in ctx.sheets.list_arbitrations(campaign.id, zone_id=zone_id):
            if line.is_resolved or line.qty_pass_1 == line.qty_pass_2:
                continue
            qty = line.qty_pass_2 if line.qty_pass_2 is not None else line.qty_pass_1
            if qty is None:
                continue
            ctx.sheets.decide_arbitration(
                line.id, qty, actor=ctx.actor,
                comment="Arbitrage groupé : comptage n°2 retenu.",
            )
            decided += 1
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.ARBITRATE,
            entity_type="zone",
            entity_id=zone_id,
            summary=f"{decided} arbitrage(s) résolu(s) par le comptage n°2",
            after={"decided": decided},
        )
        return decided

    # --------------------------------------------------------- consolidation

    def consolidate(
        self, campaign: Campaign, *, preview: bool = False
    ) -> ConsolidationResult:
        """Run the GENERIQUE consolidation.

        :param preview: include zones that are not finished yet. Used for the
            live view during counting; the posted run always requires every zone
            to be complete.
        """
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        bom_links = ctx.referentials.list_bom_links(campaign.id)
        excluded = {i.item_number for i in items.values() if i.excluded_from_bom}
        bom = BomIndex(
            bom_links,
            excluded_children=excluded,
            max_depth=campaign.config.max_bom_depth,
        )

        zones = ctx.sheets.list_zones(campaign.id)
        sheets = ctx.sheets.list_sheets(campaign.id)
        lines = ctx.sheets.lines_by_sheet(campaign.id)
        arbitrations = ctx.sheets.list_arbitrations(campaign.id)

        sheets_by_zone: dict[str, list[CountSheet]] = {}
        for sheet in sheets:
            sheets_by_zone.setdefault(sheet.zone_id, []).append(sheet)
        arb_by_zone: dict[str, list] = {}
        for arb in arbitrations:
            arb_by_zone.setdefault(arb.zone_id, []).append(arb)

        payload = ConsolidationInput(
            campaign_id=campaign.id,
            zones=[
                ZoneCounts(
                    zone=zone,
                    sheets=sheets_by_zone.get(zone.id, []),
                    lines_by_sheet=lines,
                    arbitrations=arb_by_zone.get(zone.id, []),
                )
                for zone in zones
            ],
            items=items,
            bom=bom,
            passes_required=campaign.config.generic_passes,
            arbitration_tolerance=campaign.config.arbitration_tolerance,
            require_done_zones=not preview,
        )
        return consolidate_generic(payload)

    def consolidate_and_save(self, campaign: Campaign) -> dict[str, Any]:
        """Consolidate, persist the run, and post it to the GENERIQUE journal.

        The generated lines land in the INVV journal of ``B06VRAC / GENERIQUE``
        with source ``CONSOLIDATION``. The journal is then ready to be exported
        and pasted into the ERP — or, once the ERP write-back is wired, posted
        directly.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        result = self.consolidate(campaign, preview=False)

        if result.blocking:
            raise ValidationError(
                f"{len(result.blocking)} point(s) bloquant(s) empêchent la "
                "consolidation.",
                findings=[f.model_dump(mode="json") for f in result.blocking],
            )
        if not result.lines:
            raise ValidationError(
                "La consolidation ne produit aucune ligne : aucune zone terminée."
            )

        run_id = ctx.consolidation.save_run(
            campaign_id=campaign.id,
            run_by=ctx.actor,
            engine_version=ENGINE_VERSION,
            zones_included=result.zones_included,
            zones_skipped=result.zones_skipped,
            findings=[f.model_dump(mode="json") for f in result.findings],
            lines=result.lines,
            breakdown=result.breakdown,
        )

        generic_key = campaign.config.generic_key
        journal = next(
            (j for j in ctx.journals.list(campaign.id) if j.key == generic_key), None
        )
        if journal is None:
            ctx.journals.ensure_journals(campaign.id, [generic_key], actor=ctx.actor)
            journal = next(
                j for j in ctx.journals.list(campaign.id) if j.key == generic_key
            )

        journal_lines = [
            CountJournalLine(
                id=new_id(),
                journal_id=journal.id,
                campaign_id=campaign.id,
                item_number=line.item_number,
                qty_manual=line.qty,
                unit=line.unit,
                source=DataSource.CONSOLIDATION,
                comment=f"Consolidation GENERIQUE ({len(line.zone_codes)} zone(s))",
            )
            for line in result.lines
        ]
        with ctx.db.transaction() as conn:
            ctx.journals.replace_lines_for_journal(
                journal.id, campaign.id, journal_lines, actor=ctx.actor, conn=conn
            )
            if journal.status is JournalStatus.PENDING:
                ctx.journals.set_status(
                    [journal.id], JournalStatus.IN_PROGRESS, actor=ctx.actor, conn=conn
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.CONSOLIDATE,
                entity_type="consolidation_run",
                entity_id=run_id,
                summary=(
                    f"Consolidation GENERIQUE : {len(result.lines)} article(s), "
                    f"{len(result.zones_included)} zone(s) incluse(s), "
                    f"{len(result.findings)} constat(s)."
                ),
                after={
                    "runId": run_id,
                    "lines": len(result.lines),
                    "zonesIncluded": result.zones_included,
                    "zonesSkipped": result.zones_skipped,
                    "journalId": journal.id,
                },
                conn=conn,
            )

        return {
            "runId": run_id,
            "journalId": journal.id,
            "lines": len(result.lines),
            "totalQty": float(result.total_qty),
            "zonesIncluded": result.zones_included,
            "zonesSkipped": result.zones_skipped,
            "findings": [f.model_dump(mode="json") for f in result.findings],
        }

    def wip_without_bom(self, campaign: Campaign) -> list[dict[str, Any]]:
        """WIP lines whose assembly has no bill of materials.

        These block the consolidation by design — exploding an assembly with no
        structure would silently destroy the counted quantity, which is exactly
        what the legacy inner join did. The BOM referential is frozen during
        counting, so the actionable remedy is on the counting side: reclassify
        the line as *WIP assemblé* and count the assembly as itself.

        This returns everything the UI needs to offer that in one click.
        """
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        bom = BomIndex(ctx.referentials.list_bom_links(campaign.id))
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        sheets = {s.id: s for s in ctx.sheets.list_sheets(campaign.id)}
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)

        out: list[dict[str, Any]] = []
        for sheet_id, lines in lines_by_sheet.items():
            sheet = sheets.get(sheet_id)
            if sheet is None:
                continue
            zone = zones.get(sheet.zone_id)
            for line in lines:
                if line.section is not CountSection.WIP or not line.is_counted:
                    continue
                if bom.has_bom(line.item_number):
                    continue
                item = items.get(line.item_number)
                out.append({
                    "lineId": line.id,
                    "sheetId": sheet_id,
                    "passNo": str(sheet.pass_no),
                    "zoneId": sheet.zone_id,
                    "zoneCode": zone.code if zone else "",
                    "itemNumber": line.item_number,
                    "name": item.name if item else "",
                    "itemType": str(item.item_type) if item else "UNKNOWN",
                    "qty": float(line.qty),
                    "unit": line.unit,
                    "knownItem": item is not None,
                })
        out.sort(key=lambda r: (r["zoneCode"], r["itemNumber"]))
        return out

    def reclassify_wip(
        self, campaign: Campaign, line_ids: Sequence[str], *, section: CountSection
    ) -> int:
        """Move counting-sheet lines to another section.

        The resolution path for :meth:`wip_without_bom`: an assembly with no
        structure is counted as itself rather than exploded. Recorded in the
        audit trail as an explicit human decision, because it changes what the
        posted journal will contain.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        if not line_ids:
            return 0

        by_sheet: dict[str, list[CountSheetLine]] = {}
        wanted = set(line_ids)
        for sheet_id, lines in ctx.sheets.lines_by_sheet(campaign.id).items():
            for line in lines:
                if line.id in wanted:
                    by_sheet.setdefault(sheet_id, []).append(
                        line.model_copy(update={"section": section})
                    )
        if not by_sheet:
            raise NotFoundError("Aucune ligne correspondante.", lineIds=list(line_ids))

        updated = 0
        with ctx.db.transaction() as conn:
            for lines in by_sheet.values():
                updated += ctx.sheets.upsert_sheet_lines(
                    lines, actor=ctx.actor, conn=conn
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="count_sheet_line",
                summary=(
                    f"{updated} ligne(s) reclassée(s) en section {section} "
                    "(assemblage sans nomenclature compté tel quel)."
                ),
                after={"section": str(section), "lineIds": list(line_ids)},
                conn=conn,
            )
        return updated

    def current_consolidation(self, campaign: Campaign) -> dict[str, Any]:
        """The stored consolidation, with its WIP drill-down."""
        ctx = self.ctx
        run = ctx.consolidation.current_run(campaign.id)
        if run is None:
            return {"run": None, "lines": [], "breakdown": []}
        items = ctx.referentials.items_by_number(campaign.id)
        lines = ctx.consolidation.current_lines(campaign.id)
        return {
            "run": {**run, "id": str(run["id"])},
            "lines": [
                {
                    **line.model_dump(mode="json"),
                    "name": items[line.item_number].name
                    if line.item_number in items else "",
                    "value": float(
                        line.qty * items[line.item_number].std_price
                    ) if line.item_number in items else 0.0,
                    "hasWip": line.has_wip,
                }
                for line in lines
            ],
            "breakdown": ctx.consolidation.wip_breakdown(campaign.id),
        }

    def wip_breakdown(self, campaign: Campaign, child_item: str) -> list[dict[str, Any]]:
        """Answer "what is this component's WIP quantity made of?"."""
        return self.ctx.consolidation.wip_breakdown(campaign.id, child_item=child_item)

    # --------------------------------------------------------------- helpers

    def _zone_counts(self, campaign: Campaign, zone_id: str) -> ZoneCounts:
        ctx = self.ctx
        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == zone_id), None
        )
        if zone is None:
            raise NotFoundError("Zone introuvable.", zoneId=zone_id)
        return ZoneCounts(
            zone=zone,
            sheets=ctx.sheets.list_sheets(campaign.id, zone_id=zone_id),
            lines_by_sheet=ctx.sheets.lines_by_sheet(campaign.id),
            arbitrations=ctx.sheets.list_arbitrations(campaign.id, zone_id=zone_id),
        )


def _passes(count: int) -> list[SheetPass]:
    order = [SheetPass.PASS_1, SheetPass.PASS_2]
    return order[: max(1, min(count, 2))]


def _section(value: Any) -> CountSection:
    from ..domain.enums import legacy_section_alias

    if value in (None, ""):
        return CountSection.LINE_SIDE
    text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    if text in CountSection.__members__:
        return CountSection[text]
    resolved = legacy_section_alias(str(value))
    if resolved is None:
        raise ValidationError(
            f"Section inconnue : {value!r}. Attendu LINE_SIDE, WIP ou WIP_OK.",
            section=str(value),
        )
    return resolved
