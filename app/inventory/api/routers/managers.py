"""Managers and perimeters: the referential and both assignment grids.

The three endpoints below are the administration side of the focus mode. The
filtering side lives on the read endpoints themselves
(``/counting/journals?focus=true``, ``/generic/zones?focus=true``), because the
whole point is that the server, not the client, decides what a manager sees.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ...services import ManagerService
from ...services.portfolio_service import PortfolioService, clear_portfolios
from ...services.product_service import ProductService, clear_products
from ..deps import CampaignDep, Ctx, manager_service
from ..schemas import (
    ManagerRowsRequest,
    WarehouseAssignmentRequest,
    ZoneAssignmentRequest,
)

router = APIRouter(prefix="/campaigns/{campaign_id}/managers", tags=["gestionnaires"])

Service = Annotated[ManagerService, Depends(manager_service)]


@router.get("", summary="Gestionnaires, entrepôts et zones affectés")
def overview(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Everything the two Préparation tabs render, in one round trip.

    The warehouse list mixes the ones the campaign actually knows about with the
    site's usual set, so the assignment can be prepared before the book stock is
    loaded. ``AUTRES`` is not a warehouse: it assigns every warehouse nobody
    named explicitly, which is what stops a newly discovered one from falling
    outside everybody's perimeter.
    """
    return service.overview(campaign)


@router.put("", summary="Renommer les gestionnaires et déclarer leur identité")
def save_managers(
    campaign: CampaignDep, payload: ManagerRowsRequest, service: Service
) -> list[dict[str, Any]]:
    """The identity is what resolves « mon périmètre » without the client saying so."""
    managers = service.save_managers(
        campaign, [m.model_dump() for m in payload.managers]
    )
    return [m.model_dump(mode="json") for m in managers]


@router.post("/warehouses", summary="Affecter des entrepôts (et leurs journaux)")
def assign_warehouses(
    campaign: CampaignDep, payload: WarehouseAssignmentRequest, service: Service
) -> dict[str, int]:
    """A journal is in a manager's perimeter when its warehouse is."""
    assignments = {a.warehouse_id: a.manager_code for a in payload.assignments}
    return {"updated": service.assign_warehouses(campaign, assignments)}


@router.post("/zones", summary="Affecter des zones de comptage")
def assign_zones(
    campaign: CampaignDep, payload: ZoneAssignmentRequest, service: Service
) -> dict[str, int]:
    """Bulk assignment over a selection — an empty code detaches the zones."""
    return {
        "updated": service.assign_zones(
            campaign, payload.zone_ids, payload.manager_code
        )
    }


# --------------------------------------------------------------------------- #
# Portefeuilles — les articles, là où les affectations portent des emplacements
# --------------------------------------------------------------------------- #

portfolios = APIRouter(
    prefix="/campaigns/{campaign_id}/portfolios", tags=["portefeuilles"]
)


@portfolios.get("", summary="Qui suit quelle référence")
def list_portfolios(campaign: CampaignDep, ctx: Ctx) -> dict[str, Any]:
    """Le tableau d'attribution, et ce qu'il pèse par personne.

    Les deux ensemble : la grille seule ne dit pas si la répartition est
    complète, et sur cinq cents références c'est la première question.
    """
    return PortfolioService(ctx).overview(campaign)


@portfolios.delete("", summary="Retirer toutes les attributions")
def clear_all_portfolios(campaign: CampaignDep, ctx: Ctx) -> dict[str, int]:
    """Repartir de zéro. Le chargement fusionne, donc ceci est la seule façon
    de tout défaire d'un coup — et c'est explicite plutôt qu'implicite dans un
    fichier vide."""
    return {"removed": clear_portfolios(ctx, campaign)}


# --------------------------------------------------------------------------- #
# Produits fabriqués — ce que l'usine fait de la référence
# --------------------------------------------------------------------------- #

products = APIRouter(
    prefix="/campaigns/{campaign_id}/products", tags=["produits fabriqués"]
)


@products.get("", summary="À quel produit fabriqué chaque référence se rattache")
def list_products(campaign: CampaignDep, ctx: Ctx) -> dict[str, Any]:
    """Le tableau de rattachement, et ce qu'il pèse par produit.

    Les deux ensemble : la grille seule ne dit pas combien de références
    restent sans produit, et c'est la première question sur cinq cents.
    """
    return ProductService(ctx).overview(campaign)


@products.delete("", summary="Détacher toutes les références")
def clear_all_products(campaign: CampaignDep, ctx: Ctx) -> dict[str, int]:
    """Repartir de zéro. Le chargement fusionne entre les références, donc ceci
    est la seule façon de tout défaire d'un coup — et c'est explicite plutôt
    qu'implicite dans un fichier vide."""
    return {"removed": clear_products(ctx, campaign)}
