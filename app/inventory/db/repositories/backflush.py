"""L'écart backflush importé de l'ERP.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import psycopg

from ...domain.models import (
    BackflushLine,
)
from ._base import _Base, _NullContext

_BACKFLUSH_COLUMNS = (
    "campaign_id, item_number, period_start, period_end, unit, net_qty, "
    "under_consumed_qty, over_consumed_qty, theoretical_qty, actual_qty, "
    "parent_count, week_count, source_loaded_at, refreshed_at"
)


class BackflushRepository(_Base):
    """The backflush variance, frozen per campaign and article."""

    def list(self, campaign_id: str) -> list[BackflushLine]:
        rows = self._fetch_all(
            f"SELECT {_BACKFLUSH_COLUMNS} FROM campaign_backflush "
            "WHERE campaign_id = %s ORDER BY item_number",
            (campaign_id,),
        )
        return [self._line(r) for r in rows]

    def by_item(self, campaign_id: str) -> dict[str, BackflushLine]:
        return {line.item_number: line for line in self.list(campaign_id)}

    def count(self, campaign_id: str) -> int:
        row = self._fetch_one(
            "SELECT count(*) AS n FROM campaign_backflush WHERE campaign_id = %s",
            (campaign_id,),
        )
        return int(row["n"]) if row else 0

    def period(self, campaign_id: str) -> dict[str, Any] | None:
        """The bounds and freshness of the frozen read, or ``None`` if never run.

        Read from the rows themselves rather than from a header table: the bounds
        are stored *with* every value precisely so that a figure cannot end up
        described by a period it was not computed on.
        """
        return self._fetch_one(
            "SELECT min(period_start) AS period_start, max(period_end) AS period_end, "
            "max(source_loaded_at) AS source_loaded_at, "
            "max(refreshed_at) AS refreshed_at, count(*) AS items "
            "FROM campaign_backflush WHERE campaign_id = %s",
            (campaign_id,),
        )

    def replace(
        self,
        campaign_id: str,
        lines: Sequence[BackflushLine],
        *,
        batch_id: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Rewrite the whole frozen read, atomically.

        Delete-then-insert rather than upsert-then-prune. Both are idempotent,
        but only this one guarantees no row survives from a previous period: an
        article that had a variance last time and none now must disappear, not
        keep its old figure under new bounds.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM campaign_backflush WHERE campaign_id = %s", (campaign_id,)
            )
            if not lines:
                return 0
            cur.executemany(
                f"INSERT INTO campaign_backflush ({_BACKFLUSH_COLUMNS}, import_batch) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)",
                [
                    (
                        campaign_id, line.item_number, line.period_start,
                        line.period_end, line.unit, line.net_qty,
                        line.under_consumed_qty, line.over_consumed_qty,
                        line.theoretical_qty, line.actual_qty, line.parent_count,
                        line.week_count, line.source_loaded_at, batch_id,
                    )
                    for line in lines
                ],
            )
        return len(lines)

    @staticmethod
    def _line(row: Mapping[str, Any]) -> BackflushLine:
        return BackflushLine(
            campaign_id=str(row["campaign_id"]),
            item_number=row["item_number"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            unit=row["unit"],
            net_qty=row["net_qty"],
            under_consumed_qty=row["under_consumed_qty"],
            over_consumed_qty=row["over_consumed_qty"],
            theoretical_qty=row["theoretical_qty"],
            actual_qty=row["actual_qty"],
            parent_count=row["parent_count"],
            week_count=row["week_count"],
            source_loaded_at=row["source_loaded_at"],
            refreshed_at=row["refreshed_at"],
        )
