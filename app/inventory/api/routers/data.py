"""Grid contracts, referentials and every bulk-import endpoint.

One route shape serves all three input modes so the frontend grid component is
written once: ``POST /campaigns/{id}/import/{target}`` accepts a multipart file,
and ``.../paste`` and ``.../rows`` accept the same target with JSON bodies.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from ...domain.enums import ExclusionScope
from ...errors import NotFoundError, ValidationError
from ...ingest import get_contract, list_contracts
from ...services import ImportService
from ..deps import CampaignDep, Ctx, import_service
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
# --------------------------------------------------------------------------- #

def _stocked_item_numbers(ctx: Any, campaign_id: str) -> set[str]:
    """Articles the campaign actually expects to see: sheets ∪ journals.

    The referential holds the whole catalogue — tens of thousands of references,
    most of which no site has held for years. What is being counted is a much
    smaller set, and it is the only one worth reading a designation or a
    bill-of-materials edge for. The two sources are unioned rather than picked
    between because the split is a matter of storage, not of interest: a
    reference on a GENERIQUE sheet and one on a journal line are both stocked.
    """
    return ctx.sheets.listed_item_numbers(campaign_id) | ctx.journals.listed_item_numbers(
        campaign_id
    )


@router.get("/campaigns/{campaign_id}/items", summary="Référentiel articles")
def list_items(
    campaign: CampaignDep,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=20_000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query()] = None,
    counted: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """The campaign's articles, filtered server-side.

    ``counted=true`` keeps only the references that appear on a GENERIQUE
    counting sheet or in a counting journal. Filtered here rather than in the
    browser so that ``total`` means what it says and the paging stays honest.
    """
    items = ctx.referentials.list_items(campaign.id)
    if counted:
        stocked = _stocked_item_numbers(ctx, campaign.id)
        items = [i for i in items if i.item_number in stocked]
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


@router.patch(
    "/campaigns/{campaign_id}/items/{item_number}", summary="Modifier un article"
)
def update_item(
    campaign: CampaignDep, item_number: str, payload: ItemPatch, ctx: Ctx
) -> dict[str, Any]:
    """Correct one article without reloading the referential.

    A referential arrives from the ERP with a designation missing here, a type
    wrong there. Before this, the only remedy was to re-import the whole file —
    so a one-character fix meant redoing the load, and people stopped fixing
    things. The edit goes through the same freeze and sequencing guard as the
    import, and lands in the audit trail with what it replaced.
    """
    ctx.guard(campaign, "items")
    current = ctx.referentials.get_item(campaign.id, item_number)
    if current is None:
        raise NotFoundError(f"Article « {item_number} » introuvable.")

    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise ValidationError("Aucune modification transmise.")
    # `model_copy` does not re-validate, so the set has to arrive already
    # normalised — otherwise a `{ALL, GENERIC}` sent by a client would be stored
    # verbatim and read back as something the picker cannot represent.
    if "exclusions" in changes:
        changes["exclusions"] = ExclusionScope.normalise(changes["exclusions"])
    updated = current.model_copy(update=changes)

    ctx.referentials.upsert_items([updated], actor=ctx.actor)
    ctx.record(
        campaign_id=campaign.id,
        action="UPDATE",
        entity_type="item",
        entity_id=item_number,
        summary=f"Modification de l'article {item_number}",
        before={k: str(getattr(current, k)) for k in changes},
        after={k: str(getattr(updated, k)) for k in changes},
    )
    return {
        **updated.model_dump(mode="json"),
        "exclusions": sorted(str(e) for e in updated.exclusions),
        "stdPrice": float(updated.std_price),
    }


#: How an exclusion reads in the audit trail and in error messages.
_EXCLUSION_LABELS = {
    ExclusionScope.GENERIC: "hors GENERIQUE",
    ExclusionScope.BOM: "ignoré en nomenclature",
    ExclusionScope.ALL: "hors périmètre",
}


def _describe(scopes: set[ExclusionScope]) -> str:
    if not scopes:
        return "aucune exclusion"
    return ", ".join(_EXCLUSION_LABELS[s] for s in sorted(scopes, key=str))


@router.post(
    "/campaigns/{campaign_id}/items/exclusions",
    summary="Exclure ou réintégrer un lot d'articles",
)
def set_item_exclusions(
    campaign: CampaignDep, payload: ItemExclusionsRequest, ctx: Ctx
) -> dict[str, Any]:
    """Apply one exclusion to a whole selection.

    Exclusions come in families, not one reference at a time: a programme that
    left the site, an after-sales range counted elsewhere, packaging nobody
    weighs. Doing it line by line through the edit modal is what made people
    give up half-way and leave a referential that is only half true — which is
    worse than one that excludes nothing, because the gaps are invisible.

    An unknown reference stops the whole batch rather than being skipped: a
    selection is made against what is on screen, so a reference the server does
    not know means the two disagree, and silently applying the rest would hide
    it.
    """
    ctx.guard(campaign, "items")
    wanted = ExclusionScope.normalise(payload.exclusions)
    numbers = list(
        dict.fromkeys(n.strip().upper() for n in payload.item_numbers if n.strip())
    )
    if not numbers:
        raise ValidationError("Aucun article transmis.")

    known = ctx.referentials.items_by_number(campaign.id)
    missing = [n for n in numbers if n not in known]
    if missing:
        raise ValidationError(
            f"{len(missing)} article(s) hors référentiel, dont "
            f"« {missing[0]} ». Rechargez la liste avant de recommencer.",
            missing=missing[:20],
        )

    changed = [
        known[n].model_copy(update={"exclusions": set(wanted)})
        for n in numbers
        if known[n].exclusions != wanted
    ]
    if changed:
        ctx.referentials.upsert_items(changed, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action="UPDATE",
            entity_type="item",
            entity_id="",
            summary=f"{len(changed)} article(s) : {_describe(wanted)}",
            after={
                "exclusions": ",".join(sorted(str(e) for e in wanted)),
                # The references themselves, so the trail answers "which ones?"
                # without replaying the selection — truncated, because a batch
                # can carry the whole catalogue and an audit row is read by a
                # human.
                "itemNumbers": ", ".join(i.item_number for i in changed[:50])
                + (" …" if len(changed) > 50 else ""),
            },
        )
    return {
        "updated": len(changed),
        "unchanged": len(numbers) - len(changed),
        "exclusions": sorted(str(e) for e in wanted),
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
    counted: Annotated[bool, Query()] = False,
) -> list[dict[str, Any]]:
    """Every edge, or only the ones a counted reference is on either side of.

    An edge is kept when **either** end is stocked, not only the parent: an
    assembly found on a sheet is kept because it will be exploded, and a
    component found on one is kept because a wrong ``qty_per`` above it is
    exactly what would make its counted quantity unexplainable.
    """
    links = ctx.referentials.list_bom_links(campaign.id)
    if parent:
        needle = parent.strip().upper()
        links = [l for l in links if l.parent_item == needle]
    if counted:
        stocked = _stocked_item_numbers(ctx, campaign.id)
        links = [
            l for l in links
            if l.parent_item in stocked or l.child_item in stocked
        ]
    # Les désignations viennent avec : une nomenclature lue en références seules
    # oblige à ouvrir le référentiel à chaque ligne pour savoir de quoi on parle.
    items = ctx.referentials.items_by_number(campaign.id)
    return [
        {
            **l.model_dump(mode="json"),
            "qtyPer": float(l.qty_per),
            "parentName": items[l.parent_item].name if l.parent_item in items else "",
            "childName": items[l.child_item].name if l.child_item in items else "",
        }
        for l in links
    ]


@router.post(
    "/campaigns/{campaign_id}/boms/activation",
    summary="Activer ou désactiver un lot de liens",
)
def set_bom_activation(
    campaign: CampaignDep, payload: BomActivationRequest, ctx: Ctx
) -> dict[str, Any]:
    """Put a batch of bill-of-materials edges in force, or retire them.

    A version change arrives as a set — a whole assembly's recipe is replaced,
    not one edge of it — so this is the operation, and doing it link by link is
    how half a version ends up in force and the other half retired.
    """
    ctx.guard(campaign, "boms")
    wanted = {(l.parent_item, l.child_item) for l in payload.links}
    known = {(l.parent_item, l.child_item): l for l in ctx.referentials.list_bom_links(campaign.id)}
    missing = sorted(wanted - set(known))
    if missing:
        raise ValidationError(
            f"{len(missing)} lien(s) introuvables, dont "
            f"« {missing[0][0]} → {missing[0][1]} ». Rechargez la liste.",
            missing=[f"{p} → {c}" for p, c in missing[:20]],
        )

    changed = [
        known[key].model_copy(update={"active": payload.active})
        for key in sorted(wanted)
        if known[key].active != payload.active
    ]
    if changed:
        ctx.referentials.upsert_bom_links(changed, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action="UPDATE",
            entity_type="bom_link",
            summary=(
                f"{len(changed)} lien(s) "
                f"{'remis en vigueur' if payload.active else 'retirés'}"
            ),
            after={"active": str(payload.active)},
        )
    return {"updated": len(changed), "unchanged": len(wanted) - len(changed)}


@router.patch("/campaigns/{campaign_id}/boms", summary="Modifier un lien de nomenclature")
def update_bom_link(
    campaign: CampaignDep, payload: BomLinkPatch, ctx: Ctx
) -> dict[str, Any]:
    """Correct the quantity or unit of one edge.

    A wrong ``qty_per`` is invisible until consolidation explodes an assembly
    and produces a component count nobody can explain. Fixing it should cost one
    field, not a re-import of the whole structure.
    """
    ctx.guard(campaign, "boms")
    parent = payload.parent_item.strip().upper()
    child = payload.child_item.strip().upper()
    current = ctx.referentials.get_bom_link(campaign.id, parent, child)
    if current is None:
        raise NotFoundError(f"Lien « {parent} → {child} » introuvable.")

    changes = payload.model_dump(exclude_none=True, exclude={"parent_item", "child_item"})
    if not changes:
        raise ValidationError("Aucune modification transmise.")
    updated = current.model_copy(update=changes)

    ctx.referentials.upsert_bom_links([updated], actor=ctx.actor)
    ctx.record(
        campaign_id=campaign.id,
        action="UPDATE",
        entity_type="bom_link",
        entity_id=f"{parent}/{child}",
        summary=f"Modification du lien {parent} → {child}",
        before={k: str(getattr(current, k)) for k in changes},
        after={k: str(getattr(updated, k)) for k in changes},
    )
    return {**updated.model_dump(mode="json"), "qtyPer": float(updated.qty_per)}


@router.delete("/campaigns/{campaign_id}/boms", summary="Supprimer un lien")
def delete_bom_link(
    campaign: CampaignDep,
    ctx: Ctx,
    parent: Annotated[str, Query(min_length=1)],
    child: Annotated[str, Query(min_length=1)],
) -> dict[str, bool]:
    ctx.guard(campaign, "boms")
    parent_key, child_key = parent.strip().upper(), child.strip().upper()
    removed = ctx.referentials.delete_bom_link(
        campaign.id, parent_key, child_key, actor=ctx.actor
    )
    if not removed:
        raise NotFoundError(f"Lien « {parent_key} → {child_key} » introuvable.")
    ctx.record(
        campaign_id=campaign.id,
        action="DELETE",
        entity_type="bom_link",
        entity_id=f"{parent_key}/{child_key}",
        summary=f"Suppression du lien {parent_key} → {child_key}",
    )
    return {"deleted": True}


@router.get("/campaigns/{campaign_id}/bom-health", summary="Santé des nomenclatures")
def bom_health(campaign: CampaignDep, ctx: Ctx) -> dict[str, Any]:
    """Cycles, orphan links and assemblies without a structure.

    Surfaced as its own endpoint because a BOM defect discovered on the day of
    the inventory costs a whole afternoon; discovered in preparation, it costs
    ten minutes.
    """
    from ...domain.bom import BomIndex
    from ...domain.controls import check_referentials, group_findings, summarise

    items = ctx.referentials.items_by_number(campaign.id)
    links = ctx.referentials.list_bom_links(campaign.id)
    index = BomIndex(links)
    findings = check_referentials(items=items, bom_links=links, bom_index=index)
    return {
        "linkCount": len(index),
        "parentCount": len(index.parents),
        "cycles": [" → ".join(c) for c in index.find_cycles()],
        "summary": summarise(findings),
        "groups": [g.to_summary() for g in group_findings(findings)],
        "findings": [f.model_dump(mode="json") for f in findings],
    }


@router.get("/campaigns/{campaign_id}/book-stock", summary="Stock ERP")
def book_stock(
    campaign: CampaignDep,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=20_000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
    top: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> dict[str, Any]:
    """The ERP snapshot, and what the biggest lines of it weigh.

    ``top=25`` keeps the twenty-five most valuable article / warehouse /
    location triples. Stock value is concentrated — a handful of lines usually
    carry most of it — and those are the ones worth counting twice and checking
    first. ``topShare`` says how much of the total they actually represent, so
    the claim is measured rather than assumed.
    """
    lines = ctx.book_stock.list(campaign.id)
    total_value = sum(float(l.value) for l in lines)
    top_share: float | None = None
    if top is not None:
        lines = sorted(lines, key=lambda l: abs(float(l.value)), reverse=True)[:top]
        kept = sum(float(l.value) for l in lines)
        top_share = kept / total_value if total_value else 0.0
    return {
        "total": len(lines),
        "totalValue": total_value,
        # Part de la valeur portée par les lignes retenues. `null` sans filtre :
        # « 100 % » se lirait comme un résultat alors que c'est une tautologie.
        "topShare": top_share,
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


@router.post("/campaigns/{campaign_id}/book-stock/freeze", summary="Geler le stock ERP")
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
