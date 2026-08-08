"""Generation of the campaign's downloadable artefacts."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.enums import AuditAction, SheetPass
from ..domain.models import Campaign, CountSheetLine, Item
from ..domain.printing import PrintMode, print_refusal
from ..errors import NotFoundError, ValidationError
from ..reporting.exports import (
    build_counting_sheet_pdf,
    build_journal_export,
    build_workbook,
)
from .analysis_service import AnalysisService
from .context import ENGINE_VERSION, ServiceContext, utcnow

log = logging.getLogger(__name__)

__all__ = ["ReportService"]


class ReportService:
    """Builds the files a campaign produces."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------- printable sheets

    def counting_sheet_pdf(
        self,
        campaign: Campaign,
        sheet_id: str,
        *,
        mode: PrintMode = PrintMode.LIST,
        with_sources: bool = False,
        blank_lines: int = 0,
    ) -> tuple[bytes, str]:
        """Render one printable counting sheet in one of its three modes.

        Printing is available from the very first phase: paper is prepared
        *before* the count, which is exactly when the sheets are needed. What
        the phase decides is not whether one can print but *what* — the record
        with quantities does not exist until something has been counted. That
        rule, and the free-entry one, live in :mod:`inventory.domain.printing`.
        """
        ctx = self.ctx
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")
        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == sheet.zone_id),
            None,
        )
        if zone is None:
            raise NotFoundError("Zone introuvable.")

        refusal = print_refusal(
            mode, free_entry=zone.free_entry, status=campaign.status
        )
        if refusal:
            raise ValidationError(refusal, zone=zone.code, mode=str(mode))

        blank_lines = _validated_blank_lines(blank_lines, mode=mode)
        items = ctx.referentials.items_by_number(campaign.id)
        lines = (
            []
            if mode is PrintMode.BLANK
            else _printable_lines(ctx.sheets.list_sheet_lines(sheet_id), items)
        )
        if mode is not PrintMode.BLANK and not lines:
            # A free-entry sheet nobody has typed into yet is the common case
            # here, and telling its owner to "load an article list" would be
            # advice against the whole point of the zone.
            raise ValidationError(
                "Rien n'a encore été saisi sur cette feuille : le relevé serait "
                "vide. Imprimez-la vierge pour la faire remplir."
                if zone.free_entry else
                "Cette feuille ne porte aucune ligne. Chargez sa liste d'articles."
            )

        pass_no = 1 if sheet.pass_no is SheetPass.PASS_1 else 2
        payload = build_counting_sheet_pdf(
            campaign_label=f"{campaign.code} — {campaign.label}",
            campaign_code=campaign.code,
            count_date=campaign.count_date,
            zone_code=zone.code,
            zone_label=zone.label or zone.code,
            pass_no=pass_no,
            lines=lines,
            sheet_id=sheet.id,
            mode=mode,
            with_sources=with_sources,
            blank_lines=blank_lines,
        )
        filename = (
            f"feuille-comptage-{_MODE_SLUGS[mode]}_{_slug(zone.code)}_"
            f"passage-{pass_no}_{campaign.code}.pdf"
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="count_sheet",
            entity_id=sheet_id,
            summary=(
                f"Impression {_MODE_LABELS[mode]} — {zone.code}, "
                f"passage n°{pass_no}"
            ),
        )
        return payload, filename

    def all_counting_sheets_pdf(
        self,
        campaign: Campaign,
        *,
        pass_no: int = 1,
        mode: PrintMode = PrintMode.LIST,
        with_sources: bool = False,
        blank_lines: int = 0,
    ) -> tuple[bytes, str]:
        """One PDF containing every printable zone's sheet for a given pass.

        This is what gets printed on the eve of the inventory — one job, one
        stack of paper, in zone order — and, once counted, what gets filed.

        The batch only holds the zones the mode actually applies to: a free-entry
        zone has no list to print without quantities, and a listed zone has no
        business getting a blank grid. Silently mixing the two would hand out a
        stack in which half the sheets ask the counter to rewrite a list the
        application already has.
        """
        ctx = self.ctx
        target_pass = SheetPass.PASS_1 if pass_no == 1 else SheetPass.PASS_2
        blank_lines = _validated_blank_lines(blank_lines, mode=mode)
        zones = ctx.sheets.list_zones(campaign.id)
        sheets = {
            (s.zone_id, s.pass_no): s
            for s in ctx.sheets.list_sheets(campaign.id)
        }
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        items = ctx.referentials.items_by_number(campaign.id)

        documents: list[bytes] = []
        refusals: list[str] = []
        for zone in zones:
            sheet = sheets.get((zone.id, target_pass))
            if sheet is None:
                continue
            refusal = print_refusal(
                mode, free_entry=zone.free_entry, status=campaign.status
            )
            if refusal:
                refusals.append(refusal)
                continue
            lines = (
                []
                if mode is PrintMode.BLANK
                else _printable_lines(lines_by_sheet.get(sheet.id, []), items)
            )
            if mode is not PrintMode.BLANK and not lines:
                continue
            documents.append(build_counting_sheet_pdf(
                campaign_label=f"{campaign.code} — {campaign.label}",
                campaign_code=campaign.code,
                count_date=campaign.count_date,
                zone_code=zone.code,
                zone_label=zone.label or zone.code,
                pass_no=pass_no,
                lines=lines,
                sheet_id=sheet.id,
                mode=mode,
                with_sources=with_sources,
                blank_lines=blank_lines,
            ))

        if not documents:
            # When every zone was turned away for the same reason, that reason
            # is the answer — far more useful than "nothing to print".
            raise ValidationError(
                refusals[0]
                if refusals
                else f"Aucune feuille à imprimer {_MODE_LABELS[mode]} pour ce passage."
            )

        merged = _merge_pdfs(documents)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="count_sheet",
            summary=(
                f"Impression groupée de {len(documents)} feuille(s) "
                f"{_MODE_LABELS[mode]} — passage n°{pass_no}"
            ),
        )
        return merged, (
            f"feuilles-comptage-{_MODE_SLUGS[mode]}_"
            f"passage-{pass_no}_{campaign.code}.pdf"
        )

    # -------------------------------------------------------- journal export

    def journal_export(self, campaign: Campaign, journal_id: str) -> tuple[bytes, str]:
        """Export one counting journal in the ERP import format."""
        ctx = self.ctx
        journal = ctx.journals.get(journal_id)
        if journal.campaign_id != campaign.id:
            raise NotFoundError("Journal introuvable dans cette campagne.")
        lines = [
            {"item_number": l.item_number, "qty": l.qty, "unit": l.unit}
            for l in ctx.journals.list_lines(journal_id)
        ]
        payload = build_journal_export(
            lines,
            campaign_code=campaign.code,
            warehouse_id=journal.warehouse_id,
            location_id=journal.location_id,
            journal_kind=str(journal.kind),
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="count_journal",
            entity_id=journal_id,
            summary=f"Export ERP du journal {journal.key} ({len(lines)} lignes)",
        )
        filename = (
            f"journal_{str(journal.kind).lower()}_{_slug(journal.warehouse_id)}_"
            f"{_slug(journal.location_id)}_{campaign.code}.xlsx"
        )
        return payload, filename

    # --------------------------------------------------------- full campaign

    def campaign_workbook(self, campaign: Campaign) -> tuple[bytes, str]:
        """One workbook containing the whole campaign dossier.

        Replaces ``BILAN INVENTAIRE.xlsx`` as a *deliverable* — but as a
        read-only snapshot produced by the application, not a live file people
        edit and re-derive numbers from.
        """
        ctx = self.ctx
        analysis = AnalysisService(ctx)
        items = ctx.referentials.items_by_number(campaign.id)
        kpis = analysis.kpis(campaign)
        controls = analysis.controls(campaign)

        sheets: dict[str, tuple[list[str], list[list[Any]]]] = {}

        # -- KPI block ---------------------------------------------------------
        kpi_rows = [[label, value] for label, value in _kpi_rows(kpis)]
        sheets["Indicateurs"] = (["Indicateur", "Valeur"], kpi_rows)

        # -- variances by item -------------------------------------------------
        variance_rows = [
            [
                v["itemNumber"], v["name"], v["itemType"], v["category"], v["program"],
                v["unit"], v["unitCost"], v["bookQty"], v["bookValue"],
                v["countedQty"], v["varianceQty"], v["varianceValue"],
                v["adjustedQty"], v["residualQty"], v["residualValue"],
                v["finalQty"], v["causeCode"], v["comment"],
                "oui" if v["isMaterial"] else "non",
            ]
            for v in analysis.top_variances(campaign, limit=100_000)
        ]
        sheets["Écarts par article"] = (
            [
                "Article", "Désignation", "Type", "Catégorie", "Programme", "Unité",
                "Coût unitaire €", "Stock livre qté", "Stock livre valeur €",
                "Compté qté", "Écart qté", "Écart valeur €", "Ajusté qté",
                "Résiduel qté", "Résiduel valeur €", "Stock après qté",
                "Cause", "Commentaire", "Matériel",
            ],
            variance_rows,
        )

        # -- variances by location --------------------------------------------
        sheets["Écarts par emplacement"] = (
            ["Emplacement", "Stock livre valeur €", "Écart valeur €",
             "Écart absolu €", "Lignes", "Lignes matérielles"],
            [
                [g["key"], g["bookValue"], g["varianceValue"],
                 g["absVarianceValue"], g["lineCount"], g["materialCount"]]
                for g in analysis.aggregate(campaign, "location", limit=100_000)
            ],
        )
        sheets["Écarts par entrepôt"] = (
            ["Entrepôt", "Stock livre valeur €", "Écart valeur €",
             "Écart absolu €", "Lignes"],
            [
                [g["key"], g["bookValue"], g["varianceValue"],
                 g["absVarianceValue"], g["lineCount"]]
                for g in analysis.aggregate(campaign, "warehouse", limit=10_000)
            ],
        )

        # -- referentials ------------------------------------------------------
        # The dossier is an archive, not a summary: an écart is only auditable
        # against the referential that produced it, and that referential is
        # frozen per campaign. Shipping the analysis without it would force the
        # reader back into the ERP — exactly the round-trip this file removes.
        sheets["Articles"] = (
            ["Article", "Désignation", "Nom de recherche", "Groupe", "Cycle de vie",
             "Type", "Catégorie", "Programme", "Spécificité", "Unité",
             "Prix standard €", "Exclusions"],
            [
                [i.item_number, i.name, i.search_name, i.item_group,
                 i.lifecycle_state, str(i.item_type), i.category, i.program,
                 str(i.commonality), i.unit, float(i.std_price),
                 ", ".join(str(e) for e in i.exclusions)]
                for i in ctx.referentials.list_items(campaign.id)
            ],
        )
        sheets["Nomenclatures"] = (
            ["Composé", "Désignation composé", "Composant",
             "Désignation composant", "Quantité par"],
            [
                [l.parent_item,
                 items[l.parent_item].name if l.parent_item in items else "",
                 l.child_item,
                 items[l.child_item].name if l.child_item in items else "",
                 float(l.qty_per)]
                for l in ctx.referentials.list_bom_links(campaign.id)
            ],
        )
        sheets["Emplacements"] = (
            ["Entrepôt", "Emplacement", "Type", "Statut", "Zone", "Origine"],
            [
                [loc.warehouse_id, loc.location_id, str(loc.type),
                 str(loc.status), loc.zone, str(loc.source)]
                for loc in ctx.referentials.list_locations(campaign.id)
            ],
        )
        sheets["Seuils"] = (
            ["Type d'article", "Valeur absolue €", "Écart relatif qté",
             "Plancher qté", "Tolérance IRA"],
            [
                [str(t.item_type), float(t.value_abs_eur), float(t.qty_relative),
                 float(t.qty_abs_floor), float(t.ira_tolerance)]
                for t in campaign.thresholds
            ],
        )

        # -- book stock snapshot ----------------------------------------------
        sheets["Stock livre"] = (
            ["Article", "Désignation", "Entrepôt", "Emplacement", "Quantité",
             "Unité", "Coût unitaire €", "Valeur €"],
            [
                [b.item_number,
                 items[b.item_number].name if b.item_number in items else "",
                 b.warehouse_id, b.location_id, float(b.qty), b.unit,
                 float(b.unit_cost), float(b.value)]
                for b in ctx.book_stock.list(campaign.id)
            ],
        )

        # -- journals ----------------------------------------------------------
        journals = ctx.journals.list(campaign.id)
        journal_lines = ctx.journals.lines_by_journal(campaign.id)
        sheets["Journaux de comptage"] = (
            ["Entrepôt", "Emplacement", "Type", "Statut", "N° ERP", "Lignes",
             "Quantité comptée", "Posté le"],
            [
                [j.warehouse_id, j.location_id, str(j.kind), str(j.status),
                 j.journal_number, len(journal_lines.get(j.id, [])),
                 float(sum(l.qty for l in journal_lines.get(j.id, []))),
                 j.posted_at.isoformat() if j.posted_at else ""]
                for j in journals
            ],
        )

        # -- GENERIQUE consolidation and its WIP trace -------------------------
        consolidation_lines = ctx.consolidation.current_lines(campaign.id)
        if consolidation_lines:
            sheets["Consolidation GENERIQUE"] = (
                ["Article", "Désignation", "Quantité totale", "Bord de ligne",
                 "WIP assemblé", "WIP éclaté", "Zones", "Unité"],
                [
                    [l.item_number,
                     items[l.item_number].name if l.item_number in items else "",
                     float(l.qty), float(l.qty_line_side), float(l.qty_wip_ok),
                     float(l.qty_wip_exploded), ", ".join(l.zone_codes), l.unit]
                    for l in consolidation_lines
                ],
            )
            breakdown = ctx.consolidation.wip_breakdown(campaign.id)
            if breakdown:
                sheets["Décomposition WIP"] = (
                    ["Zone", "Assemblage", "Qté assemblage", "Composant",
                     "Qté / assemblage", "Qté composant"],
                    [
                        [b["zone_code"], b["parent_item"], float(b["parent_qty"]),
                         b["child_item"], float(b["qty_per_parent"]),
                         float(b["child_qty"])]
                        for b in breakdown
                    ],
                )

        # -- adjustments -------------------------------------------------------
        adjustments = ctx.adjustments.list(campaign.id)
        if adjustments:
            sheets["Ajustements"] = (
                ["Article", "Date", "Nature", "Journal", "Entrepôt", "Emplacement",
                 "Quantité", "Unité", "Valeur €", "Motif", "Commentaire"],
                [
                    [a.item_number,
                     a.physical_date.isoformat() if a.physical_date else "",
                     str(a.kind), a.journal_number, a.warehouse_id, a.location_id,
                     float(a.qty), a.unit, float(a.value), a.reason_code, a.comment]
                    for a in adjustments
                ],
            )

        # -- causes ------------------------------------------------------------
        cause_split = analysis.cause_split(campaign)
        sheets["Causes"] = (
            ["Code", "Libellé", "Famille", "Écart net €", "Écart absolu €",
             "Articles", "Part"],
            [
                [r.get("code") or "", r["label"], r.get("family", ""),
                 r["value"], r["absValue"], r["items"], r["share"]]
                for r in cause_split["rows"]
            ],
        )

        # -- controls ----------------------------------------------------------
        sheets["Contrôles"] = (
            ["Sévérité", "Code", "Message", "Article", "Entrepôt", "Emplacement"],
            [
                [f["severity"], f["code"], f["message"], f.get("item_number", ""),
                 f.get("warehouse_id", ""), f.get("location_id", "")]
                for f in controls["findings"]
            ],
        )

        # -- audit trail -------------------------------------------------------
        sheets["Journal d'audit"] = (
            ["Horodatage", "Acteur", "Action", "Entité", "Identifiant", "Résumé"],
            [
                [e.at.isoformat(), e.actor, e.action, e.entity_type, e.entity_id,
                 e.summary]
                for e in ctx.audit.list(campaign.id, limit=10_000)
            ],
        )

        payload = build_workbook(
            sheets,
            title=f"Bilan d'inventaire — {campaign.code}",
            provenance={
                "Campagne": f"{campaign.code} — {campaign.label}",
                "Date de comptage": campaign.count_date.isoformat(),
                "Statut": str(campaign.status),
                "Stock livre gelé le": _iso(campaign.book_stock_frozen_at),
                "Comptage clôturé le": _iso(campaign.counting_frozen_at),
                "Campagne clôturée le": _iso(campaign.closed_at),
                "Dupliquée depuis": campaign.cloned_from_code or "—",
                "Version du moteur de calcul": ENGINE_VERSION,
                "Généré le": utcnow().isoformat(timespec="seconds"),
                "Généré par": ctx.actor,
                "Avertissement": (
                    "Export en lecture seule. Toute modification de ce fichier "
                    "n'a aucun effet sur la campagne : l'application reste la "
                    "source de vérité."
                ),
            },
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="campaign",
            entity_id=campaign.id,
            summary=f"Export du dossier complet ({len(sheets)} onglets)",
        )
        return payload, f"bilan-inventaire_{campaign.code}.xlsx"

    def grid_export(
        self, campaign: Campaign, contract_key: str
    ) -> tuple[bytes, str]:
        """Export one grid, or an empty template when the grid has no data."""
        from ..ingest import get_contract

        ctx = self.ctx
        contract = get_contract(contract_key)
        headers = [f.label for f in contract.fields]
        rows = _grid_rows(ctx, campaign, contract_key)
        payload = build_workbook(
            {contract.title[:31]: (headers, rows)},
            title=contract.title,
            provenance={
                "Campagne": campaign.code,
                "Grille": contract.title,
                "Colonnes attendues": ", ".join(f.name for f in contract.fields),
                "Colonnes obligatoires": ", ".join(
                    f.name for f in contract.fields if f.required
                ) or "—",
                "Lignes": len(rows),
                "Généré le": utcnow().isoformat(timespec="seconds"),
                "Usage": (
                    "Ce fichier peut être réimporté tel quel : les en-têtes sont "
                    "reconnus automatiquement."
                ),
            },
        )
        return payload, f"{contract_key}_{campaign.code}.xlsx"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

#: Bounds on the number of blank rows a free-entry sheet may be printed with.
#: Ten is the fewest worth walking to the printer for; 180 fills four A4 pages,
#: past which somebody is printing a notebook rather than a counting sheet.
MIN_BLANK_LINES, MAX_BLANK_LINES = 10, 180

#: Wording used in the audit trail and in file names, one per mode.
_MODE_LABELS = {
    PrintMode.BLANK: "sans références",
    PrintMode.LIST: "sans quantités",
    PrintMode.FILLED: "avec quantités",
}
_MODE_SLUGS = {
    PrintMode.BLANK: "vierge",
    PrintMode.LIST: "a-compter",
    PrintMode.FILLED: "remplie",
}


def _validated_blank_lines(requested: int, *, mode: PrintMode) -> int:
    """How many empty rows a free-entry sheet carries.

    Only meaningful for the blank sheet; anywhere else the number is ignored
    rather than refused, because it is the screen's leftover state, not a
    decision the user made about *this* print.
    """
    if mode is not PrintMode.BLANK:
        return 0
    if not MIN_BLANK_LINES <= requested <= MAX_BLANK_LINES:
        raise ValidationError(
            f"Indiquez un nombre de lignes entre {MIN_BLANK_LINES} et "
            f"{MAX_BLANK_LINES}.",
            blankLines=requested,
        )
    return requested


def _printable_lines(
    lines: Sequence[CountSheetLine], items: Mapping[str, Item]
) -> list[dict[str, Any]]:
    """Sheet lines shaped for the PDF builder.

    Every line that carries a reference is printed, counted or not. On a filled
    sheet an uncounted line is rendered as « non compté » rather than dropped:
    "this article was on the list and nobody counted it" is precisely the fact a
    record has to carry, and silently omitting the row is how the legacy
    workbook lost lines.
    """
    out: list[dict[str, Any]] = []
    for line in lines:
        if not line.item_number:
            continue
        out.append({
            "item_number": line.item_number,
            "name": items[line.item_number].name if line.item_number in items else "",
            "section": str(line.section),
            "unit": line.unit,
            "qty": float(line.qty) if line.is_counted else None,
            "source": str(line.source),
            "comment": line.comment,
        })
    return out


def _grid_rows(ctx: ServiceContext, campaign: Campaign, key: str) -> list[list[Any]]:
    match key:
        case "items":
            return [
                [i.item_number, i.name, i.search_name, i.item_group,
                 i.lifecycle_state, str(i.item_type), i.category, i.program,
                 str(i.commonality), i.unit, float(i.std_price),
                 ",".join(sorted(str(e) for e in i.exclusions))]
                for i in ctx.referentials.list_items(campaign.id)
            ]
        case "boms":
            items = ctx.referentials.items_by_number(campaign.id)
            return [
                [l.parent_item,
                 items[l.parent_item].name if l.parent_item in items else "",
                 l.child_item, float(l.qty_per), l.unit]
                for l in ctx.referentials.list_bom_links(campaign.id)
            ]
        case "book_stock":
            return [
                [b.item_number, b.warehouse_id, b.location_id, float(b.qty),
                 b.unit, float(b.unit_cost)]
                for b in ctx.book_stock.list(campaign.id)
            ]
        case "locations":
            return [
                [l.warehouse_id, l.location_id, l.zone, str(l.type), str(l.status)]
                for l in ctx.referentials.list_locations(campaign.id)
            ]
        case "zones":
            return [
                [z.code, z.label, z.sector, z.display_order]
                for z in ctx.sheets.list_zones(campaign.id)
            ]
        case "count_sheets":
            # Pass 1 only: both passes carry the same article list by
            # construction, and exporting it twice would re-import as duplicates.
            from ..domain.enums import SheetPass

            zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
            lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
            return [
                [zones[sheet.zone_id].code, line.item_number, str(line.section),
                 line.unit]
                for sheet in ctx.sheets.list_sheets(campaign.id)
                if sheet.pass_no is SheetPass.PASS_1 and sheet.zone_id in zones
                for line in lines_by_sheet.get(sheet.id, ())
            ]
        case "adjustments":
            return [
                [a.item_number,
                 a.physical_date.isoformat() if a.physical_date else "",
                 str(a.kind), a.journal_number, float(a.qty), a.unit,
                 float(a.value), a.warehouse_id, a.location_id, a.reason_code,
                 a.comment]
                for a in ctx.adjustments.list(campaign.id)
            ]
        case _:
            return []


def _kpi_rows(kpis: Any) -> list[tuple[str, Any]]:
    data = kpis.as_dict()
    labels = [
        ("Stock livre — quantité", "bookQty"),
        ("Stock livre — valeur (€)", "bookValue"),
        ("Compté — quantité", "countedQty"),
        ("Compté — valeur (€)", "countedValue"),
        ("Écart net — quantité", "netVarianceQty"),
        ("Écart net — valeur (€)", "netVarianceValue"),
        ("Écart brut (absolu) — quantité", "grossVarianceQty"),
        ("Écart brut (absolu) — valeur (€)", "grossVarianceValue"),
        ("Écart résiduel après ajustements (€)", "residualValue"),
        ("Fiabilité nette en valeur", "netReliabilityValue"),
        ("Fiabilité brute en valeur", "grossReliabilityValue"),
        ("Fiabilité brute en quantité", "grossReliabilityQty"),
        ("IRA — exactitude des enregistrements", "ira"),
        ("Lignes analysées", "lineCount"),
        ("Lignes exactes (dans la tolérance)", "accurateLineCount"),
        ("Lignes au-delà des seuils", "materialLineCount"),
        ("Comptés sans stock livre", "countedOnlyCount"),
        ("Stock livre jamais compté", "bookOnlyCount"),
    ]
    return [(label, data.get(key)) for label, key in labels]


def _merge_pdfs(documents: list[bytes]) -> bytes:
    """Concatenate the per-zone sheets into the one stack that gets printed.

    Uses ``pypdfium2``, which the scan pipeline already depends on, rather than
    ``pypdf``: the latter imports ``cryptography`` at module load for encrypted
    documents it will never meet here, and a batch print failing because of a
    dependency of a feature it does not use is not a trade worth making.
    """
    import io as _io

    import pypdfium2 as pdfium

    merged = pdfium.PdfDocument.new()
    sources = [pdfium.PdfDocument(_io.BytesIO(document)) for document in documents]
    try:
        for source in sources:
            merged.import_pages(source)
        buffer = _io.BytesIO()
        merged.save(buffer)
        return buffer.getvalue()
    finally:
        # The sources have to outlive the import calls — pdfium copies pages
        # lazily — so they are closed only once the merge is written.
        for source in sources:
            source.close()
        merged.close()


def _slug(value: str) -> str:
    return "".join(
        c if c.isalnum() or c in "-_" else "-" for c in value.strip()
    ).strip("-").lower() or "sans-nom"


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else "—"
