"""Counting-journal endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ...domain.enums import JournalStatus
from ...domain.models import LocationKey
from ...services import CountingService
from ..deps import CampaignDep, Ctx, counting_service, resolve_perimeter
from ..schemas import JournalLineRequest, JournalStatusRequest, LocationStatusRequest

router = APIRouter(prefix="/campaigns/{campaign_id}/counting", tags=["comptage"])

Service = Annotated[CountingService, Depends(counting_service)]


@router.get("/journals", summary="Lister les journaux de comptage")
def list_journals(
    campaign: CampaignDep,
    ctx: Ctx,
    service: Service,
    status: Annotated[JournalStatus | None, Query()] = None,
    warehouse_id: Annotated[str | None, Query(alias="warehouseId")] = None,
    focus: Annotated[bool, Query()] = False,
    manager: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Every journal of the campaign, or only those in the caller's perimeter.

    ``focus=true`` keeps the journals whose warehouse is assigned to the manager
    the signed-in identity resolves to. It is a **filter, not a permission**: the
    same actions remain available on the journals it hides, and a manager keeps
    the right to act outside their perimeter.

    Filtering here rather than in the browser is the whole point — a client-side
    filter would still ship the site's entire counting state to every workstation.
    """
    return service.list_journals(
        campaign.id,
        status=status,
        warehouse_id=warehouse_id,
        perimeter=resolve_perimeter(campaign, ctx, focus=focus, manager=manager),
    )


@router.get("/progress", summary="Avancement du comptage")
def progress(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    return service.progress(campaign.id)


@router.get("/controls", summary="Contrôles sur les journaux")
def controls(campaign: CampaignDep, service: Service) -> list[dict[str, Any]]:
    return service.controls(campaign)


@router.get("/journals/{journal_id}", summary="Détail d'un journal")
def get_journal(
    campaign: CampaignDep, journal_id: str, service: Service
) -> dict[str, Any]:
    """Journal lines, plus the book-stock articles nobody counted.

    Showing the *uncounted* book stock on the same screen is deliberate: those
    lines are the ones that get written down to zero at closing, and they used
    to surface only weeks later in the balance sheet.
    """
    return service.get_journal(campaign.id, journal_id)


@router.post("/journals/status", summary="Changer le statut de journaux")
def set_status(
    campaign: CampaignDep, payload: JournalStatusRequest, service: Service
) -> dict[str, int]:
    """Batch status change.

    ``BOOK_ENFORCED`` forces the counted quantity to equal the book quantity —
    for locations inventoried before the snapshot was taken.
    """
    count = service.set_status(campaign, payload.journal_ids, payload.status)
    return {"updated": count}


@router.post("/journals/{journal_id}/lines", summary="Créer ou corriger une ligne")
def upsert_line(
    campaign: CampaignDep,
    journal_id: str,
    payload: JournalLineRequest,
    service: Service,
) -> dict[str, Any]:
    """Write a manual quantity without touching the imported one."""
    line = service.upsert_line(
        campaign,
        journal_id,
        line_id=payload.line_id,
        item_number=payload.item_number,
        qty=payload.qty,
        unit=payload.unit,
        comment=payload.comment,
        expected_version=payload.expected_version,
    )
    return {**line.model_dump(mode="json"), "qty": float(line.qty)}


@router.delete("/lines/{line_id}", summary="Supprimer une ligne de comptage")
def delete_line(
    campaign: CampaignDep, line_id: str, service: Service
) -> dict[str, bool]:
    service.delete_line(campaign, line_id)
    return {"deleted": True}


@router.post("/locations/status", summary="Activer ou désactiver des emplacements")
def set_location_status(
    campaign: CampaignDep, payload: LocationStatusRequest, service: Service
) -> dict[str, int]:
    """Disabling a location removes it from the perimeter entirely.

    Its journal is deleted, its quantities and values leave every KPI, and it
    stops counting in the progress denominator.
    """
    keys = [
        LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id)
        for l in payload.locations
    ]
    return service.set_location_status(campaign, keys, payload.status)
