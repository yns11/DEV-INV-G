"""Les travaux de reconnaissance de feuilles scannées.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from ._base import _Base, new_id

# --------------------------------------------------------------------------- #
# Scan jobs
# --------------------------------------------------------------------------- #

class ScanJobRepository(_Base):
    """Le suivi d'un scan multi-feuilles (migration 015).

    La lecture d'une pile de cent feuilles dure des minutes. Cette table est ce
    que l'écran interroge pendant ce temps : sans elle, une attente longue est
    indistinguable d'une panne, et la requête HTTP du chargement finissait par
    être coupée par la passerelle en emportant le travail déjà fait.
    """

    _COLUMNS = (
        "id, campaign_id, sheet_id, filename, content_type, status, step, "
        "total_pages, pages_routed, sheets_total, sheets_done, report, error, "
        "overwrite_reviewed, created_by, created_at, started_at, finished_at"
    )

    def create(
        self,
        *,
        campaign_id: str,
        filename: str,
        content_type: str,
        overwrite_reviewed: bool,
        actor: str,
        sheet_id: str | None = None,
    ) -> str:
        """``sheet_id`` renseigné = scan d'une feuille ; nul = pile complète."""
        job_id = new_id()
        self._execute(
            "INSERT INTO scan_job (id, campaign_id, sheet_id, filename, "
            "content_type, overwrite_reviewed, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (job_id, campaign_id, sheet_id, filename, content_type,
             overwrite_reviewed, actor),
        )
        return job_id

    def latest_for_sheet(self, sheet_id: str, campaign_id: str) -> dict[str, Any] | None:
        """Le dernier scan déposé sur cette feuille, terminé ou non.

        C'est ce qui permet à l'écran de retrouver une lecture en cours après un
        rafraîchissement : sans lui, recharger la page pendant un scan donne une
        feuille d'apparence inerte, et l'utilisateur relance une lecture qui
        tourne déjà.
        """
        return self._fetch_one(
            f"SELECT {self._COLUMNS} FROM scan_job WHERE sheet_id = %s "
            "AND campaign_id = %s ORDER BY created_at DESC LIMIT 1",
            (sheet_id, campaign_id),
        )

    def get(self, job_id: str, campaign_id: str) -> dict[str, Any] | None:
        """Filtré sur la campagne autant que sur le travail : l'identifiant vient
        de l'URL, et rien d'autre n'empêcherait de lire le scan du voisin."""
        return self._fetch_one(
            f"SELECT {self._COLUMNS} FROM scan_job WHERE id = %s AND campaign_id = %s",
            (job_id, campaign_id),
        )

    def list(self, campaign_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._fetch_all(
            f"SELECT {self._COLUMNS} FROM scan_job WHERE campaign_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (campaign_id, limit),
        )

    def start(self, job_id: str, *, total_pages: int = 0) -> None:
        self._execute(
            "UPDATE scan_job SET status = 'RUNNING', started_at = now(), "
            "step = %s, total_pages = %s WHERE id = %s",
            ("Préparation des pages", total_pages, job_id),
        )

    def progress(
        self,
        job_id: str,
        *,
        step: str,
        total_pages: int | None = None,
        pages_routed: int | None = None,
        sheets_total: int | None = None,
        sheets_done: int | None = None,
    ) -> None:
        """Avance le compteur, sans jamais le faire reculer.

        Chaque champ est optionnel : une étape qui ne connaît pas encore le
        nombre de feuilles ne doit pas le remettre à zéro en le passant.
        """
        sets = ["step = %s"]
        params: list[Any] = [step]
        for column, value in (
            ("total_pages", total_pages),
            ("pages_routed", pages_routed),
            ("sheets_total", sheets_total),
            ("sheets_done", sheets_done),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                params.append(value)
        params.append(job_id)
        self._execute(f"UPDATE scan_job SET {', '.join(sets)} WHERE id = %s", params)

    def finish(self, job_id: str, *, report: dict[str, Any]) -> None:
        self._execute(
            "UPDATE scan_job SET status = 'SUCCEEDED', step = 'Terminé', "
            "report = %s, finished_at = now(), "
            "sheets_done = %s WHERE id = %s",
            (Jsonb(report), len(report.get("sheetsProcessed") or []), job_id),
        )

    def fail(self, job_id: str, *, error: str) -> None:
        self._execute(
            "UPDATE scan_job SET status = 'FAILED', step = 'Échec', error = %s, "
            "finished_at = now() WHERE id = %s",
            (error[:2000], job_id),
        )

    def abandon_orphans(self, *, reason: str) -> int:
        """Marque en échec les travaux d'un conteneur qui n'existe plus.

        Le PDF vit en mémoire du processus qui l'a reçu : un travail encore
        « en cours » au démarrage appartient à une instance disparue et
        n'avancera jamais. Le laisser dans cet état, c'est une barre de
        progression qui tourne pour toujours.
        """
        return self._execute(
            "UPDATE scan_job SET status = 'FAILED', step = 'Échec', error = %s, "
            "finished_at = now() WHERE status IN ('QUEUED', 'RUNNING')",
            (reason,),
        )
