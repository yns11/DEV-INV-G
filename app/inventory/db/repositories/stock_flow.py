"""La réconciliation de flux entre deux campagnes.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import psycopg

from ...domain.enums import (
    FlowKind,
)
from ...domain.models import (
    StockFlowErp,
    StockFlowInput,
    StockFlowRun,
)
from ._base import _Base, _NullContext

# --------------------------------------------------------------------------- #
# Stock-flow reconciliation
# --------------------------------------------------------------------------- #

class StockFlowRepository(_Base):
    """Runs comparing two campaigns, their loaded quantities and ERP snapshot."""

    _RUN_COLUMNS = (
        "id, campaign_id, baseline_campaign_id, period_start, period_end, "
        "scrap_loaded, source_loaded_at, erp_refreshed_at, "
        "receipts_refreshed_at, shipments_refreshed_at, scrap_refreshed_at, "
        "created_by, created_at, updated_at"
    )

    #: Which run column records the ERP read of each loaded step.
    _REFRESH_COLUMN: ClassVar[dict[FlowKind, str]] = {
        FlowKind.RECEIPT: "receipts_refreshed_at",
        FlowKind.SHIPMENT: "shipments_refreshed_at",
        FlowKind.SCRAP: "scrap_refreshed_at",
    }

    def list_runs(self, campaign_id: str) -> list[StockFlowRun]:
        rows = self._fetch_all(
            f"SELECT {self._RUN_COLUMNS} FROM stock_flow_run "
            "WHERE campaign_id = %s ORDER BY created_at DESC",
            (campaign_id,),
        )
        return [self._run(r) for r in rows]

    def get_run(self, run_id: str) -> StockFlowRun | None:
        row = self._fetch_one(
            f"SELECT {self._RUN_COLUMNS} FROM stock_flow_run WHERE id = %s", (run_id,)
        )
        return self._run(row) if row else None

    def upsert_run(self, run: StockFlowRun, *, actor: str) -> StockFlowRun:
        """Create the run, or update the one that already pairs the two campaigns.

        Keyed on the pair rather than on the run id: choosing the same baseline
        twice is a user re-opening their comparison, not starting a second one,
        and a second row would silently split the loaded quantities across two
        runs of which the screen would only ever show one.
        """
        row = self._fetch_one(
            "INSERT INTO stock_flow_run (id, campaign_id, baseline_campaign_id, "
            "period_start, period_end, scrap_loaded, source_loaded_at, "
            "erp_refreshed_at, created_by, updated_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (campaign_id, baseline_campaign_id) DO UPDATE SET "
            "period_start = EXCLUDED.period_start, "
            "period_end = EXCLUDED.period_end, "
            "scrap_loaded = stock_flow_run.scrap_loaded OR EXCLUDED.scrap_loaded, "
            "updated_by = EXCLUDED.updated_by, updated_at = now() "
            f"RETURNING {self._RUN_COLUMNS}",
            (
                run.id, run.campaign_id, run.baseline_campaign_id,
                run.period_start, run.period_end, run.scrap_loaded,
                run.source_loaded_at, run.erp_refreshed_at, actor, actor,
            ),
        )
        assert row is not None  # RETURNING on an upsert always yields a row
        return self._run(row)

    def delete_run(self, run_id: str) -> None:
        self._execute("DELETE FROM stock_flow_run WHERE id = %s", (run_id,))

    def mark_erp_refreshed(
        self,
        run_id: str,
        *,
        at: dt.datetime,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Stamp when production and theoretical consumption were last read.

        A targeted UPDATE rather than :meth:`upsert_run`, whose ``DO UPDATE``
        clause never carried ``erp_refreshed_at``: on a run that already existed
        — which is every run by the time it is refreshed — the date was silently
        dropped and the screen kept saying the figures had never been read.
        """
        self._execute(
            "UPDATE stock_flow_run SET erp_refreshed_at = %s, updated_by = %s, "
            "updated_at = now() WHERE id = %s",
            (at, actor, run_id),
            conn=conn,
        )

    def mark_scrap_loaded(
        self, run_id: str, *, actor: str, conn: psycopg.Connection | None = None
    ) -> None:
        """Record that the scrap step has been provided.

        Takes the caller's connection. Without it, a call made inside a
        transaction that has already touched this run borrows a *second*
        connection and waits on the row lock the first one holds — which it will
        never release, since it is blocked on this very call. The pool times out
        fifteen seconds later and reports a connection failure, naming the
        symptom rather than the deadlock.
        """
        self._execute(
            "UPDATE stock_flow_run SET scrap_loaded = true, updated_by = %s, "
            "updated_at = now() WHERE id = %s",
            (actor, run_id),
            conn=conn,
        )

    # -- loaded quantities ---------------------------------------------------

    def list_inputs(self, run_id: str) -> list[StockFlowInput]:
        rows = self._fetch_all(
            "SELECT run_id, item_number, kind, qty, unit, source "
            "FROM stock_flow_input "
            "WHERE run_id = %s ORDER BY kind, item_number",
            (run_id,),
        )
        return [
            StockFlowInput(
                run_id=str(r["run_id"]), item_number=r["item_number"],
                kind=r["kind"], qty=r["qty"], unit=r["unit"],
                source=r["source"],
            )
            for r in rows
        ]

    def mark_refreshed(
        self,
        run_id: str,
        kind: FlowKind,
        *,
        at: dt.datetime,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Stamp when one step was last read from the ERP."""
        column = self._REFRESH_COLUMN[kind]
        self._execute(
            f"UPDATE stock_flow_run SET {column} = %s, updated_by = %s, "
            "updated_at = now() WHERE id = %s",
            (at, actor, run_id),
            conn=conn,
        )

    def replace_inputs(
        self,
        run_id: str,
        kind: FlowKind,
        lines: Sequence[StockFlowInput],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Replace one kind of loaded quantity, leaving the other two alone.

        Scoped to the kind because the three loads are three separate steps: a
        user correcting their shipments must not lose the receipts they loaded
        ten minutes earlier.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM stock_flow_input WHERE run_id = %s AND kind = %s",
                (run_id, str(kind)),
            )
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO stock_flow_input "
                "(run_id, item_number, kind, qty, unit, source) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (run_id, item_number, kind) DO UPDATE SET "
                "qty = stock_flow_input.qty + EXCLUDED.qty, unit = EXCLUDED.unit, "
                "source = EXCLUDED.source",
                [
                    (
                        run_id, line.item_number, str(kind), line.qty, line.unit,
                        str(line.source),
                    )
                    for line in lines
                ],
            )
        return len(lines)

    # -- frozen ERP snapshot -------------------------------------------------

    def list_erp(self, run_id: str) -> list[StockFlowErp]:
        rows = self._fetch_all(
            "SELECT run_id, item_number, produced_qty, consumed_qty, source "
            "FROM stock_flow_erp WHERE run_id = %s ORDER BY item_number",
            (run_id,),
        )
        return [
            StockFlowErp(
                run_id=str(r["run_id"]), item_number=r["item_number"],
                produced_qty=r["produced_qty"], consumed_qty=r["consumed_qty"],
                source=r["source"],
            )
            for r in rows
        ]

    def replace_erp(
        self,
        run_id: str,
        lines: Sequence[StockFlowErp],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute("DELETE FROM stock_flow_erp WHERE run_id = %s", (run_id,))
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO stock_flow_erp (run_id, item_number, produced_qty, "
                "consumed_qty, source) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (run_id, item_number) DO UPDATE SET "
                "produced_qty = EXCLUDED.produced_qty, "
                "consumed_qty = EXCLUDED.consumed_qty, source = EXCLUDED.source",
                [
                    (
                        run_id, line.item_number, line.produced_qty,
                        line.consumed_qty, str(line.source),
                    )
                    for line in lines
                ],
            )
        return len(lines)

    @staticmethod
    def _run(row: Mapping[str, Any]) -> StockFlowRun:
        return StockFlowRun(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            baseline_campaign_id=str(row["baseline_campaign_id"]),
            period_start=row["period_start"],
            period_end=row["period_end"],
            scrap_loaded=row["scrap_loaded"],
            source_loaded_at=row["source_loaded_at"],
            erp_refreshed_at=row["erp_refreshed_at"],
            receipts_refreshed_at=row["receipts_refreshed_at"],
            shipments_refreshed_at=row["shipments_refreshed_at"],
            scrap_refreshed_at=row["scrap_refreshed_at"],
            created_by=row["created_by"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
