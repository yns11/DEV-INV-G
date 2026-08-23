"""Ce que l'exploitation demande à la base sur son propre état.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ._base import _Base

# --------------------------------------------------------------------------- #
# Exploitation
# --------------------------------------------------------------------------- #

class OperationsRepository(_Base):
    """Ce qu'un exploitant demande à la base quand quelque chose ne va pas.

    Ces requêtes ne servent aucun écran métier : elles répondent aux trois
    questions qu'on pose un jour d'inventaire — « le miroir ERP est-il à
    jour ? », « les chargements passent-ils ? », « les scans avancent-ils ? » —
    et qui n'avaient jusqu'ici de réponse qu'en ouvrant un client SQL.

    Elles sont **globales**, sans campagne : c'est l'installation qu'on
    diagnostique, pas un inventaire en particulier. Une lecture par campagne
    existe déjà là où elle a du sens (``ImportBatchRepository.latest_per_target``,
    lue par la clôture).
    """

    #: Les tables du miroir ERP, et ce que chacune alimente. Toutes portent un
    #: ``synced_at`` écrit par le job de synchronisation ; c'est ce qui permet
    #: de poser une seule requête plutôt qu'une par table.
    MIRRORS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("erp_base_article", "Référentiel articles"),
        ("erp_bom", "Nomenclatures"),
        ("erp_mouvements", "Mouvements de stock"),
        ("erp_stock_snapshot", "Snapshot de stock"),
        ("erp_ecart_backflush", "Écart backflush"),
    )

    def erp_freshness(self) -> list[dict[str, Any]]:
        """Pour chaque table du miroir : son volume et sa dernière synchro.

        Une table vide et une table jamais synchronisée se ressemblent depuis
        l'application — les deux donnent « le miroir ERP est vide » — mais pas
        du tout depuis l'exploitation : la première dit que le job a tourné sur
        une source vide, la seconde qu'il n'a pas tourné.
        """
        union = " UNION ALL ".join(
            f"SELECT '{table}' AS table_name, count(*) AS rows, "
            f"max(synced_at) AS synced_at FROM {table}"
            for table, _ in self.MIRRORS
        )
        labels = dict(self.MIRRORS)
        rows = {r["table_name"]: r for r in self._fetch_all(union)}
        return [
            {
                "table": table,
                "label": labels[table],
                "rows": int(rows.get(table, {}).get("rows") or 0),
                "syncedAt": rows.get(table, {}).get("synced_at"),
            }
            for table, _ in self.MIRRORS
        ]

    def import_volumes(self, *, hours: int = 24) -> dict[str, Any]:
        """Les chargements récents, et ce qu'ils ont refusé.

        ``rejected`` est la ligne qu'on regarde : un import qui rejette est
        désormais refusé en bloc lorsqu'il remplace, mais un fichier qui rejette
        systématiquement quelques lignes reste un contrat mal accordé, et rien
        ne le signalait tant que personne n'ouvrait le rapport.
        """
        row = self._fetch_one(
            "SELECT count(*) AS batches, "
            "coalesce(sum(rows_accepted), 0) AS accepted, "
            "coalesce(sum(rows_rejected), 0) AS rejected, "
            "count(*) FILTER (WHERE rows_rejected > 0) AS with_rejects, "
            "max(imported_at) AS last_at "
            "FROM import_batch WHERE imported_at > now() - make_interval(hours => %s)",
            (hours,),
        ) or {}
        return {
            "hours": hours,
            "batches": int(row.get("batches") or 0),
            "rowsAccepted": int(row.get("accepted") or 0),
            "rowsRejected": int(row.get("rejected") or 0),
            "batchesWithRejects": int(row.get("with_rejects") or 0),
            "lastAt": row.get("last_at"),
        }

    def scan_jobs(self, *, hours: int = 24) -> dict[str, Any]:
        """L'état des lectures de feuilles scannées, par statut.

        Un scan qui reste « en cours » est le symptôme d'un conteneur recyclé
        en pleine lecture : le PDF vivait dans sa mémoire. Le démarrage marque
        ces travaux en échec, mais leur nombre dit à quelle fréquence cela
        arrive.
        """
        rows = self._fetch_all(
            "SELECT status, count(*) AS n FROM scan_job "
            "WHERE created_at > now() - make_interval(hours => %s) "
            "GROUP BY status",
            (hours,),
        )
        by_status = {str(r["status"]): int(r["n"]) for r in rows}
        return {
            "hours": hours,
            "byStatus": by_status,
            "running": by_status.get("RUNNING", 0) + by_status.get("QUEUED", 0),
            "failed": by_status.get("FAILED", 0),
        }
