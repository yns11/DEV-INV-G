"""GENERIQUE endpoints: zones, sheets, scans, arbitration and consolidation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile

from ...errors import ValidationError
from ...services import GenericService
from ..deps import CampaignDep, Ctx, generic_service, resolve_perimeter
from ..schemas import (
    ArbitrationDecisionRequest,
    ReclassifyRequest,
    SheetLinesRequest,
    SheetTransitionRequest,
    ZonePassesRequest,
    ZoneRequest,
)

router = APIRouter(prefix="/campaigns/{campaign_id}/generic", tags=["GENERIQUE"])

Service = Annotated[GenericService, Depends(generic_service)]

#: Scans are read by a vision model; anything else would be silently misread.
_ACCEPTED_SCAN_TYPES = {
    "application/pdf", "image/png", "image/jpeg", "image/jpg", "image/webp",
    "image/tiff",
}


@router.get("/zones", summary="Zones et feuilles de comptage")
def list_zones(
    campaign: CampaignDep,
    ctx: Ctx,
    service: Service,
    focus: Annotated[bool, Query()] = False,
    manager: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Zones with their derived status and per-pass progress.

    ``focus=true`` keeps only the zones assigned to the manager the signed-in
    identity resolves to. Like everywhere else, it is a **filter, not a
    permission**: nothing about what may be done to a zone changes with it.
    """
    return service.list_zones(
        campaign,
        perimeter=resolve_perimeter(campaign, ctx, focus=focus, manager=manager),
    )


@router.post("/zones", status_code=201, summary="Créer une zone")
def create_zone(
    campaign: CampaignDep, payload: ZoneRequest, service: Service
) -> dict[str, Any]:
    """Create a zone and its counting sheets.

    Allowed in preparation — which is when one decides what to count — as well
    as during counting, where a physical area nobody had listed is routinely
    discovered on the day.

    The zone is created with no pre-printed article list, so it is flagged as a
    free-entry sheet unless the caller says otherwise.
    """
    zone = service.create_zone(
        campaign,
        code=payload.code,
        label=payload.label,
        sector=payload.sector,
        display_order=payload.display_order,
        passes=payload.passes,
        free_entry=payload.free_entry,
        manager_code=payload.manager_code,
    )
    return zone.model_dump(mode="json")


@router.post("/zones/passes", summary="Changer le nombre de comptages de zones")
def set_zone_passes(
    campaign: CampaignDep, payload: ZonePassesRequest, service: Service
) -> dict[str, int]:
    """Bulk switch between one and two independent counts.

    Dropping to one deletes the second sheet, so the call is refused — naming
    the zones — when that sheet already carries a quantity: a zone brought back
    to a single count after the fact would lose a real count.
    """
    return service.set_zone_passes(campaign, payload.zone_ids, payload.passes)


@router.delete("/zones/{zone_id}", summary="Supprimer une zone")
def delete_zone(
    campaign: CampaignDep, zone_id: str, service: Service
) -> dict[str, bool]:
    service.delete_zone(campaign, zone_id)
    return {"deleted": True}


@router.get("/sheets/{sheet_id}", summary="Contenu d'une feuille de comptage")
def get_sheet(
    campaign: CampaignDep, sheet_id: str, service: Service
) -> dict[str, Any]:
    return service.get_sheet(campaign, sheet_id)


@router.post("/sheets/{sheet_id}/transition", summary="Changer le statut d'une feuille")
def transition_sheet(
    campaign: CampaignDep,
    sheet_id: str,
    payload: SheetTransitionRequest,
    service: Service,
) -> dict[str, Any]:
    """PENDING → COUNTING → ENCODING → DONE, each step reversible one notch.

    Pass 2 cannot start before pass 1 has been returned: two simultaneous counts
    are not two independent counts.
    """
    sheet = service.transition_sheet(
        campaign, sheet_id, payload.target, counter_name=payload.counter_name
    )
    return sheet.model_dump(mode="json")


@router.put("/sheets/{sheet_id}/lines", summary="Enregistrer les lignes d'une feuille")
def upsert_sheet_lines(
    campaign: CampaignDep,
    sheet_id: str,
    payload: SheetLinesRequest,
    service: Service,
) -> dict[str, int]:
    rows = [
        {
            "id": line.id,
            "item_number": line.item_number,
            "section": str(line.section),
            "qty": line.qty,
            "unit": line.unit,
            "comment": line.comment,
            "display_order": line.display_order,
        }
        for line in payload.lines
    ]
    written = service.upsert_sheet_lines(campaign, sheet_id, rows, replace=payload.replace)
    return {"written": written}


@router.delete("/lines/{line_id}", summary="Supprimer une ligne de feuille")
def delete_sheet_line(
    campaign: CampaignDep, line_id: str, service: Service
) -> dict[str, bool]:
    service.delete_sheet_line(campaign, line_id)
    return {"deleted": True}


@router.post("/sheets/{sheet_id}/scan", summary="Extraire une feuille scannée par IA")
async def extract_scan(
    campaign: CampaignDep,
    sheet_id: str,
    service: Service,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Read a scanned counting sheet with the vision model.

    Values land in the grid as ``SCAN_AI`` with a per-line confidence; a human
    reviews and validates them. Nothing is posted automatically, and a reference
    the model reads that was not on the printed sheet is reported as suspect
    rather than accepted.
    """
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in _ACCEPTED_SCAN_TYPES:
        raise ValidationError(
            f"Type de fichier non pris en charge : {content_type}. "
            "Formats acceptés : PDF, PNG, JPEG, WEBP, TIFF.",
            contentType=content_type,
        )
    payload = await file.read()
    if not payload:
        raise ValidationError("Le fichier reçu est vide.")
    return service.extract_from_scan(
        campaign,
        sheet_id,
        payload=payload,
        filename=file.filename or "scan",
        content_type=content_type,
    )


# --------------------------------------------------------------------------- #
# Arbitration
# --------------------------------------------------------------------------- #

@router.get("/arbitrations", summary="Écarts entre comptage n°1 et n°2")
def list_arbitrations(
    campaign: CampaignDep,
    service: Service,
    zone_id: Annotated[str | None, Query(alias="zoneId")] = None,
) -> list[dict[str, Any]]:
    """Every (item, section) present in either pass, valued and sorted.

    Sorted with the decisions that still need a human first, then by the euro
    impact of the gap — so the most expensive disagreement is dealt with first.
    """
    return service.list_arbitrations(campaign, zone_id)


@router.post("/zones/{zone_id}/arbitrations/refresh", summary="Recalculer les écarts")
def refresh_arbitrations(
    campaign: CampaignDep, zone_id: str, service: Service
) -> list[dict[str, Any]]:
    return service.refresh_arbitrations(campaign, zone_id)


@router.post("/arbitrations/{arbitration_id}", summary="Arbitrer un écart")
def decide_arbitration(
    campaign: CampaignDep,
    arbitration_id: str,
    payload: ArbitrationDecisionRequest,
    service: Service,
) -> dict[str, bool]:
    service.decide_arbitration(
        campaign, arbitration_id, payload.qty, comment=payload.comment
    )
    return {"decided": True}


@router.post(
    "/zones/{zone_id}/arbitrations/accept-pass-2",
    summary="Retenir le comptage n°2 pour tous les écarts d'une zone",
)
def accept_pass_2(
    campaign: CampaignDep, zone_id: str, service: Service
) -> dict[str, int]:
    """One-click resolution, recorded as an explicit decision per line."""
    return {"decided": service.accept_pass_2(campaign, zone_id)}


# --------------------------------------------------------------------------- #
# Consolidation
# --------------------------------------------------------------------------- #

@router.get("/consolidation/preview", summary="Prévisualiser la consolidation")
def preview_consolidation(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Run the consolidation including unfinished zones, without saving.

    This is the live view during counting: it shows what the GENERIQUE journal
    would contain right now, and which zones are still missing.
    """
    result = service.consolidate(campaign, preview=True)
    return {
        "lines": [
            {**l.model_dump(mode="json"), "qty": float(l.qty)} for l in result.lines
        ],
        "totalQty": float(result.total_qty),
        "zonesIncluded": result.zones_included,
        "zonesSkipped": result.zones_skipped,
        "findings": [f.model_dump(mode="json") for f in result.findings],
        "blocking": len(result.blocking),
    }


@router.post("/consolidation", summary="Consolider et alimenter le journal GENERIQUE")
def run_consolidation(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Consolidate every finished zone and post the result to the INVV journal.

    Replaces the Excel macro plus the manual copy/paste into the ERP: the
    resulting journal is downloadable in the ERP import format.
    """
    return service.consolidate_and_save(campaign)


@router.get("/consolidation", summary="Dernière consolidation enregistrée")
def current_consolidation(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    return service.current_consolidation(campaign)


@router.get("/wip-without-bom", summary="Lignes WIP sans nomenclature")
def wip_without_bom(campaign: CampaignDep, service: Service) -> list[dict[str, Any]]:
    """WIP lines whose assembly has no bill of materials.

    They block the consolidation on purpose: exploding an assembly with no
    structure would silently destroy the counted quantity. Since the BOM
    referential is frozen during counting, the remedy is to reclassify the line
    as *WIP assemblé* — which is what ``POST /reclassify-wip`` does.
    """
    return service.wip_without_bom(campaign)


@router.post("/reclassify-wip", summary="Reclasser des lignes de comptage")
def reclassify_wip(
    campaign: CampaignDep, payload: ReclassifyRequest, service: Service
) -> dict[str, int]:
    """Move counting-sheet lines to another section, as an explicit decision."""
    return {
        "updated": service.reclassify_wip(
            campaign, payload.line_ids, section=payload.section
        )
    }


@router.get("/wip/{item_number}", summary="Décomposition du WIP d'un composant")
def wip_breakdown(
    campaign: CampaignDep, item_number: str, service: Service
) -> list[dict[str, Any]]:
    """Answer "where does this component's WIP quantity come from?".

    The specification asks for the WIP to be explorable rather than shown as one
    aggregated number; this is the data behind that drill-down.
    """
    return service.wip_breakdown(campaign, item_number.strip().upper())
