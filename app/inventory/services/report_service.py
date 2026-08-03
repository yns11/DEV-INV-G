"""Generation of the campaign's downloadable artefacts."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..domain.enums import AuditAction, SheetPass
from ..domain.models import Campaign
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

    def counting_sheet_pdf(self, campaign: Campaign, sheet_id: str) -> tuple[bytes, str]:
        """Render one printable counting sheet."""
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

        items = ctx.referentials.items_by_number(campaign.id)
        lines = [
            {
                "item_number": line.item_number,
                "name": items[line.item_number].name
                if line.item_number in items else "",
                "section": str(line.section),
                "unit": line.unit,
            }
            for line in ctx.sheets.list_sheet_lines(sheet_id)
        ]
        if not lines:
            raise ValidationError(
                "Cette feuille ne contient aucune ligne : ajoutez d'abord les "
                "articles à compter."
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
        )
        filename = (
            f"feuille-comptage_{_slug(zone.code)}_passage-{pass_no}_"
            f"{campaign.code}.pdf"
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="count_sheet",
            entity_id=sheet_id,
            summary=f"Impression de la feuille {zone.code} — passage n°{pass_no}",
        )
        return payload, filename

    def all_counting_sheets_pdf(
        self, campaign: Campaign, *, pass_no: int = 1
    ) -> tuple[bytes, str]:
        """One PDF containing every zone's sheet for a given pass.

        This is what gets printed on the eve of the inventory — one job, one
        stack of paper, in zone order.
        """
        ctx = self.ctx
        target_pass = SheetPass.PASS_1 if pass_no == 1 else SheetPass.PASS_2
        zones = ctx.sheets.list_zones(campaign.id)
        sheets = {
            (s.zone_id, s.pass_no): s
            for s in ctx.sheets.list_sheets(campaign.id)
        }
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        items = ctx.referentials.items_by_number(campaign.id)

        documents: list[bytes] = []
        for zone in zones:
            sheet = sheets.get((zone.id, target_pass))
            if sheet is None:
                continue
            lines = [
                {
                    "item_number": line.item_number,
                    "name": items[line.item_number].name
                    if line.item_number in items else "",
                    "section": str(line.section),
                    "unit": line.unit,
                }
                for line in lines_by_sheet.get(sheet.id, [])
            ]
            if not lines:
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
            ))

        if not documents:
            raise ValidationError(
                "Aucune zone ne contient de lignes à imprimer pour ce passage."
            )

        merged = _merge_pdfs(documents)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="count_sheet",
            summary=(
                f"Impression groupée de {len(documents)} feuille(s) — "
                f"passage n°{pass_no}"
            ),
        )
        return merged, f"feuilles-comptage_passage-{pass_no}_{campaign.code}.pdf"

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
    import io as _io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for document in documents:
        for page in PdfReader(_io.BytesIO(document)).pages:
            writer.add_page(page)
    buffer = _io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _slug(value: str) -> str:
    return "".join(
        c if c.isalnum() or c in "-_" else "-" for c in value.strip()
    ).strip("-").lower() or "sans-nom"


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else "—"
