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
        "--branch", default=os.environ.get("INV_LAKEBASE_BRANCH", ""),
        help="projects/<projet>/branches/<branche> — d'où l'endpoint est déduit.",
    )
    parser.add_argument(
        "--pg-database", default=os.environ.get("PGDATABASE", "databricks_postgres"),
        help="Nom Postgres de la base (souligné), pas l'id de ressource.",
    )
    parser.add_argument(
        "--pg-user", default=os.environ.get("PGUSER", ""),
        help="Rôle Postgres. Par défaut l'identité qui exécute le job.",
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

    try:
        connection = psycopg.connect(_lakebase_conninfo(args))
    except Exception as exc:
        raise RuntimeError(_connection_advice(exc)) from exc

    with connection as conn:
        conn.execute(f"SET search_path TO {args.pg_schema}, public")
        try:
            _swap(conn, "erp_base_article", ITEM_COLUMNS, items)
            _swap(conn, "erp_bom", BOM_COLUMNS, boms)
        except Exception as exc:
            raise RuntimeError(_write_advice(exc, args.pg_schema)) from exc
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


def _lakebase_conninfo(args: Any, client: Any = None) -> str:
    """Chaîne de connexion Lakebase, découverte plutôt qu'attendue.

    Une App Databricks reçoit PGHOST / PGDATABASE / PGUSER de la plateforme
    parce qu'une ressource ``postgres`` lui est attachée. **Un job n'en reçoit
    rien** : ce n'est pas une App, et il n'a pas de ressources. La première
    version de ce fichier reprenait le contrat de l'application et s'arrêtait
    donc net au premier lancement.

    Ce que le job connaît, c'est la branche Lakebase — passée en paramètre,
    construite depuis les mêmes variables de bundle que la ressource de l'App,
    si bien que les deux ne peuvent pas désigner des branches différentes. Le
    reste s'en déduit : l'endpoint en écriture, son hôte, l'identité qui exécute
    le job, et un credential OAuth frais.

    Les variables d'environnement restent prioritaires quand elles sont là :
    exécution locale, ou secret scope pour un rôle Postgres dédié.
    """
    host = os.environ.get("PGHOST")
    database = os.environ.get("PGDATABASE") or args.pg_database
    user = os.environ.get("PGUSER") or args.pg_user
    password = os.environ.get("PGPASSWORD")

    if not (host and user and password):
        client = client or _workspace_client()
        name, resolved_host = _read_write_endpoint(client, args.branch)
        host = host or resolved_host
        user = user or _current_identity(client)
        password = password or _mint(client, name)
        log.info("Lakebase : endpoint %s, hôte %s, identité %s", name, host, user)

    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")
    return (
        f"host={host} port={port} dbname={database} user={user} "
        f"password={password} sslmode={sslmode}"
    )


def _connection_advice(exc: Exception) -> str:
    """Ce qu'il faut faire, plutôt que le message brut de libpq.

    Les deux échecs attendus au premier lancement se ressemblent à l'écran et
    n'ont pas du tout le même remède : une identité sans rôle Postgres, et une
    identité qui n'a pas le droit de se connecter à cette base.
    """
    message = str(exc)
    if "does not exist" in message and "role" in message.lower():
        return (
            "L'identité qui exécute le job n'a pas de rôle Postgres dans la base "
            "Lakebase. Ajoutez-la comme rôle de base de données (console Lakebase "
            f"→ le projet → Roles), puis relancez. Détail : {message}"
        )
    if "password authentication" in message or "authentication failed" in message:
        return (
            "Authentification Lakebase refusée. Le credential est minté pour "
            "l'identité du job : vérifiez qu'elle a bien CAN_CONNECT sur la base. "
            f"Détail : {message}"
        )
    return f"Connexion à Lakebase impossible : {message}"


def _write_advice(exc: Exception, schema: str) -> str:
    message = str(exc)
    if "permission denied" in message.lower():
        return (
            "Le job n'a pas les droits d'écriture sur le miroir. Les tables "
            f"appartiennent au service principal de l'App ; la migration 006 "
            f"({schema}) les ouvre à l'identité de synchronisation — redéployez "
            f"l'App pour qu'elle s'applique, puis relancez. Détail : {message}"
        )
    return f"Écriture du miroir impossible : {message}"


def _workspace_client() -> Any:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _read_write_endpoint(client: Any, branch: str) -> tuple[str, str]:
    """Le chemin de ressource de l'endpoint en écriture, et son hôte.

    On écrit : un endpoint en lecture seule ferait échouer la synchronisation
    au premier INSERT, après avoir lu tout le référentiel.
    """
    if not branch:
        raise RuntimeError(
            "Branche Lakebase inconnue : passez --branch "
            "projects/<projet>/branches/<branche>, ou exportez PGHOST, PGUSER "
            "et PGPASSWORD depuis un secret scope."
        )
    try:
        endpoints = list(client.postgres.list_endpoints(branch))
    except Exception as exc:
        raise RuntimeError(
            f"Impossible de lister les endpoints de « {branch} ». L'identité qui "
            "exécute le job a-t-elle accès au projet Lakebase ?"
        ) from exc

    for endpoint in endpoints:
        status = getattr(endpoint, "status", None)
        if "READ_WRITE" not in str(getattr(status, "endpoint_type", "")):
            continue
        hosts = getattr(status, "hosts", None)
        host = getattr(hosts, "host", None) or getattr(
            hosts, "read_write_pooled_host", None
        )
        if host:
            return endpoint.name, str(host)

    raise RuntimeError(
        f"Aucun endpoint en écriture sur « {branch} ». Vérifiez la branche, ou "
        "exportez PGHOST / PGUSER / PGPASSWORD depuis un secret scope."
    )


def _current_identity(client: Any) -> str:
    """Le rôle Postgres de l'identité qui exécute le job.

    Lakebase authentifie une identité Databricks sous son propre nom : l'adresse
    e-mail pour une personne, l'application id pour un service principal.
    """
    try:
        name = client.current_user.me().user_name
    except Exception as exc:
        raise RuntimeError(
            "Impossible de déterminer l'identité qui exécute le job ; "
            "passez --pg-user."
        ) from exc
    if not name:
        raise RuntimeError("Identité sans nom d'utilisateur ; passez --pg-user.")
    return str(name)


def _mint(client: Any, endpoint: str) -> str:
    """Un credential Lakebase, comme l'application en demande un.

    ``postgres.generate_database_credential`` prend le chemin de ressource d'un
    endpoint. L'API ``database.*`` de l'ancien palier provisionné, appelée avec
    un nom d'hôte, échoue par « Database instance not found » — elle n'est pas
    tentée.
    """
    credential = client.postgres.generate_database_credential(endpoint)
    token = getattr(credential, "token", None)
    if not token:
        raise RuntimeError("Databricks n'a pas renvoyé de credential Lakebase.")
    return str(token)


if __name__ == "__main__":
    sys.exit(main())
