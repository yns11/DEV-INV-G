"""Analysis endpoints: variances, KPIs, controls, analytics, AI and adjustments."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ...db import new_id
from ...domain.enums import AuditAction
from ...domain.models import AdjustmentLine
from ...services import AnalysisService
from ..deps import CampaignDep, Ctx, analysis_service
from ..schemas import AdjustmentRowRequest, AnalysisRequest

router = APIRouter(prefix="/campaigns/{campaign_id}/analysis", tags=["analyse"])

Service = Annotated[AnalysisService, Depends(analysis_service)]


@router.get("/kpis", summary="Indicateurs de la campagne")
def kpis(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Headline figures.

    Three distinct reliability measures are returned, because they answer three
    different questions and conflating them is how a steering committee ends up
    with a number nobody can defend:

    * ``netReliabilityValue``   — offsets allowed (did we gain or lose overall?)
    * ``grossReliabilityValue`` — absolute errors (how much did we get wrong?)
    * ``ira``                   — share of records within tolerance (WMS standard)
    """
    return service.kpis(campaign).as_dict()


@router.get("/variances", summary="Liste des écarts")
def variances(
    campaign: CampaignDep,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100_000)] = 200,
    material_only: Annotated[bool, Query(alias="materialOnly")] = False,
    granularity: Annotated[str, Query(pattern="^(item|item_location)$")] = "item",
) -> list[dict[str, Any]]:
    """The exception list.

    ``granularity=item`` is the financial view (a transfer between two bins is
    not a variance); ``item_location`` is the operational view (which bin to go
    and recount).
    """
    return service.top_variances(
        campaign, limit=limit, material_only=material_only, granularity=granularity
    )


@router.get("/aggregate", summary="Écarts agrégés par dimension")
def aggregate(
    campaign: CampaignDep,
    service: Service,
    dimension: Annotated[str, Query()] = "item_type",
    limit: Annotated[int, Query(ge=1, le=5000)] = 200,
) -> list[dict[str, Any]]:
    return service.aggregate(campaign, dimension, limit=limit)


@router.get("/transfers", summary="Part de l'écart qui n'est qu'un transfert")
def transfers(
    campaign: CampaignDep,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    """How much of the variance cancels out between two bins of one reference.

    A pallet moved from one location to another appears twice in the
    per-location view — short here, over there — and drags the IRA down without
    a single part having been lost. This endpoint measures that part, which is
    why the analysis screen opens on the per-reference reading rather than on
    the reference/location one.
    """
    return service.transfers(campaign, limit=limit)


@router.get("/pareto", summary="Courbe de Pareto des écarts")
def pareto(
    campaign: CampaignDep,
    service: Service,
    coverage: Annotated[float, Query(ge=0.1, le=1.0)] = 0.8,
) -> list[dict[str, Any]]:
    """The shortest article list covering *coverage* of the absolute variance."""
    return service.pareto(campaign, coverage=coverage)


@router.get("/controls", summary="Contrôles de cohérence")
def controls(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    return service.controls(campaign)


@router.get("/breakdown/{item_number}", summary="D'où vient un chiffre")
def breakdown(
    campaign: CampaignDep,
    item_number: str,
    service: Service,
    aspect: Annotated[str, Query()] = "counted",
    warehouse_id: Annotated[str, Query(alias="warehouseId")] = "",
    location_id: Annotated[str, Query(alias="locationId")] = "",
) -> dict[str, Any]:
    """The lines behind one figure, whichever figure it is.

    Only the WIP column could be explored; every other quantity had to be taken
    on trust. One endpoint, one shape — origin, place, detail, quantity, value —
    so a single dialog serves the consolidated journal, the variances, the root
    causes and the adjustments instead of four that would drift apart.
    """
    return service.breakdown(
        campaign,
        item_number,
        aspect,
        warehouse_id=warehouse_id,
        location_id=location_id,
    )


@router.get("/alerts", summary="Compteurs d'alertes pour la navigation")
def alerts(campaign: CampaignDep, service: Service) -> dict[str, int]:
    """How many *distinct* things are wrong, per screen.

    Distinct, not total: a control firing on four hundred articles is one thing
    to go and look at, and a badge reading « 400 » next to « Contrôles » says
    nothing except that the number is large. What the sidebar has to answer is
    "is there something here I have not seen?", and that is a count of controls.

    Its own endpoint rather than a field of the overview: computing it runs the
    whole control suite, and the overview is fetched on every screen of every
    page load.
    """
    return service.alert_counts(campaign)


@router.get("/analytics", summary="Analyses statistiques et machine learning")
def analytics(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """ABC/XYZ, Pareto, anomalies, clustering, recount priority, data forensics.

    Assembled in one call because every block shares the same reconciled frame;
    computing them separately would redo the reconciliation four times.
    """
    return service.analytics(campaign)


@router.get("/compare", summary="Comparer avec une campagne précédente")
def compare(
    campaign: CampaignDep,
    service: Service,
    other_campaign_id: Annotated[str, Query(alias="otherCampaignId")],
) -> dict[str, Any]:
    """Article-by-article comparison with a previous campaign.

    When the ERP movements between the two count dates are loaded, the
    bookkeeping identity ``book_now == book_then + movements`` is checked too,
    and the drift reported.
    """
    return service.compare(campaign, other_campaign_id)


@router.get("/causes", summary="Référentiel des causes standard")
def causes(campaign: CampaignDep, ctx: Ctx) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") for c in ctx.analysis.list_causes()]


@router.get("/cause-split", summary="Répartition des écarts par cause")
def cause_split(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Variance value by assigned root cause, unassigned share included."""
    return service.cause_split(campaign)


@router.put("/variances/{item_number}", summary="Affecter une cause à un écart")
def save_analysis(
    campaign: CampaignDep,
    item_number: str,
    payload: AnalysisRequest,
    service: Service,
) -> dict[str, Any]:
    analysis = service.save_analysis(
        campaign,
        item_number=item_number,
        cause_code=payload.cause_code,
        comment=payload.comment,
        accepted=payload.accepted,
    )
    return analysis.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# AI
# --------------------------------------------------------------------------- #

@router.post("/ai/suggest-causes", summary="Proposer des causes par IA")
def suggest_causes(
    campaign: CampaignDep,
    service: Service,
    max_items: Annotated[int, Query(ge=1, le=100, alias="maxItems")] = 40,
) -> dict[str, int]:
    """Propose a root cause for the largest variances.

    Proposals are stored beside the human decision, never instead of it: an
    analyst still has to accept one for it to become the campaign's answer.
    """
    return {"suggestions": service.suggest_causes(campaign, max_items=max_items)}


@router.get("/ai/summary", summary="Synthèse IA de la campagne")
def ai_summary(campaign: CampaignDep, service: Service) -> dict[str, str]:
    """A directors'-committee narrative grounded in the computed figures."""
    return {"markdown": service.narrative(campaign)}


@router.get("/ai/explain/{item_number}", summary="Expliquer un écart")
def explain(
    campaign: CampaignDep, item_number: str, service: Service
) -> dict[str, Any]:
    return service.explain(campaign, item_number.strip().upper())


# --------------------------------------------------------------------------- #
# Adjustments
# --------------------------------------------------------------------------- #

@router.get("/adjustments", summary="Mouvements et ajustements")
def list_adjustments(
    campaign: CampaignDep,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=50_000)] = 1000,
) -> list[dict[str, Any]]:
    return [
        {
            **a.model_dump(mode="json"),
            "qty": float(a.qty),
            "value": float(a.value),
        }
        for a in ctx.adjustments.list(campaign.id, limit=limit)
    ]


@router.put("/adjustments", summary="Créer ou modifier des ajustements")
def upsert_adjustments(
    campaign: CampaignDep, rows: list[AdjustmentRowRequest], ctx: Ctx
) -> dict[str, int]:
    ctx.guard(campaign, "adjustments")
    lines = [
        AdjustmentLine(
            id=row.id or new_id(),
            campaign_id=campaign.id,
            item_number=row.item_number,
            warehouse_id=row.warehouse_id,
            location_id=row.location_id,
            kind=row.kind,
            qty=row.qty,
            unit=row.unit,
            value=row.value,
            journal_number=row.journal_number,
            physical_date=row.physical_date,
            reason_code=row.reason_code,
            comment=row.comment,
            source="MANUAL",
        )
        for row in rows
    ]
    written = ctx.adjustments.upsert(lines, actor=ctx.actor)
    ctx.record(
        campaign_id=campaign.id,
        action=AuditAction.UPDATE,
        entity_type="adjustment_line",
        summary=f"{len(lines)} ajustement(s) enregistré(s) manuellement",
        after={"count": len(lines)},
    )
    return {"written": written}


@router.delete("/adjustments/{line_id}", summary="Supprimer un ajustement")
def delete_adjustment(
    campaign: CampaignDep, line_id: str, ctx: Ctx
) -> dict[str, bool]:
    ctx.guard(campaign, "adjustments")
    ctx.adjustments.delete(line_id, actor=ctx.actor)
    ctx.record(
        campaign_id=campaign.id,
        action=AuditAction.DELETE,
        entity_type="adjustment_line",
        entity_id=line_id,
        summary="Suppression logique d'un ajustement",
    )
    return {"deleted": True}
