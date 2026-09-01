"""Lire une feuille de comptage remplie à la main.

Une feuille revient de l'atelier avec des quantités écrites au stylo. Les
resaisir au clavier prend une minute par feuille et se trompe une fois sur
cent ; un modèle les lit en quelques secondes, et se trompe autrement.

Deux règles gouvernent tout ce module, et elles viennent de là :

**Le modèle ne décide de rien.** Ce qu'il rend est une proposition, portée à
l'écran avec sa confiance, et validée par la personne qui tient la feuille.
Une quantité écrite dans la base sans qu'un humain l'ait vue serait un
comptage que personne n'a fait.

**La liste des articles est celle de la feuille imprimée.** Une référence que
le modèle croit lire mais qui n'y figure pas est signalée, jamais acceptée :
elle créerait une ligne que le référentiel ne peut pas rapprocher.

Extrait de ``GenericService`` : la lecture de scans n'a rien à voir avec les
zones ni avec les feuilles. Elle parle à un modèle hébergé quand tout le reste
parle à la base, elle est la seule partie du service à pouvoir échouer parce
qu'un point de terminaison est lent, et elle pesait à elle seule un quart du
fichier.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ..db import new_id
from ..domain.enums import (
    AuditAction,
    SheetPass,
)
from ..domain.models import (
    Campaign,
)
from ..errors import (
    NotFoundError,
    ValidationError,
)
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["ProgressReporter", "ScanService"]


#: Combien de pages une *seule* feuille peut porter. Une feuille de comptage
#: tient sur une à trois pages ; au-delà, c'est une pile déposée sur le mauvais
#: écran, et c'est le scan multi-feuilles qu'il faut.
_SINGLE_SHEET_PAGES = 8

#: Ce que la lecture d'une pile signale de son avancement. Un protocole, pas une
#: dépendance : le pipeline ne connaît ni la table `scan_job` ni l'écran, il dit
#: simplement où il en est à qui veut l'entendre.
ProgressReporter = Callable[..., None]


class _Stopwatch:
    """Le temps passé par étape, en millisecondes.

    « Le scan est lent » ne dit pas où : le dépôt de la pièce, le rendu PDF, la
    file d'attente de l'endpoint, la génération, ou l'écriture en base. Chacune
    de ces cinq causes appelle une correction différente, et trois d'entre elles
    ne sont pas dans ce code. Les mesurer séparément est ce qui permet de savoir
    laquelle traiter — et, après une optimisation, si elle a servi.
    """

    __slots__ = ("_steps",)

    def __init__(self) -> None:
        self._steps: dict[str, int] = {}

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._steps[name] = self._steps.get(name, 0) + elapsed

    def as_dict(self) -> dict[str, int]:
        return {**self._steps, "totalMs": sum(self._steps.values())}


class ScanService:
    """La lecture des feuilles scannées."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def extract_from_scan(
        self,
        campaign: Campaign,
        sheet_id: str,
        *,
        payload: bytes,
        filename: str,
        content_type: str,
        on_progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        """Read a scanned sheet with the vision model.

        The result lands in the grid as ``SCAN_AI`` values that a human reviews
        and validates; nothing is posted automatically.

        A sheet with a pre-printed list is read *against* it: the model only has
        to find the handwritten quantity next to a known reference, and anything
        else it reads is provably a hallucination. A free-entry sheet has no such
        list by design, so the same guard is applied one step later — the model
        transcribes what it sees, and a reference the campaign's referential does
        not know is reported instead of created.
        """
        from ..ai import SheetExtractor, render_pdf_pages

        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        # L'avancement est annoncé étape par étape. Une lecture de feuille dure
        # de dix secondes à plus d'une minute selon la longueur de la liste
        # pré-imprimée : sans ces jalons, l'écran ne distingue pas un travail qui
        # avance d'un appel qui a calé, et l'utilisateur relance.
        say = on_progress or (lambda **_: None)
        say(step="Ouverture de la feuille")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == sheet.zone_id),
            None,
        )
        expected_lines = ctx.sheets.list_sheet_lines(sheet_id)
        free_entry = not expected_lines
        if free_entry and not (zone is not None and zone.free_entry):
            raise ValidationError(
                "Cette feuille n'a aucune ligne pré-imprimée et sa zone n'est "
                "pas déclarée en saisie libre. Chargez sa liste d'articles, ou "
                "passez la zone en saisie libre."
            )

        items = ctx.referentials.items_by_number(campaign.id)
        extractor = SheetExtractor()

        # Le scan est archivé avant d'être lu. C'est la pièce qui justifie les
        # quantités : sans elle, une valeur contestée six mois plus tard n'a
        # plus rien derrière elle, le conteneur qui l'a reçue ayant disparu.
        say(step="Archivage de la pièce justificative")
        # `required=True` : la feuille manuscrite repart dans l'atelier et finit
        # à la benne. Écrire les quantités lues en sachant que l'image n'a pas
        # été archivée fabriquerait un comptage invérifiable.
        archived = ctx.evidence.put(
            payload, campaign_code=campaign.code, kind="scans", filename=filename,
            required=True,
        )
        storage_path = archived.path if archived else None

        say(step="Rendu des pages")
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            # Rasterised, not split: the endpoint accepts images only.
            #
            # Une feuille seule tient en quelques pages ; le plafond sert ici de
            # garde-fou contre une pile déposée par erreur sur l'écran d'une
            # feuille — auquel cas c'est l'écran multi-feuilles qu'il faut.
            images = render_pdf_pages(
                payload, max_pages=_SINGLE_SHEET_PAGES, dpi=ctx.settings.scan_dpi
            )
            mime = "image/png"
        else:
            images = [payload]
            mime = content_type or "image/png"

        common = {
            "campaign_id": campaign.id,
            "sheet_id": sheet_id,
            "zone_label": (zone.label or zone.code) if zone else sheet.zone_id,
            "pass_no": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
            "images": images,
            "image_mime": mime,
            # Une case peut porter « 3*48+7 » : trois palettes et un fond de bac.
            # Le réglage de la campagne décide si c'est une quantité ou une case
            # vide — jamais un refus, une lecture ne fait pas échouer les cent
            # autres lignes pour une case douteuse.
            "allow_formulas": campaign.config.allow_formulas,
            "id_factory": new_id,
        }
        say(
            step=(
                f"Lecture par le modèle ({len(images)} page(s), "
                f"{len(expected_lines)} ligne(s) attendues)"
                if expected_lines
                else f"Lecture par le modèle ({len(images)} page(s))"
            ),
            total_pages=len(images),
            sheets_total=1,
            sheets_done=0,
        )
        result = (
            extractor.extract_free_entry(known_items=items, **common)
            if free_entry
            else extractor.extract(
                expected=extractor.expected_from_items(expected_lines, items),
                **common,
            )
        )

        say(step="Écriture des quantités lues")
        # Les quantités lues, le chemin de la pièce qui les justifie et la trace
        # de la lecture forment un tout : une feuille dont les lignes sont
        # écrites mais dont le chemin de preuve manque affiche des chiffres que
        # plus rien ne rattache au papier.
        with ctx.db.transaction() as conn:
            ctx.sheets.replace_sheet_lines(
                # La lecture porte sur des quantités, pas sur la mise en page :
                # elle ne connaît ni les intertitres ni les lignes vides, et
                # n'a donc rien à dire sur leur sort.
                sheet_id, result.lines, actor=ctx.actor, conn=conn,
                keep_layout=True,
            )
            ctx.sheets.update_sheet(
                campaign.id,
                sheet_id,
                counter_name=result.counter_name or None,
                evidence_path=storage_path,
                evidence_sha256=archived.sha256 if archived else None,
                evidence_bytes=archived.size if archived else None,
                evidence_mime=archived.mime if archived else None,
                extraction_confidence=result.mean_confidence,
                actor=ctx.actor,
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="count_sheet",
                entity_id=sheet_id,
                summary=(
                    f"Extraction IA du scan « {filename} » : "
                    f"{len(result.lines)} lignes, confiance moyenne "
                    f"{result.mean_confidence or 0:.0%}."
                ),
                after=result.as_report(),
                conn=conn,
            )
        say(step="Terminé", sheets_total=1, sheets_done=1)
        return {
            "report": result.as_report(),
            "sheet": ctx.sheets.get_sheet(sheet_id).model_dump(mode="json"),
        }

    def extract_from_multi_scan(
        self,
        campaign: Campaign,
        *,
        payload: bytes,
        filename: str,
        content_type: str,
        overwrite_reviewed: bool = False,
        on_progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        """Read a scan holding **several** counting sheets in one pass.

        The whole stack goes on the scanner and comes back as one PDF. Because
        the application printed those pages, every one carries its sheet's
        identifier in the footer: routing is reading that line, not guessing
        from content. A page whose footer cannot be read is reported, never
        attributed — a page filed under the wrong zone posts a count against
        stock that was never there.

        **Sheets a human has already corrected are skipped by default.** The
        expensive, irreplaceable work in this whole chain is somebody sitting
        down with the paper and fixing what the model misread; a second scan
        that silently overwrote it would destroy exactly that. Overwriting stays
        possible — it is an explicit choice, and the report names what it cost.

        :param on_progress: appelé à chaque étape franchie. C'est par là que le
            travail asynchrone alimente sa barre de progression : sur une pile de
            cent feuilles, six minutes de silence sont indistinguables d'une
            panne. Ignoré — et le traitement identique — quand personne n'écoute.
        """
        from ..ai import (
            SheetCandidate,
            SheetExtractor,
            footer_strips,
            in_parallel,
            page_count,
            render_pdf_pages,
        )

        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        settings = ctx.settings
        clock = _Stopwatch()
        say = on_progress or (lambda **_: None)
        say(step="Archivage du scan")

        # Une seule pièce pour toute la pile, et chaque feuille pointe dessus :
        # c'est bien un seul document qui les justifie toutes, et le découper
        # inventerait des originaux qui n'ont jamais existé.
        with clock.step("evidence_upload_ms"):
            archived = ctx.evidence.put(
                payload, campaign_code=campaign.code, kind="scans",
                filename=filename, required=True,
            )
            storage_path = archived.path if archived else None

        is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
        if is_pdf:
            # Compté avant d'être rendu : une pile trop épaisse se refuse en la
            # nommant. La version précédente en rendait le début et laissait
            # tomber le reste avec une ligne de journal — des comptages perdus
            # sans que personne ne l'apprenne, ce qui est pire que lent.
            total_pages = page_count(payload)
            if total_pages > settings.scan_max_pages:
                raise ValidationError(
                    f"Ce scan porte {total_pages} pages, au-delà des "
                    f"{settings.scan_max_pages} traitées en une fois. Scannez la "
                    "pile en deux fois : chaque page porte son identité, l'ordre "
                    "des piles n'a aucune importance.",
                    pages=total_pages,
                    maxPages=settings.scan_max_pages,
                )
            say(step="Préparation des pages", total_pages=total_pages)
            with clock.step("pdf_render_ms"):
                images = render_pdf_pages(
                    payload, max_pages=settings.scan_max_pages, dpi=settings.scan_dpi
                )
            mime = "image/png"
        else:
            images = [payload]
            mime = content_type or "image/png"

        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        sheets = ctx.sheets.list_sheets(campaign.id)
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        items = ctx.referentials.items_by_number(campaign.id)

        # A sheet is readable either because it carries a pre-printed list, or
        # because its zone is declared free entry — in which case what the model
        # reads is checked against the article referential instead. A sheet that
        # is neither is left out: the model would have nothing to be wrong
        # against.
        candidates = [
            SheetCandidate(
                sheet_id=sheet.id,
                zone_code=zones[sheet.zone_id].code,
                pass_no=1 if sheet.pass_no is SheetPass.PASS_1 else 2,
            )
            for sheet in sheets
            if sheet.zone_id in zones
            and (lines_by_sheet.get(sheet.id) or zones[sheet.zone_id].free_entry)
        ]
        if not candidates:
            raise ValidationError(
                "Aucune feuille n'est lisible : elles n'ont ni liste d'articles "
                "pré-imprimée, ni zone déclarée en saisie libre."
            )

        extractor = SheetExtractor()
        # Le routage ne lit qu'une ligne, imprimée en pied de page : lui envoyer
        # les pages entières, c'est transmettre neuf dixièmes de surface inutile.
        say(step="Identification des feuilles", total_pages=len(images))
        with clock.step("routing_ms"):
            routing = extractor.route_pages(
                footers=footer_strips(images),
                candidates=candidates,
                image_mime="image/png",
                batch_size=settings.scan_routing_batch,
                max_workers=settings.scan_max_workers,
            )

        by_id = {s.id: s for s in sheets}
        processed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        # Ce qui est à lire, et ce qui est préservé. Séparé de la lecture pour
        # que la boucle qui suit ne fasse qu'une chose — sans quoi le
        # parallélisme aurait à porter aussi la règle de préservation.
        to_read: list[tuple[str, list[int]]] = []
        for sheet_id, pages in routing.pages_by_sheet.items():
            sheet = by_id[sheet_id]
            zone = zones[sheet.zone_id]
            corrected = [
                l for l in lines_by_sheet.get(sheet_id, ()) if l.was_ai_corrected
            ]
            if corrected and not overwrite_reviewed:
                skipped.append({
                    "sheetId": sheet_id,
                    "zoneCode": zone.code,
                    "passNo": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
                    "pages": [p + 1 for p in pages],
                    "correctedLines": len(corrected),
                    "reason": (
                        f"{len(corrected)} ligne(s) lues par l'IA puis corrigées à "
                        "la main. Un nouveau scan les écraserait."
                    ),
                })
                continue
            to_read.append((sheet_id, pages))

        def read(job: tuple[str, list[int]]):
            sheet_id, pages = job
            sheet = by_id[sheet_id]
            zone = zones[sheet.zone_id]
            expected_lines = lines_by_sheet.get(sheet_id, [])
            common = {
                "campaign_id": campaign.id,
                "sheet_id": sheet_id,
                "zone_label": zone.label or zone.code,
                "pass_no": 1 if sheet.pass_no is SheetPass.PASS_1 else 2,
                "images": [images[p] for p in pages],
                "image_mime": mime,
                "id_factory": new_id,
            }
            return (
                extractor.extract_free_entry(known_items=items, **common)
                if not expected_lines
                else extractor.extract(
                    expected=extractor.expected_from_items(expected_lines, items),
                    **common,
                )
            )

        # **Les appels au modèle en parallèle, les écritures en série.** Cent
        # feuilles lues l'une après l'autre, c'est cent latences additionnées ;
        # c'est là qu'était l'essentiel du temps. Les écritures, elles, restent
        # sur ce fil-ci : elles passent par le pool de connexions et par la
        # discipline de transaction du service, qui ne se partagent pas.
        say(
            step="Lecture des feuilles",
            pages_routed=len(images) - len(routing.unrouted),
            sheets_total=len(to_read),
            sheets_done=0,
        )
        with clock.step("model_inference_ms"):
            outcomes = in_parallel(
                read,
                to_read,
                settings.scan_max_workers,
                on_done=lambda n: say(step="Lecture des feuilles", sheets_done=n),
            )

        say(step="Enregistrement", sheets_done=len(to_read))
        with clock.step("db_write_ms"):
            for (sheet_id, pages), result in zip(
                to_read, outcomes, strict=True
            ):
                sheet = by_id[sheet_id]
                zone = zones[sheet.zone_id]
                pass_no = 1 if sheet.pass_no is SheetPass.PASS_1 else 2
                if isinstance(result, BaseException):
                    # Une feuille perdue ne perd pas la pile : elle est nommée,
                    # avec ses pages, et se rejoue seule.
                    log.warning("Lecture de la feuille %s échouée : %s",
                                sheet_id, result)
                    failed.append({
                        "sheetId": sheet_id,
                        "zoneCode": zone.code,
                        "passNo": pass_no,
                        "pages": [p + 1 for p in pages],
                        "reason": str(result),
                    })
                    continue

                corrected = [
                    l for l in lines_by_sheet.get(sheet_id, ()) if l.was_ai_corrected
                ]
                # Une transaction par feuille, pas une pour la pile : le
                # rapport nomme les feuilles traitées une à une, et une pile de
                # trente feuilles ne doit pas perdre les vingt-neuf qui ont
                # abouti parce que la trentième a échoué. Ce qui doit tenir
                # ensemble, ce sont les lignes d'une feuille et le chemin de la
                # preuve qui les justifie.
                with ctx.db.transaction() as conn:
                    ctx.sheets.replace_sheet_lines(
                        sheet_id, result.lines, actor=ctx.actor, conn=conn,
                        keep_layout=True,
                    )
                    ctx.sheets.update_sheet(
                        campaign.id,
                        sheet_id,
                        counter_name=result.counter_name or None,
                        evidence_path=storage_path,
                        evidence_sha256=archived.sha256 if archived else None,
                        evidence_bytes=archived.size if archived else None,
                        evidence_mime=archived.mime if archived else None,
                        extraction_confidence=result.mean_confidence,
                        actor=ctx.actor,
                        conn=conn,
                    )
                # The per-sheet report is spread *first*: it carries its own
                # ``pages`` key holding a count, and the page list is what the
                # screen renders. Spreading it last silently replaced the list
                # with an integer and crashed the report on ``pages.join``.
                processed.append({
                    **result.as_report(),
                    "sheetId": sheet_id,
                    "zoneCode": zone.code,
                    "passNo": pass_no,
                    "pages": [p + 1 for p in pages],
                    "overwroteCorrections": len(corrected),
                })

        report = {
            "pages": len(images),
            "sheetsProcessed": processed,
            "sheetsSkipped": skipped,
            "sheetsFailed": failed,
            "unroutedPages": routing.unrouted,
            # Sans chronomètres, « c'est lent » ne dit pas où : le rendu PDF, la
            # file d'attente de l'endpoint, la génération ou l'écriture. Chaque
            # étape se mesure donc, et le rapport les porte.
            "timings": {
                **clock.as_dict(),
                "pages": len(images),
                "sheets": len(processed),
                "imageBytes": sum(len(blob) for blob in images),
                "routingTokens": routing.tokens_used,
                "extractionTokens": sum(
                    r.tokens_used for r in outcomes
                    if not isinstance(r, BaseException)
                ),
                "maxWorkers": settings.scan_max_workers,
                "endpoint": settings.scan_endpoint,
            },
        }
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.IMPORT,
            entity_type="count_sheet",
            summary=(
                f"Scan multi-feuilles « {filename} » : {len(images)} page(s), "
                f"{len(processed)} feuille(s) lue(s), {len(skipped)} préservée(s), "
                f"{len(failed)} en échec, "
                f"{len(routing.unrouted)} page(s) non attribuée(s)."
            ),
            after=report,
        )
        return report
