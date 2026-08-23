"""Campaign lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ...domain.enums import CampaignStatus
from ...domain.models import CampaignConfig, Thresholds
from ...services import BoardService, CampaignService
from ..deps import CampaignDep, board_service, campaign_service
from ..schemas import (
    CloneCampaignRequest,
    CreateCampaignRequest,
    TransitionRequest,
    UpdateThresholdsRequest,
)

router = APIRouter(prefix="/campaigns", tags=["campagnes"])

Service = Annotated[CampaignService, Depends(campaign_service)]
Board = Annotated[BoardService, Depends(board_service)]


@router.get("", summary="Lister les campagnes")
def list_campaigns(
    service: Service,
    include_closed: Annotated[bool, Query(alias="includeClosed")] = True,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Une page de campagnes, et combien il y en a en tout.

    La réponse portait un tableau nu, borné à cent sans le dire : après
    quelques années d'inventaires, les plus anciennes disparaissaient de
    l'écran sans qu'aucun message ne l'annonce. ``total`` est ce qui permet à
    l'interface de proposer les suivantes plutôt que de faire comme si elles
    n'existaient pas.
    """
    campaigns, total = service.page(
        include_closed=include_closed, limit=limit, offset=offset
    )
    return {
        "items": [c.model_dump(mode="json") for c in campaigns],
        "total": total,
        "offset": offset,
    }


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


@router.get("/{campaign_id}/work-queues", summary="Files de travail du jour")
def work_queues(
    campaign: CampaignDep,
    board: Board,
    focus: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Ce qui attend quelqu'un, maintenant.

    Un pourcentage répond à « où en est-on », jamais à « que faire ». Ces files
    répondent aux trois questions d'un matin d'inventaire : ce qui attend une
    décision, ce qu'on peut fermer tout de suite, et qui n'a pas commencé.

    ``focus=true`` applique le périmètre du gestionnaire connecté. C'est là que
    le périmètre gagne sa place : quarante zones réparties sur neuf
    responsables donnent un tableau illisible si chacun voit tout.
    """
    return board.work_queues(campaign, focus=focus)


@router.get(
    "/{campaign_id}/closure-checklist",
    summary="Liste de contrôle avant clôture",
)
def closure_checklist(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """L'état des lieux du dossier, avant le seul geste irréversible.

    Trois tons : ce qui bloque, ce qui mérite un regard, ce qui est fait. Le
    premier vient de la même fonction que le refus — l'écran et le serveur ne
    peuvent donc pas diverger. Lisible pendant toute la phase d'analyse, et pas
    seulement dans la fenêtre qui clôture : découvrir trois points bloquants au
    moment de cliquer, un vendredi soir, est exactement ce qu'on évite ici.
    """
    return service.closure_checklist(campaign.id)


@router.post("/{campaign_id}/transition", summary="Changer le statut de la campagne")
def transition(
    campaign: CampaignDep, payload: TransitionRequest, service: Service
) -> dict[str, Any]:
    return service.transition(campaign.id, payload.target).model_dump(mode="json")


@router.get("/{campaign_id}/thresholds", summary="Seuils de matérialité")
def get_thresholds(campaign: CampaignDep, service: Service) -> list[dict[str, Any]]:
    return [t.model_dump(mode="json") for t in service.thresholds(campaign)]


@router.put("/{campaign_id}/thresholds", summary="Mettre à jour les seuils")
def update_thresholds(
    campaign: CampaignDep, payload: UpdateThresholdsRequest, service: Service
) -> list[dict[str, Any]]:
    updated = service.update_thresholds(
        campaign.id, [Thresholds(**t.model_dump()) for t in payload.thresholds]
    )
    return [t.model_dump(mode="json") for t in updated]


@router.get("/{campaign_id}/audit", summary="Journal d'audit")
def audit_trail(
    campaign: CampaignDep,
    service: Service,
    entity_type: Annotated[str | None, Query(alias="entityType")] = None,
    actor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    events = service.audit_trail(
        campaign, entity_type=entity_type, actor=actor, limit=limit, offset=offset
    )
    return [e.model_dump(mode="json") for e in events]


@router.get("/{campaign_id}/imports", summary="Historique des imports")
def import_history(
    campaign: CampaignDep,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return service.import_history(campaign, limit=limit)
