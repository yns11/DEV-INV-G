"""Grid contracts, referentials and every bulk-import endpoint.

One route shape serves all three input modes so the frontend grid component is
written once: ``POST /campaigns/{id}/import/{target}`` accepts a multipart file,
and ``.../paste`` and ``.../rows`` accept the same target with JSON bodies.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from ...errors import ValidationError
from ...ingest import get_contract, list_contracts
from ...services import ImportService, ReferentialService
from ..deps import CampaignDep, import_service, referential_service
from ..paging import MAX_PAGE, page
from ..responses import GridContractResponse
from ..schemas import (
    BomActivationRequest,
    BomLinkPatch,
    ItemExclusionsRequest,
    ItemPatch,
    PasteRequest,
    RowsRequest,
)
from ..uploads import offload, read_upload

router = APIRouter(tags=["données"])

#: Les grilles dont l'écriture **remplace** l'ensemble existant. Un rejet y
#: transforme une ligne manquante en ligne supprimée, ce que le service refuse
#: sans dérogation explicite. « boms » n'y figure pas : il ne remplace que
#: lorsqu'on le lui demande, et le service reçoit alors `replace`.
WHOLESALE_TARGETS = ("book_stock", "backflush")

Importer = Annotated[ImportService, Depends(import_service)]
Referentials = Annotated[ReferentialService, Depends(referential_service)]

def _write_options(target: str, *, replace: bool, allow_partial: bool) -> dict:
    """Les options d'écriture que ce chargement accepte.

    Passer `allow_partial` à un import qui ne remplace rien serait accepté par
    Python et sans effet, ce qui est pire qu'une erreur : le drapeau
    apparaîtrait dans l'interface sans jamais rien changer. Il n'est donc
    transmis qu'aux grilles pour lesquelles il veut dire quelque chose.
    """
    options: dict = {}
    if target == "boms":
        options["replace"] = replace
    if target in WHOLESALE_TARGETS or (target == "boms" and replace):
        options["allow_partial"] = allow_partial
    return options


#: Import targets and the service method that handles each. Declaring the map
#: here keeps the routes thin and makes an unsupported target a clean 422.
_TARGETS = {
    "items": "import_items",
    "boms": "import_boms",
    "book_stock": "import_book_stock",
    "count_journal_lines": "import_journal_lines",
    "count_sheets": "import_count_sheets",
    "adjustments": "import_adjustments",
    "backflush": "import_backflush",
    "locations": "import_locations",
}


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #

@router.get(
    "/contracts", summary="Contrats de colonnes des grilles",
    responses={200: {"model": list[GridContractResponse]}},
)
def contracts() -> list[dict[str, Any]]:
    """The column contract of every importable grid.

    The frontend renders its empty grids from this, so the header a user sees is
    by construction the header the parser expects.
    """
    return list_contracts()


@router.get(
    "/contracts/{contract_key}", summary="Contrat d'une grille",
    responses={200: {"model": GridContractResponse}},
)
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
    allow_partial: Annotated[bool, Form(alias="allowPartial")] = False,
    dry_run: Annotated[bool, Form(alias="dryRun")] = False,
    borne_debut: Annotated[dt.date | None, Query(alias="borneDebut")] = None,
    borne_fin: Annotated[dt.date | None, Query(alias="borneFin")] = None,
) -> dict[str, Any]:
    """Upload a ``.xlsx`` / ``.csv`` into a grid.

    With ``dryRun=true`` nothing is written: the response reports exactly what
    *would* happen, which is how a user checks a file before committing to it.
    """
    method = _resolve(target)
    payload = await read_upload(file)
    kwargs: dict[str, Any] = {
        "mode": "file",
        "payload": payload,
        "filename": file.filename or "",
        "sheet": sheet,
        **_period(target, borne_debut, borne_fin),
    }

    # `offload` : cet endpoint est `async` parce que la lecture du fichier
    # l'est, et FastAPI n'exécute dans un pool de fils que les endpoints `def`.
    # Un import de deux cent mille lignes tenu sur la boucle immobilise
    # l'application entière le temps qu'il dure.
    if dry_run:
        return await offload(lambda: importer.preview(target, **kwargs))

    duplicate = await offload(
        lambda: importer.check_duplicate(campaign.id, target, **kwargs)
    )
    extra = _write_options(target, replace=replace, allow_partial=allow_partial)
    outcome = await offload(
        lambda: getattr(importer, method)(campaign, **kwargs, **extra)
    )
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
    campaign: CampaignDep,
    target: str,
    payload: PasteRequest,
    importer: Importer,
    borne_debut: Annotated[dt.date | None, Query(alias="borneDebut")] = None,
    borne_fin: Annotated[dt.date | None, Query(alias="borneFin")] = None,
) -> dict[str, Any]:
    """Accept a Ctrl-C / Ctrl-V block pasted into a grid cell."""
    method = _resolve(target)
    kwargs: dict[str, Any] = {
        "mode": "paste", "text": payload.text,
        **_period(target, borne_debut, borne_fin),
    }
    if payload.dry_run:
        return importer.preview(target, **kwargs)
    extra = _write_options(
        target, replace=payload.replace, allow_partial=payload.allow_partial
    )
    return getattr(importer, method)(campaign, **kwargs, **extra).as_dict()


#: The grids the ERP is authoritative for. Book stock deliberately stays a file:
#: it is a snapshot taken at a precise instant, and reading it live would give a
#: picture of "now" rather than of the moment the count began.
ERP_TARGETS = ("items", "boms", "book_stock", "backflush")

#: Grids read from a *fact* table, which therefore need a period. A referential
#: has a state; a fact table has a history, and one cannot be read without
#: saying over what.
PERIOD_TARGETS = ("backflush",)


def _period(
    target: str, start: dt.date | None, end: dt.date | None
) -> dict[str, Any]:
    """The period arguments, only for the grids that take one.

    Passed as query parameters on all four input modes rather than added to the
    JSON bodies: the bounds qualify *the read*, not the payload, and a file
    upload has no body to put them in anyway. One shape for the four routes
    beats three that would drift.
    """
    if target not in PERIOD_TARGETS:
        return {}
    return {"period_start": start, "period_end": end}


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


@router.get("/erp/stock-dates", summary="Dates de snapshot de stock disponibles")
def erp_stock_dates() -> dict[str, Any]:
    """Les photos de stock que la source propose, la plus récente d'abord.

    Séparé de ``/erp/source`` parce que celui-ci est lu au chargement de chaque
    écran d'import, alors que cette liste n'intéresse que le Stock ERP — et
    qu'elle coûte une requête à la source.
    """
    from ...ingest.erp import ErpReader, erp_available

    if not erp_available():
        return {"dates": []}
    return {"dates": [d.isoformat() for d in ErpReader().stock_dates()]}


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
    allow_partial: Annotated[bool, Query(alias="allowPartial")] = False,
    borne_debut: Annotated[dt.date | None, Query(alias="borneDebut")] = None,
    borne_fin: Annotated[dt.date | None, Query(alias="borneFin")] = None,
    snapshot_date: Annotated[dt.date | None, Query(alias="dateSnapshot")] = None,
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
    kwargs: dict[str, Any] = {
        "mode": "erp",
        **_period(target, borne_debut, borne_fin),
        **({"snapshot_date": snapshot_date} if target == "book_stock" else {}),
    }
    if dry_run:
        return importer.preview(target, **kwargs)
    extra = _write_options(target, replace=replace, allow_partial=allow_partial)
    return getattr(importer, method)(campaign, **kwargs, **extra).as_dict()


@router.post(
    "/campaigns/{campaign_id}/import/{target}/rows",
    summary="Enregistrer des lignes saisies dans la grille",
)
def import_rows(
    campaign: CampaignDep,
    target: str,
    payload: RowsRequest,
    importer: Importer,
    borne_debut: Annotated[dt.date | None, Query(alias="borneDebut")] = None,
    borne_fin: Annotated[dt.date | None, Query(alias="borneFin")] = None,
) -> dict[str, Any]:
    method = _resolve(target)
    kwargs: dict[str, Any] = {
        "mode": "rows", "rows": payload.rows,
        **_period(target, borne_debut, borne_fin),
    }
    if payload.dry_run:
        return importer.preview(target, **kwargs)
    extra = _write_options(
        target, replace=payload.replace, allow_partial=payload.allow_partial
    )
    return getattr(importer, method)(campaign, **kwargs, **extra).as_dict()


# --------------------------------------------------------------------------- #
# Referential reads
#
# Ces routes traduisent, elles ne décident pas. Lire les paramètres, borner une
# page, rendre en JSON : le reste — la garde de phase, la comparaison avec
# l'existant, l'écriture, l'audit — appartient à ReferentialService, où une
# règle se vérifie sans construire une application HTTP.
# --------------------------------------------------------------------------- #

def _item_json(item: Any) -> dict[str, Any]:
    """Un article, dans la forme que la grille attend.

    Deux traductions, et toutes deux de sérialisation : un ensemble d'énumérés
    devient une liste triée pour que la réponse soit stable d'un appel à
    l'autre, et un ``Decimal`` devient un nombre parce que JSON n'en a pas.
    """
    return {
        **item.model_dump(mode="json"),
        "exclusions": sorted(str(e) for e in item.exclusions),
        "stdPrice": float(item.std_price),
    }


@router.get("/campaigns/{campaign_id}/items", summary="Référentiel articles")
def list_items(
    campaign: CampaignDep,
    service: Referentials,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query()] = None,
    counted: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """The campaign's articles, filtered server-side.

    ``counted=true`` keeps only the references that appear on a GENERIQUE
    counting sheet or in a counting journal.
    """
    items = service.list_items(campaign, search=search, counted=counted)
    return page(items, offset=offset, limit=limit, render=_item_json)


@router.patch(
    "/campaigns/{campaign_id}/items/{item_number}", summary="Modifier un article"
)
def update_item(
    campaign: CampaignDep, item_number: str, payload: ItemPatch, service: Referentials
) -> dict[str, Any]:
    """Correct one article without reloading the referential."""
    updated = service.update_item(
        campaign, item_number, payload.model_dump(exclude_none=True)
    )
    return _item_json(updated)


@router.post(
    "/campaigns/{campaign_id}/items/exclusions",
    summary="Exclure ou réintégrer un lot d'articles",
)
def set_item_exclusions(
    campaign: CampaignDep, payload: ItemExclusionsRequest, service: Referentials
) -> dict[str, Any]:
    """Apply one exclusion to a whole selection."""
    return service.set_item_exclusions(
        campaign, payload.item_numbers, payload.exclusions
    )


@router.delete(
    "/campaigns/{campaign_id}/items/{item_number}", summary="Supprimer un article"
)
def delete_item(
    campaign: CampaignDep, item_number: str, service: Referentials
) -> dict[str, bool]:
    service.delete_item(campaign, item_number)
    return {"deleted": True}


@router.get("/campaigns/{campaign_id}/boms", summary="Nomenclatures")
def list_boms(
    campaign: CampaignDep,
    service: Referentials,
    parent: Annotated[str | None, Query()] = None,
    counted: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 5000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Every edge, or only the ones a counted reference is on either side of.

    Paginé, et porteur du total : une nomenclature complète se compte en
    dizaines de milliers de liens, et la renvoyer entière pour en afficher
    trente était le seul appel de l'application capable de tenir une seconde à
    lui tout seul.
    """
    links, items = service.list_bom_links(campaign, parent=parent, counted=counted)
    return page(
        links,
        offset=offset,
        limit=limit,
        render=lambda l: {
            **l.model_dump(mode="json"),
            "qtyPer": float(l.qty_per),
            "parentName": items[l.parent_item].name if l.parent_item in items else "",
            "childName": items[l.child_item].name if l.child_item in items else "",
        },
    )


@router.post(
    "/campaigns/{campaign_id}/boms/activation",
    summary="Activer ou désactiver un lot de liens",
)
def set_bom_activation(
    campaign: CampaignDep, payload: BomActivationRequest, service: Referentials
) -> dict[str, Any]:
    """Put a batch of bill-of-materials edges in force, or retire them."""
    return service.set_bom_activation(campaign, payload.links, payload.active)


@router.patch("/campaigns/{campaign_id}/boms", summary="Modifier un lien de nomenclature")
def update_bom_link(
    campaign: CampaignDep, payload: BomLinkPatch, service: Referentials
) -> dict[str, Any]:
    """Correct the quantity or unit of one edge."""
    updated = service.update_bom_link(
        campaign,
        payload.parent_item,
        payload.child_item,
        payload.model_dump(exclude_none=True, exclude={"parent_item", "child_item"}),
    )
    return {**updated.model_dump(mode="json"), "qtyPer": float(updated.qty_per)}


@router.delete("/campaigns/{campaign_id}/boms", summary="Supprimer un lien")
def delete_bom_link(
    campaign: CampaignDep,
    service: Referentials,
    parent: Annotated[str, Query(min_length=1)],
    child: Annotated[str, Query(min_length=1)],
) -> dict[str, bool]:
    service.delete_bom_link(campaign, parent, child)
    return {"deleted": True}


@router.get("/campaigns/{campaign_id}/bom-health", summary="Santé des nomenclatures")
def bom_health(campaign: CampaignDep, service: Referentials) -> dict[str, Any]:
    """Cycles, orphan links and assemblies without a structure.

    Surfaced as its own endpoint because a BOM defect discovered on the day of
    the inventory costs a whole afternoon; discovered in preparation, it costs
    ten minutes.
    """
    return service.bom_health(campaign)


@router.get("/campaigns/{campaign_id}/book-stock", summary="Stock ERP")
def book_stock(
    campaign: CampaignDep,
    service: Referentials,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
    top: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> dict[str, Any]:
    """The ERP snapshot, and what the biggest lines of it weigh."""
    view = service.book_stock(campaign, top=top)
    return {
        "total": len(view.lines),
        "totalValue": view.total_value,
        # Part de la valeur portée par les lignes retenues. `null` sans filtre :
        # « 100 % » se lirait comme un résultat alors que c'est une tautologie.
        "topShare": view.top_share,
        "frozenAt": campaign.book_stock_frozen_at.isoformat()
        if campaign.book_stock_frozen_at else None,
        "rows": [
            {
                **l.model_dump(mode="json"),
                "qty": float(l.qty),
                "unitCost": float(l.unit_cost),
                "value": float(l.value),
            }
            for l in view.lines[offset : offset + limit]
        ],
    }


@router.post("/campaigns/{campaign_id}/book-stock/freeze", summary="Geler le stock ERP")
def freeze_book_stock(campaign: CampaignDep, importer: Importer) -> dict[str, Any]:
    return importer.freeze_book_stock(campaign).model_dump(mode="json")


@router.get("/campaigns/{campaign_id}/locations", summary="Entrepôts et emplacements")
def list_locations(campaign: CampaignDep, service: Referentials) -> dict[str, Any]:
    warehouses, locations = service.locations(campaign)
    return {
        "warehouses": [w.model_dump(mode="json") for w in warehouses],
        "locations": [
            {
                **view.location.model_dump(mode="json"),
                "hasJournal": view.journal is not None,
                "journalStatus": str(view.journal.status) if view.journal else None,
            }
            for view in locations
        ],
    }


def _resolve(target: str) -> str:
    method = _TARGETS.get(target)
    if method is None:
        raise ValidationError(
            f"Cible d'import inconnue : {target!r}.", allowed=sorted(_TARGETS)
        )
    return method
