"""Grid contracts, referentials and every bulk-import endpoint.

One route shape serves all three input modes so the frontend grid component is
written once: ``POST /campaigns/{id}/import/{target}`` accepts a multipart file,
and ``.../paste`` and ``.../rows`` accept the same target with JSON bodies.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from ...errors import ValidationError
from ...ingest import get_contract, list_contracts
from ...services import ImportService
from ..deps import CampaignDep, Ctx, import_service
from ..schemas import PasteRequest, RowsRequest

router = APIRouter(tags=["données"])

Importer = Annotated[ImportService, Depends(import_service)]

#: Import targets and the service method that handles each. Declaring the map
#: here keeps the routes thin and makes an unsupported target a clean 422.
_TARGETS = {
    "items": "import_items",
    "boms": "import_boms",
    "book_stock": "import_book_stock",
    "count_journal_lines": "import_journal_lines",
    "count_sheets": "import_count_sheets",
    "adjustments": "import_adjustments",
    "locations": "import_locations",
}


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #

@router.get("/contracts", summary="Contrats de colonnes des grilles")
def contracts() -> list[dict[str, Any]]:
    """The column contract of every importable grid.

    The frontend renders its empty grids from this, so the header a user sees is
    by construction the header the parser expects.
    """
    return list_contracts()


@router.get("/contracts/{contract_key}", summary="Contrat d'une grille")
def contract(contract_key: str) -> dict[str, Any]:
    try:
        return get_contract(contract_key).as_dict()
    except KeyError as exc:
        raise ValidationError(str(exc), contract=contract_key) from exc


# --------------------------------------------------------------------------- #
# Imports
# --------------------------------------------------------------------------- #

@router.post(
    "/campaigns/{campaign_id}/import/{target}",
    summary="Importer un fichier dans une grille",
)
async def import_file(
    campaign: CampaignDep,
    target: str,
    importer: Importer,
    file: Annotated[UploadFile, File()],
    sheet: Annotated[str | None, Form()] = None,
    replace: Annotated[bool, Form()] = False,
    dry_run: Annotated[bool, Form(alias="dryRun")] = False,
) -> dict[str, Any]:
    """Upload a ``.xlsx`` / ``.csv`` into a grid.

    With ``dryRun=true`` nothing is written: the response reports exactly what
    *would* happen, which is how a user checks a file before committing to it.
    """
    method = _resolve(target)
    payload = await file.read()
    kwargs: dict[str, Any] = {
        "mode": "file",
        "payload": payload,
        "filename": file.filename or "",
        "sheet": sheet,
    }

    if dry_run:
        return importer.preview(target, **kwargs)

    duplicate = importer.check_duplicate(campaign.id, target, **kwargs)
    extra = {"replace": replace} if target == "boms" else {}
    outcome = getattr(importer, method)(campaign, **kwargs, **extra)
    result = outcome.as_dict()
    if duplicate:
        result["duplicateOf"] = {
            "importedAt": duplicate["imported_at"].isoformat(),
            "importedBy": duplicate["imported_by"],
            "filename": duplicate["filename"],
            "rowsAccepted": duplicate["rows_accepted"],
        }
    return result


@router.post(
    "/campaigns/{campaign_id}/import/{target}/paste",
    summary="Importer un collage depuis Excel",
)
def import_paste(
    campaign: CampaignDep, target: str, payload: PasteRequest, importer: Importer
) -> dict[str, Any]:
    """Accept a Ctrl-C / Ctrl-V block pasted into a grid cell."""
    method = _resolve(target)
    kwargs: dict[str, Any] = {"mode": "paste", "text": payload.text}
    if payload.dry_run:
        return importer.preview(target, **kwargs)
    extra = {"replace": payload.replace} if target == "boms" else {}
    return getattr(importer, method)(campaign, **kwargs, **extra).as_dict()


#: The two grids the ERP is authoritative for. Book stock deliberately stays a
#: file: it is a snapshot taken at a precise instant, and reading it live would
#: give a picture of "now" rather than of the moment the count began.
ERP_TARGETS = ("items", "boms")


@router.get("/erp/source", summary="État de la source ERP")
def erp_source() -> dict[str, Any]:
    """Whether an ERP read is possible, and from which tables.

    The screen offers the option or explains its absence with this; a button
    that can only fail is worse than no button.
    """
    from ...config import get_settings
    from ...ingest.erp import (
        MIRROR_BOM_TABLE,
        MIRROR_ITEMS_TABLE,
        erp_available,
        mirror_state,
        reading_from_mirror,
        unavailable_reason,
    )

    settings = get_settings()
    mirrored = reading_from_mirror()
    return {
        "available": erp_available(),
        "reason": unavailable_reason(),
        "source": settings.erp_source,
        "tables": (
            {"items": MIRROR_ITEMS_TABLE, "boms": MIRROR_BOM_TABLE}
            if mirrored
            else {"items": settings.erp_items_fqn, "boms": settings.erp_bom_fqn}
        ),
        # How old the copy is. Absent when reading the ERP live, where the
        # question does not arise.
        "mirror": mirror_state() if mirrored and erp_available() else None,
    }


@router.post(
    "/campaigns/{campaign_id}/import/{target}/erp",
    summary="Importer depuis les tables ERP",
)
def import_erp(
    campaign: CampaignDep,
    target: str,
    importer: Importer,
    dry_run: Annotated[bool, Query(alias="dryRun")] = False,
    replace: Annotated[bool, Query()] = False,
    approved_only: Annotated[bool, Query(alias="approvedOnly")] = False,
) -> dict[str, Any]:
    """Read the referential straight from the ERP silver tables.

    Joins the pipeline at the same point as a spreadsheet: the rows are
    validated, previewed and mapped identically, and the grid stays editable
    afterwards. What changes is only that nobody had to export and re-import a
    file — which is where most referential errors came from.
    """
    if target not in ERP_TARGETS:
        raise ValidationError(
            f"La grille « {target} » n'a pas de source ERP.",
            allowed=list(ERP_TARGETS),
        )
    method = _resolve(target)
    kwargs: dict[str, Any] = {"mode": "erp", "approved_only": approved_only}
    if dry_run:
        return importer.preview(target, **kwargs)
    extra = {"replace": replace} if target == "boms" else {}
    return getattr(importer, method)(campaign, **kwargs, **extra).as_dict()


@router.post(
    "/campaigns/{campaign_id}/import/{target}/rows",
    summary="Enregistrer des lignes saisies dans la grille",
)
def import_rows(
    campaign: CampaignDep, target: str, payload: RowsRequest, importer: Importer
) -> dict[str, Any]:
    method = _resolve(target)
    kwargs: dict[str, Any] = {"mode": "rows", "rows": payload.rows}
    if payload.dry_run:
        return importer.preview(target, **kwargs)
    extra = {"replace": payload.replace} if target == "boms" else {}
    return getattr(importer, method)(campaign, **kwargs, **extra).as_dict()


# --------------------------------------------------------------------------- #
# Referential reads
# --------------------------------------------------------------------------- #

@router.get("/campaigns/{campaign_id}/items", summary="Référentiel articles")
def list_items(
    campaign: CampaignDep,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=20_000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    items = ctx.referentials.list_items(campaign.id)
    if search:
        needle = search.strip().upper()
        items = [
            i for i in items
            if needle in i.item_number or needle in i.name.upper()
            or needle in i.search_name.upper()
        ]
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": [
            {
                **i.model_dump(mode="json"),
                "exclusions": sorted(str(e) for e in i.exclusions),
                "stdPrice": float(i.std_price),
            }
            for i in page
        ],
    }


@router.delete(
    "/campaigns/{campaign_id}/items/{item_number}", summary="Supprimer un article"
)
def delete_item(campaign: CampaignDep, item_number: str, ctx: Ctx) -> dict[str, bool]:
    ctx.guard(campaign, "items")
    ctx.referentials.delete_item(campaign.id, item_number, actor=ctx.actor)
    ctx.record(
        campaign_id=campaign.id,
        action="DELETE",
        entity_type="item",
        entity_id=item_number,
        summary=f"Suppression logique de l'article {item_number}",
    )
    return {"deleted": True}


@router.get("/campaigns/{campaign_id}/boms", summary="Nomenclatures")
def list_boms(
    campaign: CampaignDep,
    ctx: Ctx,
    parent: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    links = ctx.referentials.list_bom_links(campaign.id)
    if parent:
        needle = parent.strip().upper()
        links = [l for l in links if l.parent_item == needle]
    return [
        {**l.model_dump(mode="json"), "qtyPer": float(l.qty_per)} for l in links
    ]


@router.get("/campaigns/{campaign_id}/bom-health", summary="Santé des nomenclatures")
def bom_health(campaign: CampaignDep, ctx: Ctx) -> dict[str, Any]:
    """Cycles, orphan links and assemblies without a structure.

    Surfaced as its own endpoint because a BOM defect discovered on the day of
    the inventory costs a whole afternoon; discovered in preparation, it costs
    ten minutes.
    """
    from ...domain.bom import BomIndex
    from ...domain.controls import check_referentials, summarise

    items = ctx.referentials.items_by_number(campaign.id)
    links = ctx.referentials.list_bom_links(campaign.id)
    index = BomIndex(links)
    findings = check_referentials(items=items, bom_links=links, bom_index=index)
    return {
        "linkCount": len(index),
        "parentCount": len(index.parents),
        "cycles": [" → ".join(c) for c in index.find_cycles()],
        "summary": summarise(findings),
        "findings": [f.model_dump(mode="json") for f in findings],
    }


@router.get("/campaigns/{campaign_id}/book-stock", summary="Stock livre")
def book_stock(
    campaign: CampaignDep,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=20_000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    lines = ctx.book_stock.list(campaign.id)
    return {
        "total": len(lines),
        "frozenAt": campaign.book_stock_frozen_at.isoformat()
        if campaign.book_stock_frozen_at else None,
        "rows": [
            {
                **l.model_dump(mode="json"),
                "qty": float(l.qty),
                "unitCost": float(l.unit_cost),
                "value": float(l.value),
            }
            for l in lines[offset : offset + limit]
        ],
    }


@router.post("/campaigns/{campaign_id}/book-stock/freeze", summary="Geler le stock livre")
def freeze_book_stock(campaign: CampaignDep, importer: Importer) -> dict[str, Any]:
    return importer.freeze_book_stock(campaign).model_dump(mode="json")


@router.get("/campaigns/{campaign_id}/locations", summary="Entrepôts et emplacements")
def list_locations(campaign: CampaignDep, ctx: Ctx) -> dict[str, Any]:
    locations = ctx.referentials.list_locations(campaign.id)
    journals = {j.key: j for j in ctx.journals.list(campaign.id)}
    return {
        "warehouses": [
            w.model_dump(mode="json") for w in ctx.referentials.list_warehouses(campaign.id)
        ],
        "locations": [
            {
                **l.model_dump(mode="json"),
                "hasJournal": l.key in journals,
                "journalStatus": str(journals[l.key].status) if l.key in journals else None,
            }
            for l in locations
        ],
    }


def _resolve(target: str) -> str:
    method = _TARGETS.get(target)
    if method is None:
        raise ValidationError(
            f"Cible d'import inconnue : {target!r}.", allowed=sorted(_TARGETS)
        )
    return method
