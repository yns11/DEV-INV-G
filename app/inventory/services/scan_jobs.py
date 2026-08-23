"""Le scan multi-feuilles, mené comme un travail suivi plutôt qu'en ligne.

Une pile de cent feuilles fait deux cents pages : un rendu, un routage, cent
lectures et cent écritures. Cela dure des minutes, et cela tenait jusqu'ici dans
la requête HTTP du chargement — avec trois conséquences, dans cet ordre de
gravité :

* la **passerelle coupait** avant la fin, et ce qui avait été lu partait avec la
  connexion ;
* l'écran restait figé, sans rien pour distinguer « ça travaille » de « c'est
  bloqué » ;
* un navigateur rafraîchi relançait tout.

Le chargement rend donc maintenant un identifiant de travail, tout de suite, et
la lecture continue derrière. L'écran interroge ce travail et affiche où il en
est.

**Un fil, pas une file.** Un seul scan à la fois par conteneur, et c'est
délibéré : deux piles en parallèle se disputeraient le même endpoint, donc le
même débit, et n'iraient pas plus vite à deux — elles se contenteraient de
rendre les deux barres de progression illisibles.

**Le PDF reste en mémoire.** Le stocker en base doublerait jusqu'à soixante
mégaoctets par pile dans une base transactionnelle, pour un gain qui n'existe
que si le conteneur redémarre au milieu. Ce cas-là est traité autrement : au
démarrage, tout travail encore « en cours » appartient à une instance disparue,
et il est marqué en échec — une barre qui tourne pour toujours est un mensonge
plus coûteux qu'un rechargement.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..domain.models import Campaign
from ..errors import InventoryError, NotFoundError
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["ScanJobService", "abandon_orphan_jobs", "shutdown_workers"]

#: Un seul scan à la fois. Voir l'en-tête du module.
_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan-job")

#: Les PDF en attente de traitement, par identifiant de travail. Vidé dès que le
#: travail se termine, quelle qu'en soit l'issue : une pile de soixante
#: mégaoctets gardée après coup est une fuite qui se voit au bout de dix scans.
_payloads: dict[str, bytes] = {}
_lock = threading.Lock()


class ScanJobService:
    """Dépose un scan, et rend de quoi en suivre la lecture."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ écrit

    def queue(
        self,
        campaign: Campaign,
        *,
        payload: bytes,
        filename: str,
        content_type: str,
        overwrite_reviewed: bool = False,
        sheet_id: str | None = None,
    ) -> dict[str, Any]:
        """Enregistre le travail et rend la main immédiatement.

        La garde d'écriture est passée **ici**, pas dans le fil de travail : un
        refus doit répondre au chargement, pendant que l'utilisateur regarde,
        et non apparaître deux minutes plus tard dans un travail en échec.

        ``sheet_id`` renseigné vise une feuille ; nul, c'est la pile entière.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        if sheet_id is not None:
            # Vérifié au dépôt, pas dans le fil : « feuille introuvable » doit
            # répondre au chargement, pas s'afficher deux minutes plus tard.
            sheet = ctx.sheets.get_sheet(sheet_id)
            if sheet.campaign_id != campaign.id:
                raise NotFoundError("Feuille introuvable dans cette campagne.")

        job_id = ctx.scan_jobs.create(
            campaign_id=campaign.id,
            sheet_id=sheet_id,
            filename=filename,
            content_type=content_type,
            overwrite_reviewed=overwrite_reviewed,
            actor=ctx.actor,
        )
        with _lock:
            _payloads[job_id] = payload
        _workers.submit(
            _run, job_id=job_id, campaign_id=campaign.id, actor=ctx.actor,
            request_id=ctx.request_id or "",
        )
        log.info(
            "Scan %s mis en file : %s (%d Kio)", job_id, filename, len(payload) // 1024
        )
        return self.get(campaign, job_id)

    # -------------------------------------------------------------------- lit

    def get(self, campaign: Campaign, job_id: str) -> dict[str, Any]:
        row = self.ctx.scan_jobs.get(job_id, campaign.id)
        if row is None:
            raise NotFoundError("Travail de scan introuvable dans cette campagne.")
        return _as_dict(row)

    def list(self, campaign: Campaign, *, limit: int = 20) -> list[dict[str, Any]]:
        return [
            _as_dict(row) for row in self.ctx.scan_jobs.list(campaign.id, limit=limit)
        ]

    def latest_for_sheet(
        self, campaign: Campaign, sheet_id: str
    ) -> dict[str, Any] | None:
        """Le dernier scan de cette feuille, pour reprendre un suivi interrompu.

        Un navigateur rafraîchi pendant une lecture perd l'identifiant du
        travail. Sans ce rappel, l'écran revient inerte et invite à relancer un
        scan qui tourne déjà — deux lectures concurrentes sur la même feuille,
        dont la seconde écrase la première.
        """
        row = self.ctx.scan_jobs.latest_for_sheet(sheet_id, campaign.id)
        return _as_dict(row) if row else None


# --------------------------------------------------------------------------- #
# Le fil de travail
# --------------------------------------------------------------------------- #

def _run(*, job_id: str, campaign_id: str, actor: str, request_id: str) -> None:
    """Lit la pile, de bout en bout, hors de toute requête HTTP.

    Son propre contexte de service : le fil n'a ni la requête ni la connexion de
    celui qui a déposé le scan, et emprunter les siennes serait les utiliser
    après leur fermeture. L'identité, elle, est celle du déposant — c'est bien
    son import, et le journal d'audit doit le dire.
    """
    from .scan_service import ScanService

    with _lock:
        payload = _payloads.get(job_id)
    if payload is None:  # pragma: no cover — le dépôt vient d'écrire la clé
        log.error("Scan %s : charge utile absente, travail abandonné", job_id)
        return

    ctx = ServiceContext(actor=actor, request_id=request_id)
    jobs = ctx.scan_jobs
    try:
        campaign = ctx.campaigns.get(campaign_id)
        row = jobs.get(job_id, campaign_id) or {}
        jobs.start(job_id)

        def say(**progress: Any) -> None:
            try:
                jobs.progress(job_id, **progress)
            except Exception as exc:  # pragma: no cover — l'avancement n'est
                # pas le travail : une écriture de progression qui échoue ne
                # doit pas faire perdre une lecture de cent feuilles.
                log.warning("Avancement du scan %s non écrit : %s", job_id, exc)

        service = ScanService(ctx)
        sheet_id = row.get("sheet_id")
        common = {
            "payload": payload,
            "filename": row.get("filename") or "scan",
            "content_type": row.get("content_type") or "",
            "on_progress": say,
        }
        # Une feuille ou une pile : même table, même suivi, même écran. Seule
        # la lecture diffère, et c'est `sheet_id` qui la désigne.
        if sheet_id:
            outcome = service.extract_from_scan(campaign, str(sheet_id), **common)
            report = outcome["report"]
            log.info("Scan %s terminé : feuille %s lue", job_id, sheet_id)
        else:
            report = service.extract_from_multi_scan(
                campaign,
                overwrite_reviewed=bool(row.get("overwrite_reviewed")),
                **common,
            )
            log.info(
                "Scan %s terminé : %d feuille(s) lue(s)",
                job_id, len(report.get("sheetsProcessed") or []),
            )
        jobs.finish(job_id, report=report)
    except InventoryError as exc:
        # Un refus métier — pile trop épaisse, aucune feuille lisible, phase
        # gelée. Le message est déjà écrit pour un humain : il part tel quel.
        jobs.fail(job_id, error=str(exc))
        log.warning("Scan %s refusé : %s", job_id, exc)
    except Exception as exc:
        jobs.fail(job_id, error=f"{type(exc).__name__} : {exc}")
        log.exception("Scan %s en échec", job_id)
    finally:
        with _lock:
            _payloads.pop(job_id, None)


def abandon_orphan_jobs() -> int:
    """Au démarrage : les travaux en cours appartiennent à un conteneur mort.

    Leur PDF vivait dans sa mémoire. Les laisser « en cours » afficherait une
    progression qui n'avancera jamais ; les marquer en échec dit à l'utilisateur
    la seule chose utile — recharger le scan.
    """
    try:
        abandoned = ServiceContext(actor="system", request_id="startup").scan_jobs
        count = abandoned.abandon_orphans(
            reason=(
                "L'application a redémarré pendant le traitement. Rechargez le "
                "scan : les feuilles déjà lues avant l'interruption ont été "
                "enregistrées et seront simplement relues."
            )
        )
        if count:
            log.warning("%d travail(aux) de scan abandonné(s) au démarrage", count)
        return count
    except Exception as exc:  # pragma: no cover — dépend de la base
        log.warning("Travaux de scan orphelins non repris : %s", exc)
        return 0


def shutdown_workers(*, wait: bool = False) -> None:
    """Ferme le fil de travail. Appelé à l'arrêt de l'application."""
    _workers.shutdown(wait=wait, cancel_futures=True)


# --------------------------------------------------------------------------- #
# Mise en forme
# --------------------------------------------------------------------------- #

def _as_dict(row: dict[str, Any]) -> dict[str, Any]:
    """La ligne de la table, dans la forme que l'écran lit.

    ``percent`` est calculé ici plutôt que dans le navigateur : les trois écrans
    qui affichent un scan en cours donneraient sinon trois pourcentages
    légèrement différents du même travail.
    """
    total = int(row.get("sheets_total") or 0)
    done = int(row.get("sheets_done") or 0)
    return {
        "id": str(row["id"]),
        # Renseigné = scan d'une feuille. L'écran s'en sert pour savoir s'il
        # regarde son propre travail ou celui de la pile.
        "sheetId": str(row["sheet_id"]) if row.get("sheet_id") else None,
        "status": row.get("status") or "QUEUED",
        "step": row.get("step") or "",
        "filename": row.get("filename") or "",
        "totalPages": int(row.get("total_pages") or 0),
        "pagesRouted": int(row.get("pages_routed") or 0),
        "sheetsTotal": total,
        "sheetsDone": done,
        "percent": round(100 * done / total) if total else 0,
        "report": row.get("report") or {},
        "error": row.get("error") or "",
        "createdBy": row.get("created_by") or "",
        "createdAt": _iso(row.get("created_at")),
        "startedAt": _iso(row.get("started_at")),
        "finishedAt": _iso(row.get("finished_at")),
        "isDone": (row.get("status") or "") in ("SUCCEEDED", "FAILED"),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
