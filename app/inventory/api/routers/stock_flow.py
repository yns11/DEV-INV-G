"""Stock-flow reconciliation between two campaigns.

One resource — the *run* — pairing this campaign with an earlier one, and four
things you do to it: choose the baseline, load the three quantities, refresh the
two ERP measures, read the report.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from ...domain.enums import FlowKind
from ...errors import ValidationError
from ...services import ImportService, StockFlowService
from ..deps import CampaignDep, Ctx, import_service
from ..schemas import PasteRequest, RowsRequest, StockFlowRunRequest

router = APIRouter(
    prefix="/campaigns/{campaign_id}/stock-flow", tags=["réconciliation"]
)


def stock_flow_service(ctx: Ctx) -> StockFlowService:
    return StockFlowService(ctx)


Service = Annotated[StockFlowService, Depends(stock_flow_service)]
#: The dry run goes through the generic importer: « see it before you commit it »
#: is the loop that matters, and it must be the same one everywhere.
ImporterDep = Annotated[ImportService, Depends(import_service)]


def _kind(value: str) -> FlowKind:
    """The step being loaded, refused rather than defaulted when unknown.

    Defaulting would silently file a shipment as a receipt — a sign error on a
    whole file, invisible on screen and worth twice the quantity it carries.
    """
    try:
        return FlowKind[value.strip().upper()]
    except KeyError:
        raise ValidationError(
            f"Étape de chargement inconnue : {value!r}.",
            allowed=[str(k) for k in FlowKind],
        ) from None


@router.get("/candidates", summary="Campagnes comparables")
def candidates(campaign: CampaignDep, service: Service) -> list[dict[str, Any]]:
    """The campaigns counted before this one, most recent first."""
    return service.comparable_campaigns(campaign)


@router.get("", summary="Comparaisons ouvertes")
def list_runs(campaign: CampaignDep, service: Service) -> list[dict[str, Any]]:
    return service.list_runs(campaign)


@router.post("", summary="Ouvrir une comparaison")
def open_run(
    campaign: CampaignDep, payload: StockFlowRunRequest, service: Service
) -> dict[str, Any]:
    """Pair this campaign with an earlier one and derive the period.

    The bounds are not asked for: they *are* the two count dates, snapped to
    their ISO Mondays. Letting them be typed would allow a period that does not
    match the inventories it compares, which is the one thing this report cannot
    survive.
    """
    run = service.open_run(campaign, payload.baseline_campaign_id)
    return service.report(campaign, run.id)["run"]


@router.delete("/{run_id}", summary="Supprimer une comparaison")
def delete_run(
    campaign: CampaignDep, run_id: str, service: Service
) -> dict[str, bool]:
    service.delete_run(campaign, run_id)
    return {"deleted": True}


@router.get("/{run_id}", summary="Rapport de comparaison")
def report(campaign: CampaignDep, run_id: str, service: Service) -> dict[str, Any]:
    """Header, KPIs, the flow chain and one line per article."""
    return service.report(campaign, run_id)


@router.post("/{run_id}/erp", summary="Lire production et consommation théorique")
def refresh_erp(
    campaign: CampaignDep, run_id: str, service: Service
) -> dict[str, Any]:
    return service.refresh_erp(campaign, run_id)


@router.post("/{run_id}/scrap/skip", summary="Ignorer l'étape des rebuts")
def skip_scrap(
    campaign: CampaignDep, run_id: str, service: Service
) -> dict[str, Any]:
    """Record that scrap was deliberately left out, rather than forgotten."""
    run = service.skip_scrap(campaign, run_id)
    return {"scrapLoaded": run.scrap_loaded}


# --------------------------------------------------------------------------- #
# Loading the three quantities
# --------------------------------------------------------------------------- #
#
# Three input modes, same shape as every other grid in the application: a file,
# a clipboard paste, or rows typed into the grid. Written as three thin routes
# over one service method so the validation, the summary and the audit entry
# cannot drift between them.

@router.post("/{run_id}/inputs/{kind}", summary="Charger un fichier de quantités")
async def load_file(
    campaign: CampaignDep,
    run_id: str,
    kind: str,
    service: Service,
    importer: ImporterDep,
    file: Annotated[UploadFile, File()],
    sheet: Annotated[str | None, Form()] = None,
    dry_run: Annotated[bool, Form(alias="dryRun")] = False,
) -> dict[str, Any]:
    payload = await file.read()
    kwargs: dict[str, Any] = {
        "mode": "file", "payload": payload,
        "filename": file.filename or "", "sheet": sheet,
    }
    if dry_run:
        return importer.preview("stock_flow", **kwargs)
    return service.load_inputs(campaign, run_id, _kind(kind), **kwargs).as_dict()


@router.post(
    "/{run_id}/inputs/{kind}/paste", summary="Coller des quantités depuis Excel"
)
def load_paste(
    campaign: CampaignDep,
    run_id: str,
    kind: str,
    payload: PasteRequest,
    service: Service,
    importer: ImporterDep,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"mode": "paste", "text": payload.text}
    if payload.dry_run:
        return importer.preview("stock_flow", **kwargs)
    return service.load_inputs(campaign, run_id, _kind(kind), **kwargs).as_dict()


@router.post("/{run_id}/inputs/{kind}/rows", summary="Enregistrer des quantités saisies")
def load_rows(
    campaign: CampaignDep,
    run_id: str,
    kind: str,
    payload: RowsRequest,
    service: Service,
    importer: ImporterDep,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"mode": "rows", "rows": payload.rows}
    if payload.dry_run:
        return importer.preview("stock_flow", **kwargs)
    return service.load_inputs(campaign, run_id, _kind(kind), **kwargs).as_dict()


@router.get("/{run_id}/inputs/{kind}", summary="Quantités chargées d'une étape")
def list_inputs(
    campaign: CampaignDep,
    run_id: str,
    kind: str,
    ctx: Ctx,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50_000)] = 20_000,
) -> list[dict[str, Any]]:
    wanted = _kind(kind)
    # Resolved through the service so a run belonging to another campaign is a
    # 404 here as well as everywhere else.
    run = service.report(campaign, run_id)["run"]
    return [
        {
            "item_number": entry.item_number,
            "qty": float(entry.qty),
            "unit": entry.unit,
        }
        for entry in ctx.stock_flow.list_inputs(run["id"])
        if entry.kind is wanted
    ][:limit]
