"""Download endpoints: printable sheets, ERP journal exports, campaign dossier."""

from __future__ import annotations

import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...services import ReportService
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


@router.get("/counting-sheets/{sheet_id}.pdf", summary="Imprimer une feuille")
def counting_sheet(
    campaign: CampaignDep, sheet_id: str, service: Service
) -> Response:
    payload, filename = service.counting_sheet_pdf(campaign, sheet_id)
    return _download(payload, filename, "application/pdf")


@router.get("/counting-sheets.pdf", summary="Imprimer toutes les feuilles d'un passage")
def all_counting_sheets(
    campaign: CampaignDep,
    service: Service,
    pass_no: Annotated[int, Query(ge=1, le=2, alias="passNo")] = 1,
) -> Response:
    """One PDF with every zone's sheet, in zone order — the eve-of-inventory print."""
    payload, filename = service.all_counting_sheets_pdf(campaign, pass_no=pass_no)
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
