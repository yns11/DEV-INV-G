"""GENERIQUE endpoints: zones, sheets, scans, arbitration and consolidation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from ...domain.controls import group_findings
from ...errors import ValidationError
from ...services import GenericService, ScanJobService
from ..deps import (
    CampaignDep,
    Ctx,
    generic_service,
    resolve_perimeter,
    scan_job_service,
)
from ..schemas import (
    ArbitrationDecisionRequest,
    ReclassifyRequest,
    SheetLineDeleteRequest,
    SheetLinesRequest,
    ZoneClosureRequest,
    ZoneDeleteRequest,
    ZoneNegativeRequest,
    ZonePassesRequest,
    ZoneRequest,
)

router = APIRouter(prefix="/campaigns/{campaign_id}/generic", tags=["GENERIQUE"])

Service = Annotated[GenericService, Depends(generic_service)]
ScanJobs = Annotated[ScanJobService, Depends(scan_job_service)]

#: Scans are read by a vision model; anything else would be silently misread.
_ACCEPTED_SCAN_TYPES = {
    "application/pdf", "image/png", "image/jpeg", "image/jpg", "image/webp",
    "image/tiff",
}


@router.get("/zones", summary="Zones et feuilles de comptage")
def list_zones(
    campaign: CampaignDep,
    ctx: Ctx,
    service: Service,
    focus: Annotated[bool, Query()] = False,
    manager: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Zones with their derived status and per-pass progress.

    ``focus=true`` keeps only the zones assigned to the manager the signed-in
    identity resolves to. Like everywhere else, it is a **filter, not a
    permission**: nothing about what may be done to a zone changes with it.
    """
    return service.list_zones(
        campaign,
        perimeter=resolve_perimeter(campaign, ctx, focus=focus, manager=manager),
    )


@router.post("/zones", status_code=201, summary="Créer une zone")
def create_zone(
    campaign: CampaignDep, payload: ZoneRequest, service: Service
) -> dict[str, Any]:
    """Create a zone and its counting sheets.

    Allowed in preparation — which is when one decides what to count — as well
    as during counting, where a physical area nobody had listed is routinely
    discovered on the day.

    The zone is created with no pre-printed article list, so it is flagged as a
    free-entry sheet unless the caller says otherwise.
    """
    zone = service.create_zone(
        campaign,
        code=payload.code,
        label=payload.label,
        sector=payload.sector,
        display_order=payload.display_order,
        passes=payload.passes,
        free_entry=payload.free_entry,
        manager_code=payload.manager_code,
    )
    return zone.model_dump(mode="json")


@router.post("/zones/passes", summary="Changer le nombre de comptages de zones")
def set_zone_passes(
    campaign: CampaignDep, payload: ZonePassesRequest, service: Service
) -> dict[str, int]:
    """Bulk switch between one and two independent counts.

    Dropping to one deletes the second sheet, so the call is refused — naming
    the zones — when that sheet already carries a quantity: a zone brought back
    to a single count after the fact would lose a real count.
    """
    return service.set_zone_passes(campaign, payload.zone_ids, payload.passes)


@router.post("/zones/negative", summary="Autoriser les quantités négatives")
def set_zone_negative(
    campaign: CampaignDep, payload: ZoneNegativeRequest, service: Service
) -> dict[str, int]:
    """Lift the no-negative rule on the zones that legitimately need it.

    A counted quantity is normally positive — one does not find minus twenty
    screws in a bin — so a negative is refused as a typo. Correction sheets are
    the exception, and this is where they are declared.
    """
    return {
        "updated": service.set_zone_negative(
            campaign, payload.zone_ids, payload.allowed
        )
    }


@router.post("/zones/delete", summary="Supprimer des zones et leurs feuilles")
def delete_zones(
    campaign: CampaignDep, payload: ZoneDeleteRequest, service: Service
) -> dict[str, int]:
    """Retirer une zone, ou toute une sélection, pendant la préparation.

    Une zone créée en double, un secteur qui ne sera finalement pas compté : il
    fallait jusqu'ici vivre avec, la suppression n'étant offerte nulle part.

    La zone est retirée logiquement et ses feuilles supprimées : les laisser
    donnerait des feuilles rattachées à une zone qui n'existe plus.
    """
    return service.delete_zones(campaign, payload.zone_ids)


@router.get("/lines", summary="Toutes les lignes de feuilles de la campagne")
def list_all_lines(
    campaign: CampaignDep,
    ctx: Ctx,
    service: Service,
    zone_id: Annotated[str | None, Query(alias="zoneId")] = None,
) -> list[dict[str, Any]]:
    """Every counting-sheet line, flat, with its zone and pass.

    The zone-by-zone screens answer "what is on this sheet?"; this one answers
    "where does this reference appear at all?", which is the question when a
    line was typed into the wrong zone or a whole family has to be added to
    fifteen sheets at once. Same lines, one list, so a correction is one edit
    instead of fifteen navigations.
    """
    return service.list_all_lines(campaign, zone_id=zone_id)


@router.post("/lines/delete", summary="Supprimer un lot de lignes")
def delete_sheet_lines(
    campaign: CampaignDep, payload: SheetLineDeleteRequest, service: Service
) -> dict[str, int]:
    """Remove a selection of lines in one go.

    Lines arrive by the hundred from an ERP list and are pruned by hand; doing
    that one confirmation at a time is what makes people give up and count
    references nobody stocks any more.
    """
    return {"deleted": service.delete_sheet_lines(campaign, payload.line_ids)}


@router.get("/sheets/{sheet_id}", summary="Contenu d'une feuille de comptage")
def get_sheet(
    campaign: CampaignDep, sheet_id: str, service: Service
) -> dict[str, Any]:
    return service.get_sheet(campaign, sheet_id)


@router.post("/zones/{zone_id}/closure", summary="Terminer une zone, ou la rouvrir")
def set_zone_closure(
    campaign: CampaignDep,
    zone_id: str,
    payload: ZoneClosureRequest,
    service: Service,
) -> dict[str, Any]:
    """La seule décision d'état du parcours de comptage.

    Elle remplace quatre transitions par feuille — en attente, comptage en
    cours, encodage en cours, terminée — qu'il fallait faire avancer à la main
    sans qu'aucune écriture n'en dépende.

    Une zone dont les deux comptages se contredisent encore refuse la clôture,
    et le dit : la consolidation ne saurait pas quelle quantité retenir.
    Rouvrir, en revanche, ne se refuse jamais.
    """
    return service.set_zone_closed(campaign, zone_id, closed=payload.closed)


@router.put("/sheets/{sheet_id}/lines", summary="Enregistrer les lignes d'une feuille")
def upsert_sheet_lines(
    campaign: CampaignDep,
    sheet_id: str,
    payload: SheetLinesRequest,
    service: Service,
) -> dict[str, int]:
    rows = [
        {
            "id": line.id,
            "item_number": line.item_number,
            "section": str(line.section),
            "qty": line.qty,
            "unit": line.unit,
            "comment": line.comment,
            "display_order": line.display_order,
        }
        for line in payload.lines
    ]
    written = service.upsert_sheet_lines(campaign, sheet_id, rows, replace=payload.replace)
    return {"written": written}


@router.delete("/lines/{line_id}", summary="Supprimer une ligne de feuille")
def delete_sheet_line(
    campaign: CampaignDep, line_id: str, service: Service
) -> dict[str, bool]:
    service.delete_sheet_line(campaign, line_id)
    return {"deleted": True}


@router.post("/sheets/{sheet_id}/scan", summary="Déposer le scan d'une feuille")
async def extract_scan(
    campaign: CampaignDep,
    sheet_id: str,
    jobs: ScanJobs,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Dépose le scan d'une feuille et rend de quoi en suivre la lecture.

    **La réponse est immédiate**, comme pour une pile. Une feuille seule est plus
    courte à lire qu'une pile de cent, mais pas courte : rendu des pages, un
    appel au modèle de vision, écriture des lignes — de dix secondes à plus d'une
    minute. Tenue dans cette requête, l'attente n'offrait rien à regarder et ne
    distinguait pas un travail qui avance d'un appel qui a calé. L'écran
    interroge ``GET /scan/jobs/{jobId}``, exactement comme pour le multi-feuilles.

    Ce que la lecture fait n'a pas changé. Les quantités arrivent en ``SCAN_AI``
    avec une confiance par ligne, qu'un humain relit et valide ; rien n'est posté
    automatiquement, et une référence lue qui ne figure pas sur la feuille
    imprimée est signalée comme suspecte plutôt qu'acceptée.
    """
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in _ACCEPTED_SCAN_TYPES:
        raise ValidationError(
            f"Type de fichier non pris en charge : {content_type}. "
            "Formats acceptés : PDF, PNG, JPEG, WEBP, TIFF.",
            contentType=content_type,
        )
    payload = await file.read()
    if not payload:
        raise ValidationError("Le fichier reçu est vide.")
    return jobs.queue(
        campaign,
        sheet_id=sheet_id,
        payload=payload,
        filename=file.filename or "scan",
        content_type=content_type,
    )


@router.get(
    "/sheets/{sheet_id}/scan/job", summary="Le dernier scan déposé sur cette feuille"
)
def latest_sheet_scan_job(
    campaign: CampaignDep, sheet_id: str, jobs: ScanJobs
) -> dict[str, Any] | None:
    """De quoi reprendre un suivi qu'un rafraîchissement a interrompu.

    Sans lui, recharger la page pendant une lecture donne une feuille
    d'apparence inerte, et l'utilisateur relance un scan qui tourne déjà.
    """
    return jobs.latest_for_sheet(campaign, sheet_id)


@router.post("/scan", summary="Déposer un scan de plusieurs feuilles")
async def extract_multi_scan(
    campaign: CampaignDep,
    jobs: ScanJobs,
    file: Annotated[UploadFile, File()],
    overwrite_reviewed: Annotated[bool, Form(alias="overwriteReviewed")] = False,
) -> dict[str, Any]:
    """Dépose une pile scannée et rend de quoi en suivre la lecture.

    **La réponse est immédiate.** Une pile de cent feuilles fait deux cents
    pages, et sa lecture dure des minutes : la tenir dans cette requête faisait
    couper la passerelle avant la fin, en emportant ce qui avait déjà été lu.
    Le travail est donc enregistré, la lecture continue derrière, et l'écran
    interroge ``GET /scan/jobs/{jobId}``.

    Ce que la lecture fait n'a pas changé. Chaque page est attribuée à sa feuille
    par l'identifiant que l'application a elle-même imprimé en pied de page ; une
    page dont le pied est illisible est **signalée, jamais attribuée**. Et une
    feuille dont un humain a déjà corrigé les valeurs lues par l'IA est
    **préservée** : cette relecture est l'étape la plus coûteuse de toute la
    chaîne, et l'écraser en silence serait la seule erreur irrattrapable ici.
    ``overwriteReviewed=true`` le fait quand même, et le rapport dit ce que cela
    a coûté.
    """
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in _ACCEPTED_SCAN_TYPES:
        raise ValidationError(
            f"Type de fichier non pris en charge : {content_type}. "
            "Formats acceptés : PDF, PNG, JPEG, WEBP, TIFF.",
            contentType=content_type,
        )
    payload = await file.read()
    if not payload:
        raise ValidationError("Le fichier reçu est vide.")
    return jobs.queue(
        campaign,
        payload=payload,
        filename=file.filename or "scan",
        content_type=content_type,
        overwrite_reviewed=overwrite_reviewed,
    )


@router.get("/scan/jobs", summary="Les scans déposés sur cette campagne")
def list_scan_jobs(campaign: CampaignDep, jobs: ScanJobs) -> list[dict[str, Any]]:
    return jobs.list(campaign)


@router.get("/scan/jobs/{job_id}", summary="Où en est la lecture d'un scan")
def get_scan_job(
    campaign: CampaignDep, job_id: str, jobs: ScanJobs
) -> dict[str, Any]:
    """L'avancement, puis le rapport une fois la lecture terminée.

    C'est ce que l'écran interroge pendant le traitement. ``isDone`` dit quand
    arrêter : sans lui, chaque écran devrait connaître la liste des statuts
    terminaux et l'un d'eux finirait par en oublier un.
    """
    return jobs.get(campaign, job_id)


# --------------------------------------------------------------------------- #
# Arbitration
# --------------------------------------------------------------------------- #

@router.get("/arbitrations", summary="Écarts entre comptage n°1 et n°2")
def list_arbitrations(
    campaign: CampaignDep,
    service: Service,
    zone_id: Annotated[str | None, Query(alias="zoneId")] = None,
) -> list[dict[str, Any]]:
    """Every (item, section) present in either pass, valued and sorted.

    Sorted with the decisions that still need a human first, then by the euro
    impact of the gap — so the most expensive disagreement is dealt with first.
    """
    return service.list_arbitrations(campaign, zone_id)


@router.post("/zones/{zone_id}/arbitrations/refresh", summary="Recalculer les écarts")
def refresh_arbitrations(
    campaign: CampaignDep, zone_id: str, service: Service
) -> list[dict[str, Any]]:
    return service.refresh_arbitrations(campaign, zone_id)


@router.post("/arbitrations/{arbitration_id}", summary="Arbitrer un écart")
def decide_arbitration(
    campaign: CampaignDep,
    arbitration_id: str,
    payload: ArbitrationDecisionRequest,
    service: Service,
) -> dict[str, bool]:
    service.decide_arbitration(
        campaign, arbitration_id, payload.qty, comment=payload.comment
    )
    return {"decided": True}


@router.post(
    "/zones/{zone_id}/arbitrations/prefill-pass-2",
    summary="Pré-remplir les écarts d'une zone avec le comptage n°2",
)
def prefill_with_pass_2(
    campaign: CampaignDep, zone_id: str, service: Service
) -> dict[str, int]:
    """Copy pass 2 into the open arbitrations — a shortcut, not a decision.

    The quantities land in the fields; each one still has to be validated (or
    changed) before the consolidation will use it. Lines already decided are
    left untouched.
    """
    return {"proposed": service.prefill_with_pass_2(campaign, zone_id)}


# --------------------------------------------------------------------------- #
# Consolidation
# --------------------------------------------------------------------------- #

@router.get("/consolidation/preview", summary="Prévisualiser la consolidation")
def preview_consolidation(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Run the consolidation including unfinished zones, without saving.

    This is the live view during counting: it shows what the GENERIQUE journal
    would contain right now, and which zones are still missing.
    """
    result = service.consolidate(campaign, preview=True)
    items = service.ctx.referentials.items_by_number(campaign.id)
    return {
        "lines": [service.line_payload(line, items) for line in result.lines],
        "totalQty": float(result.total_qty),
        "zonesIncluded": result.zones_included,
        "zonesSkipped": result.zones_skipped,
        "findings": [f.model_dump(mode="json") for f in result.findings],
        "groups": [g.to_summary() for g in group_findings(result.findings)],
        "blocking": len(result.blocking),
    }


@router.post("/consolidation", summary="Consolider et alimenter le journal GENERIQUE")
def run_consolidation(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """Consolidate every finished zone and post the result to the INVV journal.

    Replaces the Excel macro plus the manual copy/paste into the ERP: the
    resulting journal is downloadable in the ERP import format.
    """
    return service.consolidate_and_save(campaign)


@router.get("/consolidation", summary="Dernière consolidation enregistrée")
def current_consolidation(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    return service.current_consolidation(campaign)


@router.get("/wip-without-bom", summary="Lignes WIP sans nomenclature")
def wip_without_bom(campaign: CampaignDep, service: Service) -> list[dict[str, Any]]:
    """WIP lines whose assembly has no bill of materials.

    They block the consolidation on purpose: exploding an assembly with no
    structure would silently destroy the counted quantity. Since the BOM
    referential is frozen during counting, the remedy is to reclassify the line
    as *WIP assemblé* — which is what ``POST /reclassify-wip`` does.
    """
    return service.wip_without_bom(campaign)


@router.post("/reclassify-wip", summary="Reclasser des lignes de comptage")
def reclassify_wip(
    campaign: CampaignDep, payload: ReclassifyRequest, service: Service
) -> dict[str, int]:
    """Move counting-sheet lines to another section, as an explicit decision."""
    return {
        "updated": service.reclassify_wip(
            campaign, payload.line_ids, section=payload.section
        )
    }


@router.get("/wip/{item_number}", summary="Décomposition du WIP d'un composant")
def wip_breakdown(
    campaign: CampaignDep, item_number: str, service: Service
) -> list[dict[str, Any]]:
    """Answer "where does this component's WIP quantity come from?".

    The specification asks for the WIP to be explorable rather than shown as one
    aggregated number; this is the data behind that drill-down.
    """
    return service.wip_breakdown(campaign, item_number.strip().upper())
