"""Download endpoints: printable sheets, ERP journal exports, campaign dossier."""

from __future__ import annotations

import urllib.parse
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...domain.printing import PrintMode
from ...services import ReportService
from ...services.report_service import MAX_BLANK_LINES
from ..deps import CampaignDep, report_service

router = APIRouter(prefix="/campaigns/{campaign_id}/reports", tags=["rapports"])

Service = Annotated[ReportService, Depends(report_service)]

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(payload: bytes, filename: str, media_type: str) -> Response:
    """Attachment response with an RFC 5987 filename.

    French campaign labels contain accents; without the ``filename*`` form some
    browsers mangle them into unusable file names.
    """
    quoted = urllib.parse.quote(filename)
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quoted}"
            ),
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store",
        },
    )


#: Shared print options. Printing is available from the first phase — paper is
#: prepared *before* the count. What the phase decides is which mode exists.
_Mode = Annotated[PrintMode, Query(alias="mode")]
_WithSources = Annotated[bool, Query(alias="withSources")]
_BlankLines = Annotated[int, Query(ge=0, le=MAX_BLANK_LINES, alias="blankLines")]


@router.get("/counting-sheets/{sheet_id}.pdf", summary="Imprimer une feuille")
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
    return _download(payload, filename, "application/pdf")


@router.get("/counting-sheets.pdf", summary="Imprimer toutes les feuilles d'un passage")
def all_counting_sheets(
    campaign: CampaignDep,
    service: Service,
    pass_no: Annotated[int, Query(ge=1, le=2, alias="passNo")] = 1,
    mode: _Mode = PrintMode.LIST,
    with_sources: _WithSources = False,
    blank_lines: _BlankLines = 0,
) -> Response:
    """The eve-of-inventory print: every zone the mode applies to, in zone order."""
    payload, filename = service.all_counting_sheets_pdf(
        campaign, pass_no=pass_no,
        mode=mode, with_sources=with_sources, blank_lines=blank_lines,
    )
    return _download(payload, filename, "application/pdf")


@router.get("/journals/{journal_id}.xlsx", summary="Exporter un journal pour l'ERP")
def journal_export(
    campaign: CampaignDep, journal_id: str, service: Service
) -> Response:
    """Column names and order match the ERP counting-journal import template.

    The file is *imported* into the ERP rather than copy-pasted, which removes
    the transcription and row-shift errors of the manual paste.
    """
    payload, filename = service.journal_export(campaign, journal_id)
    return _download(payload, filename, _XLSX)


#: Which variance table is being exported — the same two the screen offers.
_Granularity = Annotated[
    Literal["item", "item_location"], Query(alias="granularity")
]
_MaterialOnly = Annotated[bool, Query(alias="materialOnly")]


@router.get("/variances.xlsx", summary="Exporter les écarts en Excel")
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
    return _download(payload, filename, _XLSX)


@router.get("/variances.pdf", summary="Imprimer les écarts")
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
    return _download(payload, filename, "application/pdf")


@router.get("/campaign.xlsx", summary="Exporter le dossier complet de la campagne")
def campaign_workbook(campaign: CampaignDep, service: Service) -> Response:
    """The full dossier: KPIs, variances, snapshot, journals, WIP, causes, audit.

    A read-only picture produced by the application — not a live workbook people
    edit and re-derive numbers from.
    """
    payload, filename = service.campaign_workbook(campaign)
    return _download(payload, filename, _XLSX)


@router.get("/grids/{contract_key}.xlsx", summary="Exporter une grille ou son modèle")
def grid_export(
    campaign: CampaignDep, contract_key: str, service: Service
) -> Response:
    """Export a grid's content, or an empty template when it has no data yet.

    The exported file can be re-imported as-is: the headers are the contract.
    """
    payload, filename = service.grid_export(campaign, contract_key)
    return _download(payload, filename, _XLSX)
