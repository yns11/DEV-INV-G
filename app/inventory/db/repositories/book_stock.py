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
            "unit_cost, reference_date, early_batch_id "
            "FROM book_stock WHERE campaign_id = %s",
            (campaign_id,),
        )
        return [
            BookStockLine(
                campaign_id=str(r["campaign_id"]), item_number=r["item_number"],
                warehouse_id=r["warehouse_id"], location_id=r["location_id"],
                qty=r["qty"], unit=r["unit"], unit_cost=r["unit_cost"],
                reference_date=r["reference_date"],
                early_batch_id=(
                    str(r["early_batch_id"]) if r["early_batch_id"] else None
                ),
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
            # Les lignes d'un lot avancé survivent au chargement général, et
            # les lignes du jour J qui viseraient leurs emplacements ne sont
            # pas écrites. C'est la règle de référence de la campagne : la
            # référence est *ce contre quoi la campagne a été comptée*, ce qui
            # est le jour J pour un emplacement ordinaire et la date du
            # précomptage pour un emplacement scellé.
            #
            # Sans cela, un emplacement précompté afficherait un écart nul dans
            # le cas nominal — puisque poster son journal a réaligné l'ERP sur
            # le physique compté — et le résultat de son inventaire
            # disparaîtrait de la campagne.
            cur.execute(
                "DELETE FROM book_stock "
                "WHERE campaign_id = %s AND early_batch_id IS NULL",
                (campaign_id,),
            )
            cur.execute(
                "SELECT DISTINCT warehouse_id, location_id FROM book_stock "
                "WHERE campaign_id = %s AND early_batch_id IS NOT NULL",
                (campaign_id,),
            )
            reserved = {(r["warehouse_id"], r["location_id"]) for r in cur.fetchall()}
            kept = [
                line for line in lines
                if (line.warehouse_id, line.location_id) not in reserved
            ]
            if not kept:
                return 0
            with cur.copy(
                "COPY book_stock (id, campaign_id, item_number, warehouse_id, "
                "location_id, qty, unit, unit_cost, import_batch, reference_date) "
                "FROM STDIN"
            ) as copy:
                for line in kept:
                    copy.write_row((
                        new_id(), campaign_id, line.item_number,
                        line.warehouse_id, line.location_id, line.qty,
                        line.unit, line.unit_cost, batch_id, line.reference_date,
                    ))
        return len(kept)

    def replace_for_batch(
        self,
        campaign_id: str,
        batch_id: str,
        lines: Sequence[BookStockLine],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Poser la référence d'un lot avancé : `ERP@T0`, lue dans son journal.

        Aucun chargement de stock séparé n'est nécessaire — la colonne
        « Stock ERP » du journal *est* le stock d'avant comptage.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM book_stock WHERE campaign_id = %s AND early_batch_id = %s",
                (campaign_id, batch_id),
            )
            if not lines:
                return 0
            with cur.copy(
                "COPY book_stock (id, campaign_id, item_number, warehouse_id, "
                "location_id, qty, unit, unit_cost, reference_date, early_batch_id) "
                "FROM STDIN"
            ) as copy:
                for line in lines:
                    copy.write_row((
                        new_id(), campaign_id, line.item_number,
                        line.warehouse_id, line.location_id, line.qty,
                        line.unit, line.unit_cost, line.reference_date, batch_id,
                    ))
        return len(lines)
