"""Les journaux de comptage, un par emplacement ERP.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

import psycopg

from ...domain.enums import (
    DataSource,
    JournalKind,
    JournalStatus,
)
from ...domain.models import (
    CountJournal,
    CountJournalLine,
    LocationKey,
)
from ...errors import ConflictError, NotFoundError
from ._base import _Base, _NullContext, new_id

# --------------------------------------------------------------------------- #
# Counting journals
# --------------------------------------------------------------------------- #

class JournalRepository(_Base):
    """Counting journals and their lines."""

    _COLUMNS = (
        "id, campaign_id, warehouse_id, location_id, kind, status, journal_number, "
        "description, posted_at, auto_created, updated_at, row_version"
    )

    def list(
        self,
        campaign_id: str,
        *,
        status: JournalStatus | None = None,
        warehouse_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> list[CountJournal]:
        """List journals.

        :param conn: pass the *current* connection when reading back rows that
            were inserted earlier in the same open transaction. Reading through
            a second pooled connection would not see them yet, and the lines
            keyed on those journals would be silently dropped.
        """
        clauses = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]
        if status is not None:
            clauses.append("status = %s")
            params.append(str(status))
        if warehouse_id:
            clauses.append("warehouse_id = %s")
            params.append(warehouse_id)
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM count_journal WHERE {' AND '.join(clauses)} "
            "ORDER BY warehouse_id, location_id",
            params,
            conn=conn,
        )
        return [self._journal(r) for r in rows]

    def get(
        self, journal_id: str, *, conn: psycopg.Connection | None = None
    ) -> CountJournal:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM count_journal WHERE id = %s",
            (journal_id,),
            conn=conn,
        )
        if row is None:
            raise NotFoundError("Journal introuvable.", journalId=journal_id)
        return self._journal(row)

    def progress(self, campaign_id: str) -> dict[str, int]:
        row = self._fetch_one(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status IN ('POSTED','BOOK_ENFORCED')) AS complete, "
            "COUNT(*) FILTER (WHERE status = 'IN_PROGRESS') AS running, "
            "COUNT(*) FILTER (WHERE status = 'PENDING') AS pending "
            "FROM count_journal WHERE campaign_id = %s",
            (campaign_id,),
        )
        return {k: int(v) for k, v in (row or {}).items()}

    def ensure_journals(
        self,
        campaign_id: str,
        keys: Sequence[LocationKey],
        *,
        kinds: dict[LocationKey, JournalKind] | None = None,
        auto_created: bool = False,
        actor: str = "system",
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Create one PENDING journal per key that does not already have one."""
        if not keys:
            return 0
        kinds = kinds or {}
        rows = [
            (
                new_id(), campaign_id, k.warehouse_id, k.location_id,
                str(kinds.get(k, JournalKind.INVV)), auto_created, actor,
            )
            for k in keys
        ]
        return self._execute_many(
            "INSERT INTO count_journal (id, campaign_id, warehouse_id, location_id, "
            "kind, auto_created, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, warehouse_id, location_id) DO NOTHING",
            rows,
            conn=conn,
        )

    def set_status(
        self,
        campaign_id: str,
        journal_ids: Sequence[str],
        status: JournalStatus,
        *,
        actor: str,
        posted_at: dt.datetime | None = None,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Poste un lot de journaux — **de cette campagne**.

        Le filtre sur la campagne n'est pas une ceinture de plus : la
        permission est vérifiée sur la campagne de l'URL, tandis que les
        identifiants viennent du corps de la requête. Sans ce filtre, un
        gestionnaire habilité sur A postait un journal de B en connaissant son
        UUID, et la garde d'écriture n'y voyait rien.
        """
        if not journal_ids:
            return 0
        return self._execute(
            "UPDATE count_journal SET status = %s, posted_at = COALESCE(%s, posted_at), "
            "updated_by = %s, updated_at = now(), row_version = row_version + 1 "
            "WHERE campaign_id = %s AND id = ANY(%s::uuid[])",
            (str(status), posted_at, actor, campaign_id, list(journal_ids)),
            conn=conn,
        )

    def untouched_journal_keys(
        self, campaign_id: str, keys: Sequence[LocationKey],
        *, conn: psycopg.Connection | None = None,
    ) -> set[tuple[str, str]]:
        """Which of these locations have a journal nobody has used yet.

        "Used" is deliberately generous: a journal that carries a single line,
        or that somebody has merely opened, is work — and work is not something
        a reload of the ERP snapshot gets to throw away. Only a journal still
        ``PENDING`` and still empty is a leftover.
        """
        if not keys:
            return set()
        rows = self._fetch_all(
            """
            SELECT j.warehouse_id, j.location_id
            FROM count_journal j
            WHERE j.campaign_id = %s
              AND (j.warehouse_id, j.location_id)
                  IN (SELECT * FROM unnest(%s::text[], %s::text[]))
              AND j.status = 'PENDING'
              AND NOT EXISTS (
                    SELECT 1 FROM count_journal_line l
                    WHERE l.journal_id = j.id AND l.deleted_at IS NULL
              )
            """,
            (campaign_id, [k.warehouse_id for k in keys],
             [k.location_id for k in keys]),
            conn=conn,
        )
        return {(str(r["warehouse_id"]), str(r["location_id"])) for r in rows}

    def journal_keys(
        self, campaign_id: str, keys: Sequence[LocationKey],
        *, conn: psycopg.Connection | None = None,
    ) -> set[tuple[str, str]]:
        """Which of these locations have a journal at all."""
        if not keys:
            return set()
        rows = self._fetch_all(
            "SELECT warehouse_id, location_id FROM count_journal "
            "WHERE campaign_id = %s AND (warehouse_id, location_id) "
            "IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
            (campaign_id, [k.warehouse_id for k in keys],
             [k.location_id for k in keys]),
            conn=conn,
        )
        return {(str(r["warehouse_id"]), str(r["location_id"])) for r in rows}

    def delete_journals_for_locations(
        self, campaign_id: str, keys: Sequence[LocationKey],
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        """Remove journals of locations that were just disabled."""
        if not keys:
            return 0
        return self._execute(
            "DELETE FROM count_journal WHERE campaign_id = %s "
            "AND (warehouse_id, location_id) IN "
            "(SELECT * FROM unnest(%s::text[], %s::text[]))",
            (campaign_id, [k.warehouse_id for k in keys],
             [k.location_id for k in keys]),
            conn=conn,
        )

    # -- lines ---------------------------------------------------------------

    _LINE_COLUMNS = (
        "id, journal_id, campaign_id, item_number, qty_imported, qty_manual, unit, "
        "source, comment, updated_by, updated_at, row_version"
    )

    def list_lines(self, journal_id: str) -> list[CountJournalLine]:
        rows = self._fetch_all(
            f"SELECT {self._LINE_COLUMNS} FROM count_journal_line "
            "WHERE journal_id = %s AND deleted_at IS NULL ORDER BY item_number",
            (journal_id,),
        )
        return [self._line(r) for r in rows]

    def lines_by_journal(self, campaign_id: str) -> dict[str, list[CountJournalLine]]:
        rows = self._fetch_all(
            f"SELECT {self._LINE_COLUMNS} FROM count_journal_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY journal_id, item_number",
            (campaign_id,),
        )
        out: dict[str, list[CountJournalLine]] = {}
        for r in rows:
            out.setdefault(str(r["journal_id"]), []).append(self._line(r))
        return out

    def replace_imported_lines(
        self,
        campaign_id: str,
        journal_ids: Sequence[str],
        lines: Sequence[CountJournalLine],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Refresh the imported values of the given journals.

        Reloading the ERP export replaces ``qty_imported`` but **preserves**
        ``qty_manual``: the whole point of keeping the two columns apart is that
        a re-import never silently discards a human correction.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            if journal_ids:
                # Drop imported lines that vanished from the new export, but
                # keep any line a user has touched.
                cur.execute(
                    "UPDATE count_journal_line SET deleted_at = now() "
                    "WHERE journal_id = ANY(%s::uuid[]) AND qty_manual IS NULL "
                    "AND deleted_at IS NULL",
                    (list(journal_ids),),
                )
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO count_journal_line (id, journal_id, campaign_id, "
                "item_number, qty_imported, unit, source, updated_by, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (id) DO NOTHING",
                [
                    (l.id, l.journal_id, campaign_id, l.item_number, l.qty_imported,
                     l.unit, str(l.source), l.updated_by or "import")
                    for l in lines
                ],
            )
        return len(lines)

    def upsert_line(
        self, line: CountJournalLine, *, actor: str,
        expected_version: int | None = None,
        conn: psycopg.Connection | None = None,
    ) -> CountJournalLine:
        """Insert or update one line, honouring optimistic concurrency."""
        if expected_version is not None:
            n = self._execute(
                "UPDATE count_journal_line SET qty_manual = %s, unit = %s, "
                "comment = %s, source = %s, updated_by = %s, updated_at = now(), "
                "row_version = row_version + 1 "
                "WHERE campaign_id = %s AND id = %s AND row_version = %s "
                "AND deleted_at IS NULL",
                (line.qty_manual, line.unit, line.comment, str(DataSource.MANUAL),
                 actor, line.campaign_id, line.id, expected_version),
                conn=conn,
            )
            if n == 0:
                raise ConflictError(
                    "La ligne a été modifiée par quelqu'un d'autre. Rechargez-la.",
                    lineId=line.id,
                )
            return line

        self._execute(
            "INSERT INTO count_journal_line (id, journal_id, campaign_id, item_number, "
            "qty_imported, qty_manual, unit, source, comment, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (id) DO UPDATE SET qty_manual = EXCLUDED.qty_manual, "
            "unit = EXCLUDED.unit, comment = EXCLUDED.comment, "
            "source = EXCLUDED.source, updated_by = EXCLUDED.updated_by, "
            "updated_at = now(), row_version = count_journal_line.row_version + 1, "
            "deleted_at = NULL",
            (line.id, line.journal_id, line.campaign_id, line.item_number,
             line.qty_imported, line.qty_manual, line.unit, str(line.source),
             line.comment, actor),
            conn=conn,
        )
        return line

    def delete_line(
        self, campaign_id: str, line_id: str, *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        n = self._execute(
            "UPDATE count_journal_line SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND id = %s AND deleted_at IS NULL",
            (actor, campaign_id, line_id),
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Ligne de journal introuvable.", lineId=line_id)

    def replace_lines_for_journal(
        self,
        journal_id: str,
        campaign_id: str,
        lines: Sequence[CountJournalLine],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Overwrite a journal's content — used to post the GENERIQUE consolidation."""
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute("DELETE FROM count_journal_line WHERE journal_id = %s",
                        (journal_id,))
            if lines:
                cur.executemany(
                    "INSERT INTO count_journal_line (id, journal_id, campaign_id, "
                    "item_number, qty_imported, qty_manual, unit, source, "
                    "updated_by, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
                    [
                        (l.id, journal_id, campaign_id, l.item_number, l.qty_imported,
                         l.qty_manual, l.unit, str(l.source), actor)
                        for l in lines
                    ],
                )
        return len(lines)

    def listed_item_numbers(self, campaign_id: str) -> set[str]:
        """Articles present on a counting journal, whatever its status.

        Same reading as on the sheets: the line exists because somebody expects
        that article at that location, which is what makes it "stocké".
        """
        rows = self._fetch_all(
            "SELECT DISTINCT item_number FROM count_journal_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL",
            (campaign_id,),
        )
        return {str(r["item_number"]) for r in rows}

    def counted_quantities(self, campaign_id: str) -> list[dict[str, Any]]:
        """Effective counted quantity per (item, warehouse, location).

        Book-enforced journals are resolved against the snapshot so that a
        location inventoried before the freeze contributes a null variance
        instead of a phantom stock-out.
        """
        return self._fetch_all(
            """
            SELECT item_number, warehouse_id, location_id, SUM(qty) AS qty
            FROM (
                SELECT l.item_number, j.warehouse_id, j.location_id,
                       COALESCE(l.qty_manual, l.qty_imported, 0) AS qty
                FROM count_journal_line l
                JOIN count_journal j ON j.id = l.journal_id
                WHERE l.deleted_at IS NULL
                  AND j.campaign_id = %(cid)s
                  AND j.status IN ('POSTED', 'IN_PROGRESS')
                UNION ALL
                SELECT b.item_number, b.warehouse_id, b.location_id, b.qty
                FROM book_stock b
                JOIN count_journal j
                  ON j.campaign_id = b.campaign_id
                 AND j.warehouse_id = b.warehouse_id
                 AND j.location_id = b.location_id
                WHERE b.campaign_id = %(cid)s AND j.status = 'BOOK_ENFORCED'
            ) AS unified
            GROUP BY item_number, warehouse_id, location_id
            """,
            {"cid": campaign_id},
        )

    @staticmethod
    def _journal(row: dict[str, Any]) -> CountJournal:
        return CountJournal(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            kind=JournalKind(row["kind"]),
            status=JournalStatus(row["status"]),
            journal_number=row["journal_number"],
            description=row["description"],
            posted_at=row["posted_at"],
            auto_created=row["auto_created"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _line(row: dict[str, Any]) -> CountJournalLine:
        return CountJournalLine(
            id=str(row["id"]),
            journal_id=str(row["journal_id"]),
            campaign_id=str(row["campaign_id"]),
            item_number=row["item_number"],
            qty_imported=row["qty_imported"],
            qty_manual=row["qty_manual"],
            unit=row["unit"],
            source=DataSource(row["source"]),
            comment=row["comment"],
            updated_by=row["updated_by"],
            updated_at=row["updated_at"],
        )
