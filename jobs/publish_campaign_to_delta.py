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

La partition est l'identifiant, jamais le code
----------------------------------------------
Les tables étaient partitionnées par ``campaign_code``. Or un code est une
valeur métier : il se réutilise, et l'application ne supprime que logiquement.
Créer une campagne « INV-2026-06 » après en avoir retiré une du même nom faisait
donc écraser l'archive de la première par les données de la seconde, en silence
et sans recours — l'archive étant précisément ce qui reste quand la base
opérationnelle a évolué. ``campaign_id`` est un UUID, immuable et jamais
réattribué : c'est lui qui porte la partition et le prédicat de remplacement.
Le code reste une colonne, pour qu'un humain s'y retrouve.

Ce que la publication garantit, et ce qu'elle ne garantit pas
--------------------------------------------------------------
Les tables sont écrites l'une après l'autre. Une panne au milieu laisse donc
quelques tables à la nouvelle version et les autres à l'ancienne — Delta n'offre
pas de transaction couvrant plusieurs tables.

Ce qui est garanti, c'est qu'une publication incomplète ne se **fait pas passer**
pour complète. La table ``publication`` est écrite en dernier, avec le décompte
de chaque table ; rien d'autre ne la met à jour. Une campagne est publiée si, et
seulement si, elle y figure. Une exécution interrompue ne laisse aucune ligne de
manifeste, et la reprise réécrit chaque table par-dessus la précédente puisque
tout passe par ``replaceWhere`` sur le même identifiant.

Ce n'est pas l'écriture en zone de transit décrite par l'audit — elle
supposerait de doubler chaque table — mais elle en donne la propriété qui compte
au lecteur : ne jamais lire pour complet un dossier qui ne l'est pas.
"""

from __future__ import annotations

import argparse
import datetime as dt
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
# Un `spark_python_task` matérialise le fichier sur le driver ; son voisin
# `lakebase.py` est là, mais le répertoire n'est pas toujours sur le chemin
# d'import selon la façon dont le job est lancé.
#
# `__file__` ne sert à rien pour le trouver : le calcul serverless exécute le
# fichier par `exec(compile(source, chemin, "exec"))` dans un espace de noms
# ipykernel, où ce global n'existe pas. Le job échouait sur un `NameError` avant
# d'avoir lu sa première option. Le chemin passé à `compile`, lui, est toujours
# renseigné — c'est celui qu'affiche la trace — et `co_filename` le porte.
#
# Le bloc est recopié dans `sync_erp_mirror.py` : c'est lui qui met le
# répertoire sur le chemin d'import, il ne peut donc pas en venir.


def _neighbourhood(neighbour: str) -> str | None:
    """Le répertoire de ce fichier, s'il porte bien ``neighbour``.

    Rien n'est ajouté au chemin d'import sur la foi d'un chemin seul : un
    ``co_filename`` valant ``<string>`` désignerait le répertoire courant, et
    l'ajouter en tête du chemin d'import est une surprise que personne n'a
    demandée. La présence du voisin est la preuve qu'on cherche.
    """
    for candidate in (
        globals().get("__file__"),
        inspect.currentframe().f_code.co_filename,
    ):
        if not candidate:
            continue
        here = Path(candidate).resolve().parent
        if (here / neighbour).exists():
            return str(here)
    return None


_HERE = _neighbourhood("lakebase.py")
if _HERE is not None and _HERE not in sys.path:
    sys.path.insert(0, _HERE)

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
    """Les lignes d'une requête, en dictionnaires.

    La connexion est ouverte avec ``row_factory=dict_row`` : le curseur rend
    **déjà** des dictionnaires. Les rezipper avec les noms de colonnes ne
    lisait donc pas les valeurs — itérer un dictionnaire rend ses **clés** —
    et chaque champ recevait le nom de sa propre colonne :

        dict(zip(["code", "count_date"], {"code": "TRY1", "count_date": ...}))
        → {"code": "code", "count_date": "count_date"}

    Le `strict=True` ne voyait rien, les longueurs étant égales. La publication
    s'arrêtait sur la première colonne non textuelle — « la valeur
    'count_date' ne peut pas être convertie en DATE » — après avoir accepté
    sans broncher toutes les colonnes de texte qui la précédaient. Sur un
    schéma entièrement textuel, elle aurait publié une archive de noms de
    colonnes en se déclarant réussie, sur une partition ``campaign_id =
    'campaign_id'``.

    Les lignes en tuple restent traitées, pour que la fonction ne dépende pas
    d'un réglage posé ailleurs — c'est cette dépendance tacite qui a produit le
    défaut.
    """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    if rows and isinstance(rows[0], dict):
        return [dict(row) for row in rows]
    columns = [c.name for c in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", default=os.environ.get("INV_UC_CATALOG", "emotors_data_champions")
    )
    parser.add_argument("--schema", default=os.environ.get("INV_UC_SCHEMA", "inventory"))
    parser.add_argument("--campaign-code", required=True)
    # Ce qu'un job doit recevoir pour joindre Lakebase : la branche, d'où tout
    # le reste se déduit. Les mêmes noms que le job de synchronisation, parce
    # que c'est le même module qui les lit.
    parser.add_argument(
        "--branch",
        default=os.environ.get("INV_LAKEBASE_BRANCH", ""),
        help="Branche Lakebase, ex. projects/<projet>/branches/<branche>",
    )
    parser.add_argument(
        "--lakebase-endpoint",
        default="",
        help="Endpoint en écriture, déduit de la branche sinon",
    )
    parser.add_argument("--pg-host", default="", help="Court-circuite la découverte")
    parser.add_argument(
        "--pg-database", default=os.environ.get("PGDATABASE", "databricks_postgres")
    )
    parser.add_argument("--pg-user", default="", help="Identité du job sinon")
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

    # Avant d'ouvrir quoi que ce soit : les tables cibles existent-elles ?
    #
    # `publication` est écrite en dernier. Une table manquante ne se découvrait
    # donc qu'après avoir lu la campagne entière et écrit les neuf autres —
    # l'échec le plus coûteux possible, puisqu'il arrive au bout du travail
    # utile. Le job de synchronisation vérifie la forme du miroir avant de lire
    # l'ERP, pour exactement cette raison ; celui-ci ne vérifiait rien.
    missing = _missing_tables(spark, args, [*QUERIES, "publication"])
    if missing:
        log.error(
            "Tables absentes de %s.%s : %s. Le schéma Unity Catalog n'est pas à "
            "jour. Rejouez « make uc WAREHOUSE_ID=<id> PROFILE=<profil> » : "
            "sql/00_unity_catalog.sql est en CREATE TABLE IF NOT EXISTS, les "
            "tables déjà présentes ne sont pas touchées.",
            args.catalog,
            args.schema,
            ", ".join(missing),
        )
        return 3

    conninfo = _lakebase_conninfo(args)
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
        # Le prédicat porte l'identifiant, pas le code : un code se réutilise,
        # un UUID non. Voir l'en-tête du module.
        campaign_slice = f"campaign_id = '{_escape(campaign_id)}'"

        # ---- the campaign row itself -------------------------------------
        _write(
            spark,
            args,
            "campaign",
            [{**campaign, "published_at": published_at}],
            partition_column=None,
            replace_predicate=campaign_slice,
        )
        published["campaign"] = 1

        # ---- everything partitioned by campaign_id ------------------------
        for table, query in QUERIES.items():
            if table == "campaign":
                continue
            rows = fetch(cur, query, {"campaign_id": campaign_id})
            enriched = [
                {
                    **row,
                    "campaign_code": code,
                    "published_at": published_at,
                    **(
                        {"count_date": count_date}
                        if table in ("book_stock_snapshot", "count_result")
                        else {}
                    ),
                }
                for row in rows
            ]
            _write(
                spark,
                args,
                table,
                enriched,
                partition_column="campaign_id",
                replace_predicate=campaign_slice,
            )
            published[table] = len(enriched)

        # ---- le manifeste, écrit en dernier -------------------------------
        #
        # C'est lui qui rend la publication visible. Tant qu'il n'est pas écrit,
        # la campagne n'est pas publiée — quelles que soient les tables déjà
        # remplies. Une exécution interrompue ne laisse donc pas un dossier
        # à moitié rempli qui se présente comme complet.
        _write(
            spark,
            args,
            "publication",
            [manifest(campaign_id, code, published_at, published)],
            partition_column=None,
            replace_predicate=campaign_slice,
        )

        # ---- et l'application l'apprend -----------------------------------
        #
        # Le job écrit dans Delta, l'application lit Lakebase : les deux ne se
        # parlaient pas, si bien qu'une campagne pouvait être clôturée sans la
        # moindre preuve que son archive existe. Cette écriture, sur la
        # connexion déjà ouverte, est ce que la clôture consultera.
        #
        # Après le manifeste, et jamais avant : c'est lui qui fait foi.
        cur.execute(
            "UPDATE inventory.campaign SET published_at = %(at)s WHERE id = %(id)s",
            {"at": published_at, "id": campaign_id},
        )
        conn.commit()

    for table, count in published.items():
        log.info("  %-22s %8d row(s)", table, count)
    log.info("Campaign %s published successfully.", code)
    return 0


def manifest(
    campaign_id: str,
    code: str,
    published_at: dt.datetime,
    published: dict[str, int],
) -> dict[str, Any]:
    """La ligne qui déclare la publication complète.

    ``row_counts`` porte le décompte table par table : c'est ce qui permet de
    répondre à « l'archive est-elle fidèle » sans relire les neuf tables, et de
    voir tout de suite qu'une campagne archivée avec zéro ligne de comptage est
    une anomalie plutôt qu'une campagne vide.
    """
    return {
        "campaign_id": campaign_id,
        "campaign_code": code,
        "published_at": published_at,
        "engine_version": os.environ.get("INV_ENGINE_VERSION", ""),
        "table_count": len(published),
        "row_total": sum(published.values()),
        "row_counts": dict(sorted(published.items())),
    }


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

    # Une colonne vide partout n'a pas de type déductible, et Spark refuse la
    # construction entière — `CANNOT_DETERMINE_TYPE` — plutôt que de deviner.
    # Or c'est le cas ordinaire : une campagne en comptage n'a ni `closed_at`,
    # ni `counting_frozen_at`. Ces colonnes sont donc retirées ici et remises
    # plus bas par la branche « colonnes absentes », qui leur donne le type de
    # la table. La valeur écrite est la même — NULL — mais elle est typée.
    empty_columns = _always_null(rows)
    if empty_columns:
        rows = [
            {name: value for name, value in row.items() if name not in empty_columns}
            for row in rows
        ]

    if rows[0]:
        frame = spark.createDataFrame(rows)
    else:
        # Tout est vide : plus rien à inférer, la table décide seule.
        frame = spark.createDataFrame([{} for _ in rows], target_schema)
    # Align to the table's declared schema: column order and types come from the
    # DDL, not from whatever order the SELECT happened to produce.
    projected = frame.selectExpr(
        *[
            f"CAST({field.name} AS {field.dataType.simpleString()}) AS {field.name}"
            for field in target_schema.fields
            if field.name in frame.columns
        ]
    )
    missing = [f.name for f in target_schema.fields if f.name not in frame.columns]
    if missing:
        from pyspark.sql import functions as F

        for name in missing:
            field = next(f for f in target_schema.fields if f.name == name)
            projected = projected.withColumn(name, F.lit(None).cast(field.dataType))
    projected = projected.select(*[f.name for f in target_schema.fields])

    (
        projected.write.format("delta")
        .mode("overwrite")
        .option("replaceWhere", replace_predicate)
        .saveAsTable(fqn)
    )


def _missing_tables(spark: Any, args: Any, tables: list[str]) -> list[str]:
    """Celles qui n'existent pas encore dans Unity Catalog, dans l'ordre.

    Toutes sont interrogées, jamais seulement la première : un exploitant qui
    doit rejouer le script veut savoir ce qui manque, pas le découvrir table
    après table.
    """
    return [
        table
        for table in tables
        if not spark.catalog.tableExists(f"{args.catalog}.{args.schema}.{table}")
    ]


def _always_null(rows: list[dict[str, Any]]) -> set[str]:
    """Les colonnes qui ne portent aucune valeur, sur aucune ligne.

    Ce sont celles dont Spark ne peut rien déduire. Une seule suffit à faire
    refuser la construction du DataFrame entier, et elles sont ordinaires :
    une campagne en cours de comptage n'a pas de date de clôture.
    """
    names = {name for row in rows for name in row}
    return {name for name in names if all(row.get(name) is None for row in rows)}


def _lakebase_conninfo(args: argparse.Namespace) -> str:
    """Chaîne de connexion Lakebase — voir :mod:`lakebase`.

    Ce job attendait ``PGHOST`` / ``PGDATABASE`` / ``PGUSER`` dans son
    environnement, comme l'application. Un job n'est pas une App et n'a pas de
    ressource attachée : il ne les recevait donc jamais, et s'arrêtait au
    premier lancement sur un message que rien dans le bundle n'aurait pu
    satisfaire. Son repli appelait de surcroît
    ``w.database.generate_database_credential`` — l'API du palier
    *provisionné* — sur un projet Lakebase Autoscaling.

    Le job de synchronisation avait déjà été corrigé ; celui-ci ne l'était pas.
    Les deux importent maintenant la même découverte d'endpoint.
    """
    from lakebase import conninfo

    return conninfo(args)


def _escape(value: str) -> str:
    """Escape a literal for a ``replaceWhere`` predicate."""
    return value.replace("'", "''")


if __name__ == "__main__":
    raise SystemExit(main())
