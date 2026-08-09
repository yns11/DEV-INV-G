"""Copie les tables ERP d'Unity Catalog vers le miroir local de l'application.

Pourquoi ce job existe
----------------------
Lire les tables silver directement depuis l'application suppose que *son*
service principal ait ``USE CATALOG`` sur le catalogue de l'ERP. Ce privilège
ne s'accorde que par un propriétaire du catalogue. Quand aucun n'est joignable,
l'inventaire, lui, garde sa date.

Ce job renverse la contrainte : il tourne avec l'identité qui *a* déjà le droit
de lire l'ERP — la vôtre, ou un service principal de plateforme — et dépose une
copie dans la base Lakebase de l'application, où celle-ci est chez elle. Aucun
grant Unity Catalog n'est demandé à personne.

Ce qu'il copie, et ce qu'il ne fait pas
--------------------------------------
Une copie **brute** : les colonnes gardent les noms de l'ERP. La traduction en
vocabulaire de campagne (groupe fonctionnel → type d'article, prix ramené à
l'unité, « Commun » → COMMON) reste dans ``inventory.ingest.erp``, exécutée à
l'import comme pour une lecture directe. Deux vocabulaires finiraient par
diverger ; il n'y en a qu'un.

Le remplacement est **atomique** : chargement dans une table temporaire, puis
substitution dans une seule transaction. Un job interrompu laisse le miroir
précédent intact plutôt qu'un référentiel à moitié écrit — sur lequel une
campagne partirait sans rien remarquer.

Exécution
---------
    databricks bundle run inventory_sync_erp_mirror -t prod

ou en local, contre les mêmes variables d'environnement que l'application :

    python jobs/sync_erp_mirror.py --catalog emotors_data_champions \\
        --schema silver_erp_ye
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

log = logging.getLogger("sync_erp_mirror")

#: Colonnes copiées, dans l'ordre des tables du miroir (migration 005). Elles
#: reprennent les noms de l'ERP : c'est ce qui permet à l'application d'appliquer
#: la même traduction, qu'elle lise le catalogue ou la copie.
ITEM_COLUMNS = (
    "item_id", "item_name", "item_description", "search_name", "name_alias",
    "categorie", "programme", "item_group_id", "item_group_label",
    "std_cost_price", "std_price_unit", "std_unit",
)

BOM_COLUMNS = (
    "parent_itemid", "child_itemid", "child_qty", "child_unitid", "approved",
)

#: Nombre de lignes envoyées par ordre d'insertion. Assez grand pour que le
#: référentiel entier passe en quelques dizaines d'allers-retours, assez petit
#: pour ne pas construire une requête de plusieurs mégaoctets.
BATCH = 5_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", default=os.environ.get("INV_ERP_CATALOG", "emotors_data_champions")
    )
    parser.add_argument(
        "--schema", default=os.environ.get("INV_ERP_UC_SCHEMA", "silver_erp_ye")
    )
    parser.add_argument("--items-table", default="silver_base_article")
    parser.add_argument("--bom-table", default="silver_bom")
    parser.add_argument(
        "--pg-schema", default=os.environ.get("INV_PG_SCHEMA", "inventory")
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Tronque la copie (0 = tout). Pour un essai, pas pour la production.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()

    items_fqn = f"{args.catalog}.{args.schema}.{args.items_table}"
    bom_fqn = f"{args.catalog}.{args.schema}.{args.bom_table}"

    items = _read(spark, items_fqn, ITEM_COLUMNS, limit=args.limit)
    boms = _read(spark, bom_fqn, BOM_COLUMNS, limit=args.limit)
    log.info("Lu %d articles et %d liens de nomenclature", len(items), len(boms))

    if not items:
        # Écraser un référentiel valide par un vide fait disparaître la
        # possibilité même de lancer une campagne. Un ERP qui ne renvoie rien
        # est une anomalie, pas une mise à jour.
        log.error("La table %s n'a renvoyé aucune ligne — miroir laissé intact", items_fqn)
        return 1

    import psycopg

    with psycopg.connect(_lakebase_conninfo()) as conn:
        conn.execute(f"SET search_path TO {args.pg_schema}, public")
        _swap(conn, "erp_base_article", ITEM_COLUMNS, items)
        _swap(conn, "erp_bom", BOM_COLUMNS, boms)
        conn.commit()

    log.info("Miroir ERP synchronisé (%d articles, %d liens)", len(items), len(boms))
    return 0


def _read(spark: Any, fqn: str, columns: tuple[str, ...], *, limit: int) -> list[tuple]:
    """Les colonnes demandées, celles qui manquent renvoyées à NULL.

    Une colonne absente de la table silver ne doit pas arrêter la
    synchronisation : ``approved`` en particulier n'existe pas partout, et
    l'application sait la traiter comme inconnue.
    """
    available = {f.name.lower() for f in spark.table(fqn).schema.fields}
    missing = [c for c in columns if c.lower() not in available]
    if missing:
        log.warning("%s : colonnes absentes, copiées à NULL — %s", fqn, ", ".join(missing))

    projection = ", ".join(
        c if c.lower() in available else f"CAST(NULL AS STRING) AS {c}" for c in columns
    )
    query = f"SELECT {projection} FROM {fqn}"
    if limit:
        query += f" LIMIT {int(limit)}"
    return [tuple(row) for row in spark.sql(query).collect()]


def _swap(conn: Any, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
    """Remplit une table temporaire puis substitue, dans une seule transaction.

    Le ``TRUNCATE`` et l'``INSERT`` partagent la transaction ouverte par le
    contexte appelant : à aucun moment l'application ne voit un miroir vide ou
    à moitié rempli.
    """
    staging = f"{table}_staging"
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    conn.execute(f"CREATE TEMP TABLE {staging} (LIKE {table} INCLUDING DEFAULTS) "
                 "ON COMMIT DROP")
    with conn.cursor() as cur:
        for start in range(0, len(rows), BATCH):
            cur.executemany(
                f"INSERT INTO {staging} ({names}) VALUES ({placeholders})",
                rows[start:start + BATCH],
            )
    conn.execute(f"TRUNCATE {table}")
    conn.execute(
        f"INSERT INTO {table} ({names}, synced_at) "
        f"SELECT {names}, now() FROM {staging}"
    )
    log.info("%s : %d lignes", table, len(rows))


def _lakebase_conninfo() -> str:
    """Chaîne de connexion Lakebase, construite comme celle de l'application.

    Mêmes noms de variables que dans l'app : un seul contrat, et un job qui se
    configure comme elle. Sans mot de passe, un credential OAuth est demandé
    pour l'identité qui exécute le job.
    """
    host = os.environ.get("PGHOST")
    database = os.environ.get("PGDATABASE")
    user = os.environ.get("PGUSER")
    password = os.environ.get("PGPASSWORD")

    if not all((host, database, user)):
        raise RuntimeError(
            "PGHOST, PGDATABASE et PGUSER doivent être définis. Attachez la base "
            "Lakebase au job, ou exportez-les depuis un secret scope."
        )
    if not password:
        from databricks.sdk import WorkspaceClient

        credential = WorkspaceClient().database.generate_database_credential(
            request_id="sync-erp-mirror", instance_names=[host or ""]
        )
        password = getattr(credential, "token", "")

    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")
    return (
        f"host={host} port={port} dbname={database} user={user} "
        f"password={password} sslmode={sslmode}"
    )


if __name__ == "__main__":
    sys.exit(main())
