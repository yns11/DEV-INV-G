"""Comptages avancés : journaux ERP, périmètres, lots, dérives.

Compter certains emplacements avant le jour J, sans éclater preuves, écarts et
analyses entre plusieurs campagnes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ...domain.models import LocationKey
from ...services import DriftService, EarlyCountService
from ..deps import CampaignDep, drift_service, early_count_service
from ..responses import (
    DriftResponse,
    ErpJournalLineResponse,
    ErpJournalResponse,
    LabelAlert,
    RecountedInPlace,
    ScopeCandidate,
    ScopeDeclared,
)
from ..schemas import JournalScopeRequest, UnsealRequest

router = APIRouter(
    prefix="/campaigns/{campaign_id}/early-counts", tags=["comptages avancés"]
)

Early = Annotated[EarlyCountService, Depends(early_count_service)]
Drift = Annotated[DriftService, Depends(drift_service)]


def _keys(payload: JournalScopeRequest) -> list[LocationKey]:
    return [
        LocationKey(warehouse_id=item.warehouse_id, location_id=item.location_id)
        for item in payload.locations
    ]


# --------------------------------------------------------------- journaux ERP


@router.get(
    "/journals",
    summary="Lister les journaux ERP importés",
    responses={200: {"model": list[ErpJournalResponse]}},
)
def list_erp_journals(campaign: CampaignDep, service: Early) -> list[ErpJournalResponse]:
    """Les journaux tels que l'ERP les tient, avec leur périmètre déclaré."""
    return service.list_journals(campaign.id)


@router.get(
    "/journals/{erp_journal_id}/lines",
    summary="Lignes brutes d'un journal ERP",
    responses={200: {"model": list[ErpJournalLineResponse]}},
)
def erp_journal_lines(
    campaign: CampaignDep, service: Early, erp_journal_id: str
) -> list[dict[str, Any]]:
    """Ce que l'ERP a réellement envoyé, ligne par ligne.

    L'application agrège vers l'emplacement ; l'agrégat ne dit pas d'où il
    vient. Chaque ligne porte donc son appartenance au périmètre déclaré —
    hors périmètre, elle est conservée comme trace d'un déplacement et **ne
    compte pas**.
    """
    return service.journal_lines(campaign.id, erp_journal_id)


@router.get(
    "/journals/{erp_journal_id}/scope-proposal",
    summary="Proposer les emplacements d'un journal",
    responses={200: {"model": list[ScopeCandidate]}},
)
def propose_scope(
    campaign: CampaignDep, service: Early, erp_journal_id: str
) -> list[ScopeCandidate]:
    """Les emplacements que ce journal *pourrait* couvrir.

    Ceux de ses lignes, moins le tampon, moins ceux déjà alloués à un autre
    journal, le plus probable en tête. L'application propose, l'utilisateur
    tranche : les emplacements des lignes ne suffisent pas à dire le périmètre.
    """
    return [
        {
            "warehouseId": row["warehouse_id"],
            "locationId": row["location_id"],
            "lineCount": int(row["line_count"]),
            "itemCount": int(row["item_count"]),
            "qtyOnHand": float(row["qty_on_hand"] or 0),
            "qtyCounted": float(row["qty_counted"] or 0),
        }
        for row in service.propose_scope(campaign, erp_journal_id)
    ]


@router.put(
    "/journals/{erp_journal_id}/scope",
    summary="Déclarer le périmètre d'un journal",
    responses={200: {"model": ScopeDeclared}},
)
def declare_scope(
    campaign: CampaignDep,
    service: Early,
    erp_journal_id: str,
    payload: JournalScopeRequest,
) -> ScopeDeclared:
    return {"locations": service.declare_scope(campaign, erp_journal_id, _keys(payload))}


# ----------------------------------------------------- descellement du journal


@router.post(
    "/journals/{erp_journal_id}/unseal",
    summary="Desceller un journal de précomptage",
    responses={200: {"model": ScopeDeclared}},
)
def unseal_journal(
    campaign: CampaignDep,
    service: Early,
    erp_journal_id: str,
    payload: UnsealRequest,
) -> ScopeDeclared:
    """Rendre ses emplacements au comptage général.

    Le périmètre part avec le scellement : sans périmètre, le journal n'a plus
    d'emplacement à couvrir. Redéclarer est le geste qui rescelle.
    """
    return {
        "locations": service.unseal(campaign, erp_journal_id, reason=payload.reason)
    }


# -------------------------------------------------------------------- dérives


@router.get(
    "/drifts",
    summary="Lister les dérives des emplacements scellés",
    responses={200: {"model": list[DriftResponse]}},
)
def list_drifts(campaign: CampaignDep, service: Drift) -> list[DriftResponse]:
    """``ERP@J − compté@T0``, par article et emplacement scellé.

    Attendue nulle, et seules les non nulles sont rendues. En affichage seul :
    un précomptage est posté dans l'ERP avant la photo du jour J, donc ce qui
    subsiste ici est ce qui a bougé entre les deux dates — rien à trancher, et
    rien qui bloque.
    """
    return [
        {**drift.model_dump(mode="json"), "driftQty": float(drift.drift_qty)}
        for drift in service.list_drifts(campaign.id)
    ]


# ------------------------------------------------------------------ étiquettes


@router.get(
    "/label-alerts",
    summary="Étiquettes scellées comptées ailleurs",
    responses={200: {"model": list[LabelAlert]}},
)
def label_alerts(campaign: CampaignDep, service: Early) -> list[LabelAlert]:
    """Le seul regard qui descende au grain de l'étiquette.

    Il montre ce que la dérive ne voit pas : une pièce sortie d'un emplacement
    scellé sans aucune transaction ERP laisse une dérive nulle, mais si elle est
    re-scannée ailleurs, son étiquette apparaît dans un second journal.

    En affichage seul. La liste n'exclut rien d'aucune agrégation et n'appelle
    aucune décision : elle dit ce qui a bougé entre le précomptage et le jour J,
    à qui veut aller voir.
    """
    return service.label_alerts(campaign.id)


@router.get(
    "/recounted-in-place",
    summary="Emplacements scellés recomptés par un second journal",
    responses={200: {"model": list[RecountedInPlace]}},
)
def recounted_in_place(
    campaign: CampaignDep, service: Early
) -> list[RecountedInPlace]:
    """Le pendant des étiquettes comptées ailleurs, et ce qui les en sort.

    Deux journaux sur le même emplacement scellé ne décrivent pas un
    déplacement : l'étiquette est là où elle doit être. Ils remplissaient
    pourtant la liste des étiquettes comptées ailleurs de lignes dont les deux
    colonnes d'emplacement portaient la même valeur. Ils sont ici, résumés, avec
    le journal retenu et celui qui ne l'est pas.
    """
    return service.labels_recounted_in_place(campaign.id)
