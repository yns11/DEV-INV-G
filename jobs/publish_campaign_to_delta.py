"""Publish one campaign from Lakebase to Delta / Unity Catalog.

Run as a Lakeflow job (see ``databricks.yml``) or manually::

    databricks bundle run inventory_publish_campaign -t prod \
        --params campaign_code=INV-2026-06

Why a job rather than a dual write from the app
-----------------------------------------------
Writing to both stores inside a request would make every user action depend on
the warehouse being awake, and a partial failure would leave the two stores
disagreeing about the same campaign. Publishing asynchronously and idempotently
keeps the app fast and the two stores eventually consistent — with Lakebase as
the operational source of truth and Delta as the governed archive.

Idempotency
-----------
Every table is rewritten for the published campaign only, inside a
``replaceWhere`` on the campaign partition. Re-running the job produces the same
result; it never appends duplicates. A campaign can therefore be republished
after a correction without any manual clean-up.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("publish")


# --------------------------------------------------------------------------- #
# Extraction queries — one per published table
# --------------------------------------------------------------------------- #

QUERIES: dict[str, str] = {
    "campaign": """
        SELECT id::text AS campaign_id, code, label, count_date, status,
               referentials_frozen_at, book_stock_frozen_at, counting_frozen_at,
               closed_at, cloned_from_code, engine_version, created_by, created_at
        FROM inventory.campaign
        WHERE code = %(code)s AND deleted_at IS NULL
    """,
    "item_snapshot": """
        SELECT campaign_id::text, item_number, name, item_type, category, program,
               commonality, unit, std_price, exclusions
        FROM inventory.item
        WHERE campaign_id = %(campaign_id)s AND deleted_at IS NULL
    """,
    "bom_snapshot": """
        SELECT campaign_id::text, parent_item, child_item, qty_per, unit
        FROM inventory.bom_link
        WHERE campaign_id = %(campaign_id)s AND deleted_at IS NULL
    """,
    "book_stock_snapshot": """
        SELECT campaign_id::text, item_number, warehouse_id, location_id, qty, unit,
               unit_cost, (qty * unit_cost) AS value
        FROM inventory.book_stock
        WHERE campaign_id = %(campaign_id)s
    """,
    "count_result": """
        SELECT l.campaign_id::text, l.item_number, j.warehouse_id, j.location_id,
               j.kind AS journal_kind, j.status AS journal_status,
               j.journal_number, l.qty_imported, l.qty_manual,
               COALESCE(l.qty_manual, l.qty_imported, 0) AS qty, l.unit, l.source
        FROM inventory.count_journal_line l
        JOIN inventory.count_journal j ON j.id = l.journal_id
        WHERE l.campaign_id = %(campaign_id)s AND l.deleted_at IS NULL
    """,
    "wip_breakdown": """
        SELECT r.campaign_id::text, b.zone_code, b.parent_item, b.parent_qty,
               b.child_item, b.qty_per_parent, b.child_qty
        FROM inventory.wip_breakdown b
        JOIN inventory.consolidation_run r ON r.id = b.run_id
        WHERE r.campaign_id = %(campaign_id)s AND r.is_current
    """,
    "adjustment": """
        SELECT campaign_id::text, item_number, warehouse_id, location_id, kind, qty,
               unit, value, journal_number, physical_date, reason_code
        FROM inventory.adjustment_line
        WHERE campaign_id = %(campaign_id)s AND deleted_at IS NULL
    """,
    "variance_analysis": """
        SELECT campaign_id::text, item_number, cause_code, comment, analyst, accepted,
               ai_suggested_cause, ai_confidence, ai_rationale
        FROM inventory.variance_analysis
        WHERE campaign_id = %(campaign_id)s
    """,
    "audit_event": """
        SELECT campaign_id::text, id::text AS event_id, at, actor, action,
               entity_type, entity_id, summary
        FROM inventory.audit_event
        WHERE campaign_id = %(campaign_id)s
    """,
}


def fetch(cursor: Any, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    columns = [c.name for c in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=os.environ.get("INV_UC_CATALOG",
                                                            "emotors_data_champions"))
    parser.add_argument("--schema", default=os.environ.get("INV_UC_SCHEMA", "inventory"))
    parser.add_argument("--campaign-code", required=True)
    args = parser.parse_args()

    code = args.campaign_code.strip().upper()
    if not code:
        log.error("A campaign code is required.")
        return 2

    import psycopg
    from psycopg.rows import dict_row
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    published_at = dt.datetime.now(dt.UTC)

    conninfo = _lakebase_conninfo()
    log.info("Publishing campaign %s to %s.%s", code, args.catalog, args.schema)

    with psycopg.connect(conninfo, row_factory=dict_row) as conn, conn.cursor() as cur:
        campaigns = fetch(cur, QUERIES["campaign"], {"code": code})
        if not campaigns:
            log.error("Campaign %s not found in Lakebase.", code)
            return 1
        campaign = campaigns[0]
        campaign_id = campaign["campaign_id"]
        count_date = campaign["count_date"]

        published: dict[str, int] = {}

        # ---- the campaign row itself -------------------------------------
        _write(
            spark,
            args,
            "campaign",
            [{**campaign, "published_at": published_at}],
            partition_column=None,
            replace_predicate=f"code = '{_escape(code)}'",
        )
        published["campaign"] = 1

        # ---- everything partitioned by campaign_code ----------------------
        for table, query in QUERIES.items():
            if table == "campaign":
                continue
            rows = fetch(cur, query, {"campaign_id": campaign_id})
            enriched = [
                {
                    **row,
                    "campaign_code": code,
                    "published_at": published_at,
                    **({"count_date": count_date}
                       if table in ("book_stock_snapshot", "count_result") else {}),
                }
                for row in rows
            ]
            _write(
                spark,
                args,
                table,
                enriched,
                partition_column="campaign_code",
                replace_predicate=f"campaign_code = '{_escape(code)}'",
            )
            published[table] = len(enriched)

    for table, count in published.items():
        log.info("  %-22s %8d row(s)", table, count)
    log.info("Campaign %s published successfully.", code)
    return 0


def _write(
    spark: Any,
    args: argparse.Namespace,
    table: str,
    rows: list[dict[str, Any]],
    *,
    partition_column: str | None,
    replace_predicate: str,
) -> None:
    """Overwrite exactly this campaign's slice of *table*.

    :param partition_column: documents which column the predicate filters on;
        the predicate itself does the scoping, and Delta prunes the partition
        when the column is the table's partition key.

    ``replaceWhere`` scopes the overwrite to one partition, which is what makes
    the job idempotent and safe to re-run: other campaigns are never touched,
    and a re-publish replaces rather than appends.
    """
    fqn = f"{args.catalog}.{args.schema}.{table}"
    target_schema = spark.table(fqn).schema

    if not rows:
        # An empty slice still has to *clear* what was published before, or a
        # deleted line would survive forever in the archive.
        empty = spark.createDataFrame([], target_schema)
        (
            empty.write.format("delta")
            .mode("overwrite")
            .option("replaceWhere", replace_predicate)
            .saveAsTable(fqn)
        )
        return

    frame = spark.createDataFrame(rows)
    # Align to the table's declared schema: column order and types come from the
    # DDL, not from whatever order the SELECT happened to produce.
    projected = frame.selectExpr(
        *[f"CAST({field.name} AS {field.dataType.simpleString()}) AS {field.name}"
          for field in target_schema.fields
          if field.name in frame.columns]
    )
    missing = [f.name for f in target_schema.fields if f.name not in frame.columns]
    if missing:
        from pyspark.sql import functions as F

        for name in missing:
            field = next(f for f in target_schema.fields if f.name == name)
            projected = projected.withColumn(
                name, F.lit(None).cast(field.dataType)
            )
    projected = projected.select(*[f.name for f in target_schema.fields])

    (
        projected.write.format("delta")
        .mode("overwrite")
        .option("replaceWhere", replace_predicate)
        .saveAsTable(fqn)
    )


def _lakebase_conninfo() -> str:
    """Build the Lakebase connection string from the injected environment.

    In a job context the credentials come from the job's own identity; the same
    environment variable names are used as in the app so there is one contract.
    """
    host = os.environ.get("PGHOST")
    database = os.environ.get("PGDATABASE")
    user = os.environ.get("PGUSER")
    password = os.environ.get("PGPASSWORD")

    if not all((host, database, user)):
        raise RuntimeError(
            "PGHOST, PGDATABASE and PGUSER must be set. Attach the Lakebase "
            "database to the job, or export them from a secret scope."
        )
    if not password:
        # Same OAuth-token-as-password mechanism as the app.
        from databricks.sdk import WorkspaceClient

        credential = WorkspaceClient().database.generate_database_credential(
            request_id="publish-campaign", instance_names=[host or ""]
        )
        password = getattr(credential, "token", "")

    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")
    return (
        f"host={host} port={port} dbname={database} user={user} "
        f"password={password} sslmode={sslmode}"
    )


def _escape(value: str) -> str:
    """Escape a literal for a ``replaceWhere`` predicate."""
    return value.replace("'", "''")


if __name__ == "__main__":
    raise SystemExit(main())
