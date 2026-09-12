"""Les dérives des emplacements précomptés.

Voir :mod:`inventory.db.repositories` pour les trois règles que tous les dépôts
appliquent.

Séparé de :mod:`.erp_journal` — qui garde le journal, son périmètre et ses
lignes brutes — parce que ce sont deux agrégats : le journal est ce que l'ERP
produit, cette table est ce que la campagne en observe. Ils ne partagent que
l'écran qui les affiche.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from ...domain.models import EarlyCountDrift
from ._base import _Base, _NullContext

__all__ = ["EarlyCountDriftRepository"]


class EarlyCountDriftRepository(_Base):
    """Les dérives d'une campagne : deux quantités et leur différence."""

    _COLUMNS = (
        "id, campaign_id, erp_journal_id, warehouse_id, location_id, item_number, "
        "qty_counted_t0, qty_erp_j, drift_value"
    )

    def list(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[EarlyCountDrift]:
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM early_count_drift WHERE campaign_id = %s "
            "ORDER BY warehouse_id, location_id, item_number",
            (campaign_id,),
            conn=conn,
        )
        return [self._drift(row) for row in rows]

    def replace(
        self,
        campaign_id: str,
        drifts: Sequence[EarlyCountDrift],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Recalculer les dérives de la campagne, à neuf.

        Rien n'est reporté d'un calcul à l'autre : une dérive ne porte plus
        d'issue, donc il n'y a plus rien à sauver d'un réimport au suivant. Le
        notebook est rejoué très régulièrement le jour J, et chaque passage rend
        simplement l'état courant des deux quantités.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM early_count_drift WHERE campaign_id = %s", (campaign_id,)
            )
            if not drifts:
                return 0
            cur.executemany(
                "INSERT INTO early_count_drift (id, campaign_id, erp_journal_id, "
                "warehouse_id, location_id, item_number, qty_counted_t0, "
                "qty_erp_j, drift_qty, drift_value) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        drift.id, campaign_id, drift.erp_journal_id,
                        drift.warehouse_id, drift.location_id, drift.item_number,
                        drift.qty_counted_t0, drift.qty_erp_j, drift.drift_qty,
                        drift.drift_value,
                    )
                    for drift in drifts
                ],
            )
            return len(drifts)

    @staticmethod
    def _drift(row: dict[str, Any]) -> EarlyCountDrift:
        return EarlyCountDrift(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            erp_journal_id=(
                str(row["erp_journal_id"]) if row["erp_journal_id"] else None
            ),
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            item_number=row["item_number"],
            qty_counted_t0=row["qty_counted_t0"],
            qty_erp_j=row["qty_erp_j"],
            drift_value=row["drift_value"],
        )
