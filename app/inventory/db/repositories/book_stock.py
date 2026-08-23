"""Le stock ERP, gelé le jour du comptage.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg

from ...domain.models import (
    BookStockLine,
)
from ._base import _Base, _NullContext, new_id

# --------------------------------------------------------------------------- #
# Book stock
# --------------------------------------------------------------------------- #

class BookStockRepository(_Base):
    """The frozen ERP snapshot."""

    def list(self, campaign_id: str) -> list[BookStockLine]:
        rows = self._fetch_all(
            "SELECT campaign_id, item_number, warehouse_id, location_id, qty, unit, "
            "unit_cost FROM book_stock WHERE campaign_id = %s",
            (campaign_id,),
        )
        return [
            BookStockLine(
                campaign_id=str(r["campaign_id"]), item_number=r["item_number"],
                warehouse_id=r["warehouse_id"], location_id=r["location_id"],
                qty=r["qty"], unit=r["unit"], unit_cost=r["unit_cost"],
            )
            for r in rows
        ]

    def count(self, campaign_id: str) -> int:
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM book_stock WHERE campaign_id = %s",
            (campaign_id,),
        )
        return int(row["n"]) if row else 0

    def replace(
        self,
        campaign_id: str,
        lines: Sequence[BookStockLine],
        *,
        batch_id: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Replace the whole snapshot atomically.

        The book stock is a *photograph*: a partial merge would produce a
        picture that never existed. Loading it therefore truncates and rewrites
        inside one transaction, using ``COPY`` for throughput.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute("DELETE FROM book_stock WHERE campaign_id = %s",
                        (campaign_id,))
            if not lines:
                return 0
            with cur.copy(
                "COPY book_stock (id, campaign_id, item_number, warehouse_id, "
                "location_id, qty, unit, unit_cost, import_batch) FROM STDIN"
            ) as copy:
                for line in lines:
                    copy.write_row((
                        new_id(), campaign_id, line.item_number,
                        line.warehouse_id, line.location_id, line.qty,
                        line.unit, line.unit_cost, batch_id,
                    ))
        return len(lines)
