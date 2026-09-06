"""Download endpoints: printable sheets, ERP journal exports, campaign dossier."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...domain.printing import PrintMode
from ...services import ReportService
from ...services.report_service import MAX_BLANK_LINES
from ..deps import CampaignDep, report_service
from ..downloads import attachment
from ..schemas import TableExportRequest

router = APIRouter(prefix="/campaigns/{campaign_id}/reports", tags=["rapports"])

Service = Annotated[ReportService, Depends(report_service)]

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF = "application/pdf"


def _file(media_type: str, description: str) -> dict:
    """Dire dans le contrat qu'une route rend un fichier, et lequel.

    Sans cela le contrat annonce du JSON — ce que ces routes ne rendent
    jamais — et le client généré propose de désérialiser un classeur.
    """
    return {
        200: {
            "description": description,
            "content": {media_type: {"schema": {"type": "string",
                                                "format": "binary"}}},
        }
    }


#: Shared print options. Printing is available from the first phase — paper is
#: prepared *before* the count. What the phase decides is which mode exists.
_Mode = Annotated[PrintMode, Query(alias="mode")]
_WithSources = Annotated[bool, Query(alias="withSources")]
_BlankLines = Annotated[int, Query(ge=0, le=MAX_BLANK_LINES, alias="blankLines")]


@router.get(
    "/counting-sheets/{sheet_id}.pdf",
    summary="Imprimer une feuille",
    responses=_file(_PDF, "Une feuille de comptage imprimable"),
)
def counting_sheet(
    campaign: CampaignDep,
    sheet_id: str,
    service: Service,
    mode: _Mode = PrintMode.LIST,
    with_sources: _WithSources = False,
    blank_lines: _BlankLines = 0,
) -> Response:
    """One sheet, in one of its three modes.

    ``mode=list`` prints the article list with an empty quantity column — the
    sheet handed to a counter. ``mode=filled`` prints the counted quantities,
    and only exists once counting has started; ``withSources=true`` adds the
    provenance and comment columns to it. ``mode=blank`` prints ``blankLines``
    (10–180) empty rows for a free-entry zone.
    """
    payload, filename = service.counting_sheet_pdf(
        campaign, sheet_id,
        mode=mode, with_sources=with_sources, blank_lines=blank_lines,
    )
    return attachment(payload, filename, "application/pdf")


@router.get(
    "/counting-sheets.pdf",
    summary="Imprimer toutes les feuilles d'un passage",
    responses=_file(_PDF, "Toutes les feuilles d’un passage, en un document"),
)
def all_counting_sheets(
    campaign: CampaignDep,
    service: Service,
    pass_no: Annotated[int, Query(ge=1, le=2, alias="passNo")] = 1,
    mode: _Mode = PrintMode.LIST,
    with_sources: _WithSources = False,
    blank_lines: _BlankLines = 0,
    zone_ids: Annotated[str | None, Query(alias="zoneIds")] = None,
) -> Response:
    """The eve-of-inventory print: every zone the mode applies to, in zone order.

    ``zoneIds`` narrows it to a selection. Reprinting one sector's sheets, or the
    four zones whose stack got soaked, is the common case the day after — and
    printing the whole site again to get them is how a second, contradictory
    stack of paper ends up on the floor.
    """
    selection = (
        [z for z in (part.strip() for part in zone_ids.split(",")) if z]
        if zone_ids else None
    )
    payload, filename = service.all_counting_sheets_pdf(
        campaign, pass_no=pass_no,
        mode=mode, with_sources=with_sources, blank_lines=blank_lines,
        zone_ids=selection,
    )
    return attachment(payload, filename, "application/pdf")


@router.get(
    "/journals/{journal_id}.xlsx",
    summary="Exporter un journal pour l'ERP",
    responses=_file(_XLSX, "Un journal au format d’import ERP"),
)
def journal_export(
    campaign: CampaignDep, journal_id: str, service: Service
) -> Response:
    """Column names and order match the ERP counting-journal import template.

    The file is *imported* into the ERP rather than copy-pasted, which removes
    the transcription and row-shift errors of the manual paste.
    """
    payload, filename = service.journal_export(campaign, journal_id)
    return attachment(payload, filename, _XLSX)


@router.post(
    "/table.xlsx",
    summary="Exporter un tableau affiché",
    responses=_file(_XLSX, "Le tableau affiché, en classeur"),
)
def table_export(
    campaign: CampaignDep, payload: TableExportRequest, service: Service
) -> Response:
    """Any grid, exactly as it is on screen, as a workbook.

    One endpoint for every table in the application rather than one export route
    per screen: the grid component knows its own columns and its own selection,
    so it can ask for the file itself, and a table added tomorrow gets the
    button for free instead of getting it eventually.
    """
    payload_bytes, filename = service.table_export(
        campaign,
        title=payload.title,
        columns=[(c.key, c.label or c.key) for c in payload.columns],
        rows=payload.rows,
    )
    return attachment(payload_bytes, filename, _XLSX)


#: Which variance table is being exported — the same two the screen offers.
_Granularity = Annotated[
    Literal["item", "item_location"], Query(alias="granularity")
]
_MaterialOnly = Annotated[bool, Query(alias="materialOnly")]


@router.get(
    "/variances.xlsx",
    summary="Exporter les écarts en Excel",
    responses=_file(_XLSX, "Les écarts, une colonne par chiffre"),
)
def variance_export(
    campaign: CampaignDep,
    service: Service,
    granularity: _Granularity = "item",
    material_only: _MaterialOnly = False,
) -> Response:
    """The variance view, with each figure in its own column.

    ``granularity=item`` gives the site's real loss or gain; ``item_location``
    the detail one goes and recounts from. Quantity and value are separate
    columns for both the ERP stock and the counted stock — a spreadsheet whose
    cells hold two figures cannot be summed or pivoted.
    """
    payload, filename = service.variance_export(
        campaign, granularity=granularity, material_only=material_only
    )
    return attachment(payload, filename, _XLSX)


@router.get(
    "/variances.pdf",
    summary="Imprimer les écarts",
    responses=_file(_PDF, "Les écarts, en document à remettre"),
)
def variance_pdf(
    campaign: CampaignDep,
    service: Service,
    granularity: _Granularity = "item",
    material_only: _MaterialOnly = False,
) -> Response:
    """The same table as a document, biggest variances first.

    Capped: past a few hundred rows a PDF stops being read. The page says how
    many lines it left out, and the Excel export carries them all.
    """
    payload, filename = service.variance_pdf(
        campaign, granularity=granularity, material_only=material_only
    )
    return attachment(payload, filename, "application/pdf")


@router.get(
    "/campaign.xlsx",
    summary="Exporter le dossier complet de la campagne",
    responses=_file(_XLSX, "Le dossier complet de la campagne"),
)
def campaign_workbook(campaign: CampaignDep, service: Service) -> Response:
    """The full dossier: KPIs, variances, snapshot, journals, WIP, causes, audit.

    A read-only picture produced by the application — not a live workbook people
    edit and re-derive numbers from.
    """
    payload, filename = service.campaign_workbook(campaign)
    return attachment(payload, filename, _XLSX)


@router.get(
    "/consolidation-fallback.xlsx",
    summary="Exporter le classeur de repli de la consolidation GENERIQUE",
    responses=_file(_XLSX, "Le classeur de repli de la consolidation GENERIQUE"),
)
def consolidation_fallback(campaign: CampaignDep, service: Service) -> Response:
    """Le second classeur : la consolidation GENERIQUE **refaite par formules**.

    Le dossier de campagne est une photo ; celui-ci porte les données —
    référentiel, nomenclatures, une feuille par zone — et recalcule le journal
    consolidé à chaque correction. C'est le repli du jour où l'application n'est
    pas joignable et où le journal doit partir quand même.
    """
    payload, filename = service.consolidation_fallback(campaign)
    return attachment(payload, filename, _XLSX)


@router.get(
    "/grids/{contract_key}.xlsx",
    summary="Exporter une grille ou son modèle",
    responses=_file(_XLSX, "Une grille, ou son modèle quand elle est vide"),
)
def grid_export(
    campaign: CampaignDep, contract_key: str, service: Service
) -> Response:
    """Export a grid's content, or an empty template when it has no data yet.

    The exported file can be re-imported as-is: the headers are the contract.
    """
    payload, filename = service.grid_export(campaign, contract_key)
    return attachment(payload, filename, _XLSX)
