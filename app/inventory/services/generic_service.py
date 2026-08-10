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
from ..domain.printing import available_print_modes
from ..domain.workflow import assert_sheet_transition, derive_zone_status, passes_for
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ENGINE_VERSION, ServiceContext, utcnow
from .manager_service import Perimeter

log = logging.getLogger(__name__)

__all__ = ["GenericService"]

#: A stack of counting sheets fits in one scan; two hundred pages is somebody
#: feeding the whole campaign at once, which would blow the token budget long
#: before it finished.
_MAX_SCAN_PAGES = 40


class GenericService:
    """Use cases for the multi-zone GENERIQUE location."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    def list_zones(
        self, campaign: Campaign, *, perimeter: Perimeter | None = None
    ) -> list[dict[str, Any]]:
        """Zones with their derived status and per-pass progress.

        :param perimeter: when given, only the zones assigned to that manager are
            returned. Filtering here rather than in the browser is the point: a
            client-side filter would still ship every zone of the site to every
            workstation, which is unacceptable the moment a contractor counts one.
        """
        ctx = self.ctx
        zones = ctx.sheets.list_zones(campaign.id)
        if perimeter is not None:
            zones = [z for z in zones if perimeter.covers_zone(z.id)]
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
                passes_required=zone.passes,
                pending_arbitrations=pending.get(zone.id, 0),
            )
            out.append({
                **zone.model_dump(mode="json"),
                "status": str(status),
                "pendingArbitrations": pending.get(zone.id, 0),
                # Which of the three printable documents this zone can produce
                # right now. Derived server-side so the screen never re-implements
                # the matrix and drifts from what the endpoint will accept.
                "printModes": [
                    str(m)
                    for m in available_print_modes(
                        free_entry=zone.free_entry, status=campaign.status
                    )
                ],
                "sheets": [
                    {
                        **sheet.model_dump(mode="json"),
                        "lineCount": len(lines.get(sheet.id, [])),
                        "countedLines": sum(
                            1 for l in lines.get(sheet.id, []) if l.is_counted
                        ),
                        # What a second, multi-sheet scan must not overwrite
                        # without being told to.
                        "correctedLines": sum(
                            1 for l in lines.get(sheet.id, []) if l.was_ai_corrected
                        ),
                    }
                    for sheet in sorted(zone_sheets, key=lambda s: str(s.pass_no))
                ],
            })
        return out

    def get_sheet(self, campaign: Campaign, sheet_id: str) -> dict[str, Any]:
        """One sheet's content, ready for the grid.

        A pass-2 sheet also carries the pass-1 quantity of each line. Having it
        on screen is what turns encoding into a check — the encoder sees the
        disagreement as they type it, instead of meeting it later in a list of
        arbitrations detached from the paper. It is a screen-only column: the
        printed sheet must never show the first count, or the second one stops
        being independent.
        """
        ctx = self.ctx
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")
        items = ctx.referentials.items_by_number(campaign.id)
        lines = ctx.sheets.list_sheet_lines(sheet_id)
        pass_1 = self._pass_1_quantities(campaign, sheet)
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
                    "qtyPass1": pass_1.get((line.item_number, line.section)),
                }
                for line in lines
            ],
        }

    def _pass_1_quantities(
        self, campaign: Campaign, sheet: CountSheet
    ) -> dict[tuple[str, CountSection], float]:
        """Pass-1 quantities per (item, section), for a pass-2 sheet only."""
        if sheet.pass_no is not SheetPass.PASS_2:
            return {}
        ctx = self.ctx
        first = next(
            (
                s
                for s in ctx.sheets.list_sheets(campaign.id, zone_id=sheet.zone_id)
                if s.pass_no is SheetPass.PASS_1
            ),
            None,
        )
        if first is None:
            return {}
        totals: dict[tuple[str, CountSection], float] = {}
        for line in ctx.sheets.list_sheet_lines(first.id):
            if not line.is_counted:
                continue
            key = (line.item_number, line.section)
            # A sheet may list the same article twice (two pallets); the
            # comparison is against the zone's total, as the arbitration is.
            totals[key] = totals.get(key, 0.0) + float(line.qty)
        return totals

    # ----------------------------------------------------------------- zones

    def create_zone(
        self,
        campaign: Campaign,
        *,
        code: str,
        label: str = "",
        sector: str = "",
        display_order: int = 0,
        passes: int | None = None,
        free_entry: bool = True,
        manager_code: str = "",
    ) -> Zone:
        """Create a zone and its counting sheets.

        Allowed in both PREPARATION and COUNTING: preparation is precisely when
        one decides what to count, and a physical area nobody had listed is
        routinely discovered on the day of the inventory.

        :param free_entry: this endpoint creates a zone with **no** pre-printed
            article list, which is the definition of a free-entry sheet — the
            counter writes down what they find. Defaulting to ``True`` is what
            keeps the interface from presenting a deliberate blank sheet as an
            unprepared one. Loading a list through the ``count_sheets`` import
            clears the flag.
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
            passes=campaign.config.generic_passes if passes is None else passes,
            free_entry=free_entry,
            manager_code=manager_code,
        )
        if zone.code in existing:
            raise ConflictError(
                f"Une zone « {zone.code} » existe déjà dans cette campagne.",
                code=zone.code,
            )
        ctx.sheets.create_zone(zone, actor=ctx.actor)
        ctx.sheets.ensure_sheets(
            campaign.id, zone.id, passes_for(zone.passes), actor=ctx.actor,
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.CREATE,
            entity_type="zone",
            entity_id=zone.id,
            summary=f"Création de la zone {zone.code}",
            after=zone.model_dump(mode="json"),
        )
        # A zone is what unlocks the pilotage steps; the counts move with it.
        ctx.forget_progress(campaign.id)
        return zone

    def set_zone_passes(
        self, campaign: Campaign, zone_ids: Sequence[str], passes: int
    ) -> dict[str, Any]:
        """Set how many independent counts a selection of zones requires.

        Dropping to one count **deletes** the second sheet, so the operation is
        refused when that sheet already carries a quantity: bringing a zone back
        to a single count after the fact would erase a real count. The refusal
        names the zones concerned, because "some zone somewhere" is not
        actionable on inventory day.

        Raising back to two recreates the second sheet, empty.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        if passes not in (1, 2):
            raise ValidationError(
                "Le nombre de comptages doit être 1 ou 2.", passes=passes
            )
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)
        targets = [z for z in zone_ids if zones[z].passes != passes]
        if not targets:
            return {"updated": 0, "sheetsRemoved": 0, "sheetsCreated": 0}

        removed = created = 0
        if passes == 1:
            counted = ctx.sheets.zones_with_counted_pass(
                campaign.id, targets, SheetPass.PASS_2
            )
            if counted:
                codes = sorted(zones[z].code for z in counted)
                raise ConflictError(
                    "Impossible de ramener à un seul comptage : le comptage n°2 "
                    f"porte déjà des quantités saisies sur {', '.join(codes)}. "
                    "Effacez ces quantités si le second comptage doit être "
                    "abandonné.",
                    zones=codes,
                )

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, targets, actor=ctx.actor, passes=passes, conn=conn
            )
            if passes == 1:
                removed = ctx.sheets.delete_sheets_for_pass(
                    campaign.id, targets, SheetPass.PASS_2, conn=conn
                )
                ctx.sheets.delete_arbitrations(campaign.id, targets, conn=conn)
            else:
                for zone_id in targets:
                    created += ctx.sheets.ensure_sheets(
                        campaign.id, zone_id, passes_for(2),
                        actor=ctx.actor, conn=conn,
                    )
                    self._mirror_pass_1_lines(campaign, zone_id, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) passée(s) à {passes} comptage(s) "
                    f"({removed} feuille(s) supprimée(s), {created} créée(s))."
                ),
                after={
                    "passes": passes,
                    "zones": sorted(zones[z].code for z in targets),
                },
                conn=conn,
            )
        return {"updated": updated, "sheetsRemoved": removed, "sheetsCreated": created}

    def set_zone_negative(
        self, campaign: Campaign, zone_ids: Sequence[str], allowed: bool
    ) -> int:
        """Allow — or forbid again — negative counted quantities on a selection.

        Carried by the zone rather than the sheet: both passes of one area must
        obey the same rule, otherwise the arbitration compares two counts that
        were not allowed the same values.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, list(zone_ids), actor=ctx.actor,
                allow_negative=allowed, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) : quantités négatives "
                    f"{'autorisées' if allowed else 'interdites'}"
                ),
                after={
                    "allowNegative": allowed,
                    "zones": sorted(zones[z].code for z in zone_ids),
                },
                conn=conn,
            )
        return updated

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
        ctx.forget_progress(campaign.id)

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
        ctx.guard(campaign, "count_entries")
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
        ctx.guard(campaign, "count_entries")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == sheet.zone_id),
            None,
        )
        allow_negative = bool(zone and zone.allow_negative)

        existing = {l.id: l for l in ctx.sheets.list_sheet_lines(sheet_id)}
        lines: list[CountSheetLine] = []
        for order, row in enumerate(rows):
            line_id = str(row.get("id") or "") or new_id()
            previous = existing.get(line_id)
            qty = row.get("qty")
            if not allow_negative and qty not in (None, "") and Decimal(str(qty)) < 0:
                # One does not find minus twenty screws in a bin: a negative is
                # a typo until a human says otherwise, zone by zone. Catching it
                # at the keyboard costs a second; catching it at the variance
                # meeting costs an afternoon.
                raise ValidationError(
                    f"Quantité négative refusée sur « {row.get('item_number')} » : "
                    f"la zone {zone.code if zone else ''} n'autorise pas les "
                    "quantités négatives. Activez-les sur cette zone si la "
                    "feuille sert à corriger un comptage déjà posté.",
                    itemNumber=row.get("item_number"),
                    qty=str(qty),
                    zoneId=sheet.zone_id,
                )
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
        ctx.guard(campaign, "count_entries")
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
        and validates; nothing is posted automatically.

        A sheet with a pre-printed list is read *against* it: the model only has
        to find the handwritten quantity next to a known reference, and anything
        else it reads is provably a hallucination. A free-entry sheet has no such
        list by design, so the same guard is applied one step later — the model
        transcribes what it sees, and a reference the campaign's referential does
        not know is reported instead of created.
        """
        from ..ai import SheetExtractor, render_pdf_pages

        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == sheet.zone_id),
            None,
        )
        expected_lines = ctx.sheets.list_sheet_lines(sheet_id)
        free_entry = not expected_lines
        if free_entry and not (zone is not None and zone.free_entry):
            raise ValidationError(
                "Cette feuille n'a aucune ligne pré-imprimée et sa zone n'est "
                "pas déclarée en saisie libre. Chargez sa liste d'articles, ou "
                "passez la zone en saisie libre."
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

        common = {
            "campaign_id": campaign.id,
            "sheet_id": sheet_id,
            "zone_label": (zone.label or zone.code) if zone else sheet.zone_id,
            "pass_no": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
            "images": images,
            "image_mime": mime,
            "id_factory": new_id,
        }
        result = (
            extractor.extract_free_entry(known_items=items, **common)
            if free_entry
            else extractor.extract(
                expected=extractor.expected_from_items(expected_lines, items),
                **common,
            )
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

    def extract_from_multi_scan(
        self,
        campaign: Campaign,
        *,
        payload: bytes,
        filename: str,
        content_type: str,
        overwrite_reviewed: bool = False,
    ) -> dict[str, Any]:
        """Read a scan holding **several** counting sheets in one pass.

        The whole stack goes on the scanner and comes back as one PDF. Because
        the application printed those pages, every one carries its sheet's
        identifier in the footer: routing is reading that line, not guessing
        from content. A page whose footer cannot be read is reported, never
        attributed — a page filed under the wrong zone posts a count against
        stock that was never there.

        **Sheets a human has already corrected are skipped by default.** The
        expensive, irreplaceable work in this whole chain is somebody sitting
        down with the paper and fixing what the model misread; a second scan
        that silently overwrote it would destroy exactly that. Overwriting stays
        possible — it is an explicit choice, and the report names what it cost.
        """
        from ..ai import SheetCandidate, SheetExtractor, render_pdf_pages

        ctx = self.ctx
        ctx.guard(campaign, "count_entries")

        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            images = render_pdf_pages(payload, max_pages=_MAX_SCAN_PAGES)
            mime = "image/png"
        else:
            images = [payload]
            mime = content_type or "image/png"

        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        sheets = ctx.sheets.list_sheets(campaign.id)
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        items = ctx.referentials.items_by_number(campaign.id)

        # A sheet is readable either because it carries a pre-printed list, or
        # because its zone is declared free entry — in which case what the model
        # reads is checked against the article referential instead. A sheet that
        # is neither is left out: the model would have nothing to be wrong
        # against.
        candidates = [
            SheetCandidate(
                sheet_id=sheet.id,
                zone_code=zones[sheet.zone_id].code,
                pass_no=1 if sheet.pass_no is SheetPass.PASS_1 else 2,
            )
            for sheet in sheets
            if sheet.zone_id in zones
            and (lines_by_sheet.get(sheet.id) or zones[sheet.zone_id].free_entry)
        ]
        if not candidates:
            raise ValidationError(
                "Aucune feuille n'est lisible : elles n'ont ni liste d'articles "
                "pré-imprimée, ni zone déclarée en saisie libre."
            )

        extractor = SheetExtractor()
        routing = extractor.route_pages(
            images=images, candidates=candidates, image_mime=mime
        )

        by_id = {s.id: s for s in sheets}
        processed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for sheet_id, pages in routing.pages_by_sheet.items():
            sheet = by_id[sheet_id]
            zone = zones[sheet.zone_id]
            corrected = [
                l for l in lines_by_sheet.get(sheet_id, ()) if l.was_ai_corrected
            ]
            if corrected and not overwrite_reviewed:
                skipped.append({
                    "sheetId": sheet_id,
                    "zoneCode": zone.code,
                    "passNo": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
                    "pages": [p + 1 for p in pages],
                    "correctedLines": len(corrected),
                    "reason": (
                        f"{len(corrected)} ligne(s) lues par l'IA puis corrigées à "
                        "la main. Un nouveau scan les écraserait."
                    ),
                })
                continue

            expected_lines = lines_by_sheet.get(sheet_id, [])
            common = {
                "campaign_id": campaign.id,
                "sheet_id": sheet_id,
                "zone_label": zone.label or zone.code,
                "pass_no": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
                "images": [images[p] for p in pages],
                "image_mime": mime,
                "id_factory": new_id,
            }
            result = (
                extractor.extract_free_entry(known_items=items, **common)
                if not expected_lines
                else extractor.extract(
                    expected=extractor.expected_from_items(expected_lines, items),
                    **common,
                )
            )
            ctx.sheets.replace_sheet_lines(sheet_id, result.lines, actor=ctx.actor)
            ctx.sheets.update_sheet(
                sheet_id,
                status=SheetStatus.ENCODING,
                counter_name=result.counter_name or None,
                extraction_confidence=result.mean_confidence,
                actor=ctx.actor,
            )
            # The per-sheet report is spread *first*: it carries its own
            # ``pages`` key holding a count, and the page list is what the
            # screen renders. Spreading it last silently replaced the list with
            # an integer and crashed the report on ``pages.join``.
            processed.append({
                **result.as_report(),
                "sheetId": sheet_id,
                "zoneCode": zone.code,
                "passNo": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
                "pages": [p + 1 for p in pages],
                "overwroteCorrections": len(corrected),
            })

        report = {
            "pages": len(images),
            "sheetsProcessed": processed,
            "sheetsSkipped": skipped,
            "unroutedPages": routing.unrouted,
        }
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.IMPORT,
            entity_type="count_sheet",
            summary=(
                f"Scan multi-feuilles « {filename} » : {len(images)} page(s), "
                f"{len(processed)} feuille(s) lue(s), {len(skipped)} préservée(s), "
                f"{len(routing.unrouted)} page(s) non attribuée(s)."
            ),
            after=report,
        )
        return report

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
        # A single-pass zone has nothing to compare: there is no second opinion,
        # so producing arbitration lines would manufacture a decision nobody can
        # make and block the consolidation for ever.
        if zone_counts.passes_required < 2:
            return []
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
                "isProposed": line.is_proposed,
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
        ctx.guard(campaign, "count_entries")
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

    def prefill_with_pass_2(self, campaign: Campaign, zone_id: str) -> int:
        """Copy the pass-2 quantity into every open arbitration of a zone.

        A convenience, not a decision. It saves typing the same figure forty
        times; it does **not** say anybody looked at those forty lines. The
        values land in the fields the user is about to work through, and each one
        still has to be confirmed — the consolidation ignores a proposal until
        somebody validates it.

        Lines already decided are left alone: a bulk pre-fill must never quietly
        overwrite a judgement somebody made line by line.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        proposals: dict[str, Decimal] = {}
        for line in ctx.sheets.list_arbitrations(campaign.id, zone_id=zone_id):
            if line.is_resolved or line.qty_pass_1 == line.qty_pass_2:
                continue
            qty = line.qty_pass_2 if line.qty_pass_2 is not None else line.qty_pass_1
            if qty is None:
                continue
            proposals[line.id] = qty

        written = ctx.sheets.propose_arbitrations(
            campaign.id, proposals,
            comment="Pré-rempli avec le comptage n°2 — à valider.",
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="zone",
            entity_id=zone_id,
            summary=(
                f"{written} arbitrage(s) pré-rempli(s) avec le comptage n°2 "
                "(aucune validation)"
            ),
            after={"proposed": written},
        )
        return written

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
        ctx.guard(campaign, "count_entries")
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
        ctx.guard(campaign, "count_entries")
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

    def _mirror_pass_1_lines(
        self, campaign: Campaign, zone_id: str, *, conn: Any = None
    ) -> int:
        """Give a freshly recreated pass-2 sheet pass 1's article list, blank.

        Recreating the sheet alone would hand the second counter a blank page:
        the two counters must be asked about the same articles, or the
        arbitration compares a count against nothing. Quantities are of course
        not copied — that would not be a second count.
        """
        ctx = self.ctx
        sheets = {
            s.pass_no: s
            for s in ctx.sheets.list_sheets(campaign.id, zone_id=zone_id, conn=conn)
        }
        first, second = sheets.get(SheetPass.PASS_1), sheets.get(SheetPass.PASS_2)
        if first is None or second is None:
            return 0
        existing = {
            (l.item_number, l.section)
            for l in ctx.sheets.list_sheet_lines(second.id, conn=conn)
        }
        blanks = [
            CountSheetLine(
                id=new_id(),
                sheet_id=second.id,
                campaign_id=campaign.id,
                item_number=line.item_number,
                section=line.section,
                unit=line.unit,
                source=DataSource.SYSTEM,
                display_order=line.display_order,
            )
            for line in ctx.sheets.list_sheet_lines(first.id, conn=conn)
            if (line.item_number, line.section) not in existing
        ]
        if not blanks:
            return 0
        return ctx.sheets.upsert_sheet_lines(blanks, actor=ctx.actor, conn=conn)

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
