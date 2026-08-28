"""Comptages avancés : journaux ERP, périmètres, lots, dérives.

Compter certains emplacements avant le jour J, sans éclater preuves, écarts et
analyses entre plusieurs campagnes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...domain.models import LocationKey
from ...services import DriftService, EarlyCountService
from ..deps import CampaignDep, drift_service, early_count_service
from ..responses import (
    DriftResponse,
    DriftsResolved,
    EarlyBatchResponse,
    ErpJournalResponse,
    LabelAlert,
    ScopeCandidate,
    ScopeDeclared,
)
from ..schemas import (
    DriftResolutionRequest,
    EarlyBatchRequest,
    JournalScopeRequest,
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


# ----------------------------------------------------------------------- lots


@router.get(
    "/batches",
    summary="Lister les lots de comptage avancé",
    responses={200: {"model": list[EarlyBatchResponse]}},
)
def list_batches(campaign: CampaignDep, service: Early) -> list[EarlyBatchResponse]:
    return [
        {
            **batch.model_dump(mode="json", exclude={"locations"}),
            "locations": [
                {"warehouseId": k.warehouse_id, "locationId": k.location_id}
                for k in batch.locations
            ],
            "isClosed": batch.is_closed,
            "isSealed": batch.is_sealed,
        }
        for batch in service.list_batches(campaign.id)
    ]


@router.post(
    "/batches",
    summary="Ouvrir un lot de comptage avancé",
    status_code=201,
    responses={201: {"model": EarlyBatchResponse}},
)
def create_batch(
    campaign: CampaignDep, service: Early, payload: EarlyBatchRequest
) -> EarlyBatchResponse:
    batch = service.create_batch(
        campaign,
        code=payload.code,
        label=payload.label,
        counted_on=payload.counted_on,
        erp_journal_ids=payload.erp_journal_ids,
    )
    return batch.model_dump(mode="json", exclude={"locations"})


@router.post(
    "/batches/{batch_id}/close",
    summary="Clore un lot",
    responses={200: {"model": EarlyBatchResponse}},
)
def close_batch(
    campaign: CampaignDep, service: Early, batch_id: str
) -> EarlyBatchResponse:
    batch = service.close_batch(campaign, batch_id)
    return batch.model_dump(mode="json", exclude={"locations"})


@router.post(
    "/batches/{batch_id}/seal",
    summary="Sceller un lot",
    responses={200: {"model": EarlyBatchResponse}},
)
def seal_batch(
    campaign: CampaignDep, service: Early, batch_id: str
) -> EarlyBatchResponse:
    """Poser la référence des emplacements du lot, et interdire qu'on y touche.

    Refusé si l'un des journaux du périmètre n'est pas posté dans l'ERP : c'est
    le postage qui réaligne l'ERP sur le physique compté, et le scellement tient
    ce réalignement pour acquis.
    """
    batch = service.seal_batch(campaign, batch_id)
    return batch.model_dump(mode="json", exclude={"locations"})


@router.post(
    "/batches/{batch_id}/unseal",
    summary="Desceller un lot",
    responses={200: {"model": EarlyBatchResponse}},
)
def unseal_batch(
    campaign: CampaignDep, service: Early, batch_id: str, payload: UnsealRequest
) -> EarlyBatchResponse:
    batch = service.unseal_batch(campaign, batch_id, reason=payload.reason)
    return batch.model_dump(mode="json", exclude={"locations"})


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
