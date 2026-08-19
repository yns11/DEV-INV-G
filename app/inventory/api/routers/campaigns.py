"""Campaign lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ...domain.enums import CampaignStatus
from ...domain.models import CampaignConfig, Thresholds
from ...services import CampaignService
from ..deps import CampaignDep, Ctx, campaign_service
from ..schemas import (
    CampaignConfigPayload,
    CloneCampaignRequest,
    CreateCampaignRequest,
    TransitionRequest,
    UpdateThresholdsRequest,
)

router = APIRouter(prefix="/campaigns", tags=["campagnes"])

Service = Annotated[CampaignService, Depends(campaign_service)]


@router.get("", summary="Lister les campagnes")
def list_campaigns(
    service: Service,
    include_closed: Annotated[bool, Query(alias="includeClosed")] = True,
) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") for c in service.list(include_closed=include_closed)]


@router.post("", status_code=201, summary="Créer une campagne")
def create_campaign(payload: CreateCampaignRequest, service: Service) -> dict[str, Any]:
    campaign = service.create(
        code=payload.code,
        label=payload.label,
        count_date=payload.count_date,
        config=CampaignConfig(**payload.config.model_dump()) if payload.config else None,
        thresholds=(
            [Thresholds(**t.model_dump()) for t in payload.thresholds]
            if payload.thresholds
            else None
        ),
    )
    return campaign.model_dump(mode="json")


@router.post("/clone", status_code=201, summary="Dupliquer une campagne")
def clone_campaign(payload: CloneCampaignRequest, service: Service) -> dict[str, Any]:
    """Start a campaign from a previous one's referentials.

    Copies the article list, the bills of materials, the location referential
    and the GENERIQUE zones with their pre-printed sheets — never the counts.
    """
    campaign = service.clone(
        source_campaign_id=payload.source_campaign_id,
        code=payload.code,
        label=payload.label,
        count_date=payload.count_date,
        include_zones=payload.include_zones,
        include_sheet_lines=payload.include_sheet_lines,
    )
    return campaign.model_dump(mode="json")


@router.get("/{campaign_id}", summary="Détail d'une campagne")
def get_campaign(campaign: CampaignDep) -> dict[str, Any]:
    return campaign.model_dump(mode="json")


@router.delete("/{campaign_id}", summary="Supprimer une campagne")
def delete_campaign(campaign: CampaignDep, service: Service) -> dict[str, bool]:
    """Logical deletion, reserved to the campaign's author.

    Nothing is erased: the row is flagged and stops being listed. A campaign
    created by somebody else is refused with a 403 rather than silently ignored.
    """
    service.delete(campaign.id)
    return {"deleted": True}


@router.get("/{campaign_id}/overview", summary="Tableau de bord de la campagne")
def overview(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Header data for every screen: status, permissions and both progress bars."""
    data = service.overview(campaign.id)
    data["campaign"] = data["campaign"].model_dump(mode="json")
    return data


@router.get(
    "/{campaign_id}/transition-readiness",
    summary="Vérifier si un changement de statut est possible",
)
def transition_readiness(
    campaign: CampaignDep,
    service: Service,
    target: Annotated[CampaignStatus, Query()],
) -> dict[str, Any]:
    """What still blocks a transition, without attempting it."""
    return service.transition_readiness(campaign.id, target)


@router.post("/{campaign_id}/transition", summary="Changer le statut de la campagne")
def transition(
    campaign: CampaignDep, payload: TransitionRequest, service: Service
) -> dict[str, Any]:
    return service.transition(campaign.id, payload.target).model_dump(mode="json")


@router.get("/{campaign_id}/thresholds", summary="Seuils de matérialité")
def get_thresholds(campaign: CampaignDep, ctx: Ctx) -> list[dict[str, Any]]:
    return [t.model_dump(mode="json") for t in ctx.campaigns.list_thresholds(campaign.id)]


@router.put("/{campaign_id}/thresholds", summary="Mettre à jour les seuils")
def update_thresholds(
    campaign: CampaignDep, payload: UpdateThresholdsRequest, service: Service
) -> list[dict[str, Any]]:
    updated = service.update_thresholds(
        campaign.id, [Thresholds(**t.model_dump()) for t in payload.thresholds]
    )
    return [t.model_dump(mode="json") for t in updated]


@router.put("/{campaign_id}/config", summary="Mettre à jour la configuration")
def update_config(
    campaign: CampaignDep, payload: CampaignConfigPayload, service: Service
) -> dict[str, Any]:
    updated = service.update_config(campaign.id, CampaignConfig(**payload.model_dump()))
    return updated.model_dump(mode="json")


@router.get("/{campaign_id}/audit", summary="Journal d'audit")
def audit_trail(
    campaign: CampaignDep,
    ctx: Ctx,
    entity_type: Annotated[str | None, Query(alias="entityType")] = None,
    actor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    events = ctx.audit.list(
        campaign.id, entity_type=entity_type, actor=actor, limit=limit, offset=offset
    )
    return [e.model_dump(mode="json") for e in events]


@router.get("/{campaign_id}/imports", summary="Historique des imports")
def import_history(
    campaign: CampaignDep,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return [
        {**row, "id": str(row["id"])}
        for row in ctx.imports.list(campaign.id, limit=limit)
    ]
