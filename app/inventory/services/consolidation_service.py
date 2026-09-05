"""Ce qui a été compté, référence par référence.

Des dizaines de zones comptées sur papier, chacune avec ses feuilles et
parfois deux passages, aboutissent à une ligne par référence : la quantité
retenue, d'où elle vient, et ce que l'ERP en disait.

C'est l'étape où le comptage cesse d'être un ensemble de relevés pour devenir
un chiffre opposable, et deux choses s'y décident :

**La quantité retenue.** Deux passages qui s'accordent donnent leur valeur ;
deux passages qui divergent attendent un arbitrage, et la consolidation refuse
de trancher à leur place.

**Ce qu'on fait des en-cours.** Un WIP sans nomenclature ne peut pas être
éclaté en composants ; il est signalé plutôt qu'ignoré, et se reclasse à la
main.

Extrait de ``GenericService`` : la consolidation lit les feuilles, elle ne les
écrit pas. Elle est le seul endroit du service à parler d'ERP, de
nomenclatures et de valeur — trois vocabulaires que les zones et les feuilles
n'emploient jamais.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain.bom import BomIndex
from ..domain.consolidation import (
    ConsolidationInput,
    ConsolidationResult,
    ZoneCounts,
    consolidate_generic,
)
from ..domain.controls import group_findings
from ..domain.enums import (
    AuditAction,
    CountSection,
    DataSource,
    JournalStatus,
)
from ..domain.models import (
    Campaign,
    ConsolidatedLine,
    CountJournalLine,
    CountSheet,
    CountSheetLine,
)
from ..domain.quantities import ZERO
from ..errors import (
    NotFoundError,
    ValidationError,
)
from .arbitration_service import refresh_after_sheet_writes
from .context import ENGINE_VERSION, ServiceContext


class ConsolidationService:
    """La consolidation d’une campagne."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def consolidate(
        self, campaign: Campaign, *, preview: bool = False, provisional: bool = False
    ) -> ConsolidationResult:
        """Run the GENERIQUE consolidation.

        :param preview: include zones that are not finished yet. Used for the
            live view during counting; the posted run always requires every zone
            to be complete.
        :param provisional: resolve a pending arbitration with the best reading
            available instead of refusing to. Only for the live variance — the
            posted run must never guess which of two counts is right.
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
            book_stock=self._generic_book_stock(campaign),
            arbitration_tolerance=campaign.config.arbitration_tolerance,
            require_done_zones=not preview,
            provisional=provisional,
        )
        return consolidate_generic(payload)

    def _generic_book_stock(self, campaign: Campaign) -> dict[str, Decimal]:
        """What the ERP says is in GENERIQUE, per article.

        Only that location: the journal being built covers it and nothing else,
        so stock sitting in a picking bin elsewhere is not something this count
        can settle at zero.
        """
        warehouse = campaign.config.generic_warehouse
        location = campaign.config.generic_location
        totals: dict[str, Decimal] = {}
        for line in self.ctx.book_stock.list(campaign.id):
            if line.warehouse_id != warehouse or line.location_id != location:
                continue
            totals[line.item_number] = totals.get(line.item_number, ZERO) + line.qty
        return totals

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

        generic_key = campaign.config.generic_key
        journal = next(
            (j for j in ctx.journals.list(campaign.id) if j.key == generic_key), None
        )
        if journal is None:
            # La consolidation ne crée plus ce journal. Un journal apparu tout
            # seul dans la liste est un journal dont personne n'a décidé le
            # périmètre ni le gestionnaire, et qui se retrouve à compter pour la
            # couverture alors qu'il n'a jamais été ouvert. Il vient de l'import
            # ERP, comme les autres, ou d'une création explicite.
            raise ValidationError(
                f"Aucun journal de comptage n'existe pour {generic_key.warehouse_id} / "
                f"{generic_key.location_id}. Créez-le depuis « Journaux de "
                "comptage » — import ERP ou création manuelle — puis relancez la "
                "consolidation.",
                warehouseId=generic_key.warehouse_id,
                locationId=generic_key.location_id,
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
        # Le calcul est enregistré *dans* la transaction qui poste le journal.
        # Séparés, ils laissaient une campagne dont la consolidation courante
        # existe et dont le journal correspondant est vide — le refus « aucun
        # journal GENERIQUE » se déclenchant après l'enregistrement du calcul.
        with ctx.db.transaction() as conn:
            run_id = ctx.consolidation.save_run(
                campaign_id=campaign.id,
                run_by=ctx.actor,
                engine_version=ENGINE_VERSION,
                zones_included=result.zones_included,
                zones_skipped=result.zones_skipped,
                findings=[f.model_dump(mode="json") for f in result.findings],
                lines=result.lines,
                breakdown=result.breakdown,
                conn=conn,
            )
            ctx.journals.replace_lines_for_journal(
                journal.id, campaign.id, journal_lines, actor=ctx.actor, conn=conn
            )
            if journal.status is JournalStatus.PENDING:
                ctx.journals.set_status(
                    campaign.id, [journal.id], JournalStatus.IN_PROGRESS,
                    actor=ctx.actor, conn=conn,
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
                # Le constat porte sur une quantité qu'on ne saura pas
                # éclater. Zéro s'éclate en zéro : l'annoncer remplirait la
                # page d'alertes sur des lignes dont rien n'est perdu.
                if line.section is not CountSection.WIP or not line.qty:
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
        # Un reclassement change la **section** des lignes, donc les clés sur
        # lesquelles les deux passages se comparent : l'ancienne paire disparaît
        # et une nouvelle apparaît. Sans recalcul, la zone gardait un arbitrage
        # portant sur une section qu'elle ne contient plus.
        refresh_after_sheet_writes(ctx, campaign, list(by_sheet))
        return updated

    def line_payload(
        self, line: ConsolidatedLine, items: dict[str, Any]
    ) -> dict[str, Any]:
        """One consolidation line, as the screens read it.

        The four quantities are floats, not the strings ``Decimal`` serialises
        to. JavaScript adds strings by gluing them together, so a column that
        displayed correctly line by line totalled to ``NaN`` the moment the
        composition bar summed it — the sort of fault that only shows up on the
        aggregate, which is exactly where nobody thinks to check.
        """
        known = line.item_number in items
        return {
            **line.model_dump(mode="json"),
            "qty": float(line.qty),
            "qty_line_side": float(line.qty_line_side),
            "qty_wip_ok": float(line.qty_wip_ok),
            "qty_wip_exploded": float(line.qty_wip_exploded),
            "name": items[line.item_number].name if known else "",
            "value": float(
                line.qty * items[line.item_number].std_price
            ) if known else 0.0,
            "hasWip": line.has_wip,
        }

    def preview_consolidation(self, campaign: Campaign) -> dict[str, Any]:
        """La consolidation telle qu'elle serait, zones inachevées comprises.

        C'est la vue vivante pendant le comptage : ce que le journal GENERIQUE
        contiendrait maintenant, et quelles zones manquent encore. Rien n'est
        enregistré.

        Le rendu est ici et non dans la route parce qu'il a besoin du
        référentiel : la route allait le chercher à travers ``service.ctx``,
        c'est-à-dire en contournant le service pour atteindre ses dépôts. Une
        route qui sait faire cela sait tout faire, et la couche ne veut plus
        rien dire.
        """
        result = self.consolidate(campaign, preview=True)
        items = self.ctx.referentials.items_by_number(campaign.id)
        return {
            "lines": [self.line_payload(line, items) for line in result.lines],
            "totalQty": float(result.total_qty),
            "zonesIncluded": result.zones_included,
            "zonesSkipped": result.zones_skipped,
            "findings": [f.model_dump(mode="json") for f in result.findings],
            "groups": [g.to_summary() for g in group_findings(result.findings)],
            "blocking": len(result.blocking),
        }

    def current_consolidation(self, campaign: Campaign) -> dict[str, Any]:
        """The stored consolidation, with its WIP drill-down."""
        ctx = self.ctx
        run = ctx.consolidation.current_run(campaign.id)
        if run is None:
            return {"run": None, "lines": [], "breakdown": []}
        items = ctx.referentials.items_by_number(campaign.id)
        return {
            "run": {**run, "id": str(run["id"])},
            "lines": [
                self.line_payload(line, items)
                for line in ctx.consolidation.current_lines(campaign.id)
            ],
            "breakdown": ctx.consolidation.wip_breakdown(campaign.id),
        }

    def wip_breakdown(self, campaign: Campaign, child_item: str) -> list[dict[str, Any]]:
        """Answer "what is this component's WIP quantity made of?"."""
        return self.ctx.consolidation.wip_breakdown(campaign.id, child_item=child_item)
