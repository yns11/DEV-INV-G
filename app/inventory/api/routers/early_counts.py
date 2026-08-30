"""Comptages avancés : journaux ERP, périmètres, lots, dérives.

Compter certains emplacements avant le jour J, sans éclater preuves, écarts et
analyses entre plusieurs campagnes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...domain.enums import LabelResolution
from ...domain.models import LocationKey
from ...services import DriftService, EarlyCountService
from ..deps import CampaignDep, drift_service, early_count_service
from ..responses import (
    DriftResponse,
    DriftsResolved,
    ErpJournalResponse,
    LabelAlert,
    RecountedInPlace,
    RescanLocation,
    ScopeCandidate,
    ScopeDeclared,
)
from ..schemas import (
    DriftResolutionRequest,
    JournalScopeRequest,
    LabelDecisionRequest,
    UnsealRequest,
)

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
    """``ERP@J − physique@T0``, par article et emplacement scellé.

    Attendue nulle. ``blocksAnalysis`` marque celles qui arrêtent le passage en
    analyse tant que personne n'a dit laquelle des deux quantités fait foi.
    """
    return [
        {
            **drift.model_dump(mode="json"),
            "driftQty": float(drift.drift_qty),
            "isResolved": drift.is_resolved,
            "blocksAnalysis": drift.blocks_analysis,
        }
        for drift in service.list_drifts(campaign.id)
    ]


@router.post(
    "/drifts/resolve",
    summary="Trancher des dérives",
    responses={200: {"model": DriftsResolved}},
)
def resolve_drifts(
    campaign: CampaignDep, service: Drift, payload: DriftResolutionRequest
) -> DriftsResolved:
    """Quelle quantité fait foi au jour J ?

    Deux réponses : conserver le comptage avancé — avec une cause, parce que la
    campagne et l'ERP resteront alors en désaccord — ou recompter, ce qui rend
    l'emplacement au comptage général.
    """
    return {
        "resolved": service.resolve(
            campaign,
            payload.drift_ids,
            payload.resolution,
            cause_code=payload.cause_code,
            comment=payload.comment,
        )
    }


# ------------------------------------------------------------------ étiquettes


@router.get(
    "/label-alerts",
    summary="Étiquettes scellées comptées ailleurs",
    responses={200: {"model": list[LabelAlert]}},
)
def label_alerts(campaign: CampaignDep, service: Early) -> list[LabelAlert]:
    """Le seul contrôle qui descende au grain de l'étiquette.

    Il rattrape ce que la dérive ne voit pas : une pièce sortie d'un emplacement
    scellé sans aucune transaction ERP laisse une dérive nulle, mais si elle est
    re-scannée ailleurs, son étiquette apparaît dans un second journal.
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


@router.post(
    "/label-alerts/decide",
    summary="Dire où est la pièce",
    responses={200: {"model": LabelAlert}},
)
def decide_label(
    campaign: CampaignDep, service: Early, payload: LabelDecisionRequest
) -> LabelAlert:
    """Trois issues, et chacune agit sur les quantités.

    La mettre au nouvel emplacement retire l'étiquette de l'emplacement scellé ;
    l'en enlever retire la ligne de l'autre journal ; la signaler ne retire
    rien et met l'emplacement scellé sur la liste de ceux à rescanner.
    """
    decision = service.decide_label(
        campaign,
        label_id=payload.label_id,
        item_number=payload.item_number,
        decision=LabelResolution(payload.decision),
        sealed=LocationKey(
            warehouse_id=payload.sealed_warehouse_id,
            location_id=payload.sealed_location_id,
        ),
        other=LocationKey(
            warehouse_id=payload.other_warehouse_id,
            location_id=payload.other_location_id,
        ),
        comment=payload.comment,
    )
    return {
        "labelId": decision.label_id,
        "itemNumber": decision.item_number,
        "sealedWarehouseId": decision.sealed_warehouse_id,
        "sealedLocationId": decision.sealed_location_id,
        "otherWarehouseId": decision.other_warehouse_id,
        "otherLocationId": decision.other_location_id,
        "otherJournalNumber": "",
        "otherQtyCounted": 0.0,
        "decision": str(decision.decision),
        "comment": decision.comment,
        "decidedBy": decision.decided_by,
    }


@router.get(
    "/to-rescan",
    summary="Emplacements à desceller et rescanner",
    responses={200: {"model": list[RescanLocation]}},
)
def to_rescan(campaign: CampaignDep, service: Early) -> list[RescanLocation]:
    """Les emplacements scellés dont une étiquette reste en question.

    Ceux que l'issue « signaler » désigne : on n'a pas voulu trancher sur pièce,
    et la façon d'en sortir est d'aller recompter. C'est l'ancien emplacement —
    le scellé — qu'il faut desceller pour que le jour J le reprenne.
    """
    return service.locations_to_rescan(campaign.id)
