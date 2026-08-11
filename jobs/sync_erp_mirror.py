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

Si le SDK de l'environnement est antérieur à 0.81 — sa version est figée par le
runtime serverless et ne peut pas être relevée — l'hôte Lakebase doit être donné
explicitement ; il se relève une fois dans la console et ne change pas :

    ... --python-params="--pg-host=instance-xxxx.database.cloud.databricks.com"

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

#: ``statut`` (Actif / Inactif) a remplacé le drapeau ``approved`` : la table
#: silver contient désormais toutes les versions d'une nomenclature, et c'est
#: l'application qui n'éclate que celles en vigueur.
BOM_COLUMNS = (
    "parent_itemid", "child_itemid", "child_qty", "child_unitid", "statut",
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
    # Deux échappatoires, pour ne pas dépendre de la découverte quand elle est
    # refusée : l'hôte se lit dans la console Lakebase, et avec un mot de passe
    # sorti d'un secret scope le job n'appelle plus le SDK du tout.
    parser.add_argument(
        "--pg-host", default="",
        help="Hôte Lakebase, si la découverte par la branche est impossible.",
    )
    parser.add_argument(
        "--lakebase-endpoint", default=os.environ.get("INV_LAKEBASE_ENDPOINT", ""),
        help="projects/<p>/branches/<b>/endpoints/<e> — évite d'énumérer.",
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

    import psycopg

    # La connexion et la vérification de forme passent avant la lecture. Chaque
    # échec rencontré jusqu'ici — variables absentes, droits manquants, colonne
    # du miroir non migrée — est arrivé après le chargement complet du
    # référentiel, c'est-à-dire au bout du seul travail coûteux. Ces contrôles
    # tiennent en une seconde ; les faire d'abord, c'est échouer en une seconde.
    try:
        connection = psycopg.connect(_lakebase_conninfo(args))
    except Exception as exc:
        raise RuntimeError(_connection_advice(exc)) from exc

    with connection as conn:
        conn.execute(f"SET search_path TO {args.pg_schema}, public")
        _assert_mirror_shape(conn, "erp_base_article", ITEM_COLUMNS)
        _assert_mirror_shape(conn, "erp_bom", BOM_COLUMNS)

        items = _read(spark, items_fqn, ITEM_COLUMNS, limit=args.limit,
                      unique_on="item_id")
        boms = _read(spark, bom_fqn, BOM_COLUMNS, limit=args.limit)
        log.info("Lu %d articles et %d liens de nomenclature", len(items), len(boms))

        # Écraser un référentiel valide par un vide fait disparaître la
        # possibilité même de lancer une campagne. Un ERP qui ne renvoie rien
        # est une anomalie, pas une mise à jour — et cela vaut pour les deux
        # tables : le remplacement étant intégral, une lecture vide effacerait
        # tout aussi silencieusement les nomenclatures.
        for label, fqn, loaded in (
            ("articles", items_fqn, items),
            ("nomenclatures", bom_fqn, boms),
        ):
            if not loaded:
                log.error(
                    "La table %s (%s) n'a renvoyé aucune ligne — miroir laissé "
                    "intact", fqn, label,
                )
                return 1

        try:
            _swap(conn, "erp_base_article", ITEM_COLUMNS, items,
                  unique_on="item_id")
            _swap(conn, "erp_bom", BOM_COLUMNS, boms)
        except Exception as exc:
            raise RuntimeError(_write_advice(exc, args.pg_schema)) from exc
        conn.commit()

    log.info("Miroir ERP synchronisé (%d articles, %d liens)", len(items), len(boms))
    return 0


def _read(
    spark: Any,
    fqn: str,
    columns: tuple[str, ...],
    *,
    limit: int,
    unique_on: str = "",
) -> list[tuple]:
    """Les colonnes demandées, celles qui manquent renvoyées à NULL.

    Une colonne absente de la table silver ne doit pas arrêter la
    synchronisation : ``statut`` n'existait pas avant que la table porte toutes
    les versions, et l'application traite son absence comme « en vigueur ».

    ``unique_on`` déduplique à la source. La table des articles a livré deux
    lignes pour le même ``item_id`` — le programme y est calculé en cascade, et
    une remontée de nomenclature peut faire éventail — ce qui violait la clé
    primaire du miroir en fin de chargement. Cette clé n'est pas une contrainte
    de confort : un article y est une ligne, et l'application lit le miroir en
    supposant exactement cela. On déduplique donc plutôt que de la lever, de
    façon déterministe pour que deux exécutions donnent le même miroir, et le
    nombre de lignes écartées est journalisé — c'est une anomalie de la source,
    pas une routine.
    """
    available = {f.name.lower() for f in spark.table(fqn).schema.fields}
    missing = [c for c in columns if c.lower() not in available]
    if missing:
        log.warning("%s : colonnes absentes, copiées à NULL — %s", fqn, ", ".join(missing))

    projection = ", ".join(
        c if c.lower() in available else f"CAST(NULL AS STRING) AS {c}" for c in columns
    )
    query = f"SELECT {projection} FROM {fqn}"
    if unique_on and unique_on.lower() in available:
        order = ", ".join(columns)
        query = (
            f"SELECT {', '.join(columns)} FROM ("
            f"  SELECT {projection}, ROW_NUMBER() OVER ("
            f"    PARTITION BY {unique_on} ORDER BY {order}"
            f"  ) AS _rang FROM {fqn}"
            f") WHERE _rang = 1"
        )
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = [tuple(row) for row in spark.sql(query).collect()]
    if unique_on:
        total = spark.table(fqn).count()
        if total > len(rows):
            log.warning(
                "%s : %d ligne(s) en double sur %s, une seule conservée par clé",
                fqn, total - len(rows), unique_on,
            )
    return rows


def _swap(
    conn: Any,
    table: str,
    columns: tuple[str, ...],
    rows: list[tuple],
    *,
    unique_on: str = "",
) -> None:
    """Remplit une table temporaire puis substitue, dans une seule transaction.

    Le ``TRUNCATE`` et l'``INSERT`` partagent la transaction ouverte par le
    contexte appelant : à aucun moment l'application ne voit un miroir vide ou
    à moitié rempli.

    ``unique_on`` filtre une dernière fois à l'insertion. La déduplication a
    déjà eu lieu à la lecture, mais c'est ici que l'échec coûte le plus cher :
    il survient après le chargement complet, sur la dernière instruction. Deux
    mots de SQL rendent la violation impossible plutôt que rare.
    """
    staging = f"{table}_staging"
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    distinct = f"DISTINCT ON ({unique_on}) " if unique_on else ""

    conn.execute(f"CREATE TEMP TABLE {staging} (LIKE {table} INCLUDING DEFAULTS) "
                 "ON COMMIT DROP")
    with conn.cursor() as cur:
        for start in range(0, len(rows), BATCH):
            cur.executemany(
                f"INSERT INTO {staging} ({names}) VALUES ({placeholders})",
                rows[start:start + BATCH],
            )
    before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    conn.execute(f"TRUNCATE {table}")
    conn.execute(
        f"INSERT INTO {table} ({names}, synced_at) "
        f"SELECT {distinct}{names}, now() FROM {staging}"
        + (f" ORDER BY {unique_on}, {names}" if unique_on else "")
    )
    after = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    # Le remplacement est intégral, pas un ajout : le dire en chiffres évite
    # d'avoir à le déduire. Une référence retirée de l'ERP doit disparaître du
    # miroir, et rien à l'écran ne le montrait.
    log.info("%s : %d ligne(s) supprimée(s), %d écrite(s)", table, before, after)


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
    host = os.environ.get("PGHOST") or args.pg_host
    database = os.environ.get("PGDATABASE") or args.pg_database
    user = os.environ.get("PGUSER") or args.pg_user
    password = os.environ.get("PGPASSWORD")

    if not (host and user and password):
        client = client or _workspace_client()
        log.info("SDK Databricks %s", _sdk_version())
        user = user or _current_identity(client)
        api = getattr(client, "postgres", None)

        name = args.lakebase_endpoint
        if not host:
            if api is None:
                raise RuntimeError(
                    "Hôte Lakebase inconnu, et le SDK de cet environnement ne "
                    f"connaît pas l'API Lakebase Autoscaling (version "
                    f"{_sdk_version()}, w.postgres apparaît en 0.81). Sa version "
                    "est figée par le runtime serverless : passez --pg-host, "
                    "relevé dans la console Lakebase."
                )
            found, resolved_host = _read_write_endpoint(api, args.branch)
            name = name or found
            host = resolved_host

        password = password or _password(client, api, name)
        log.info("Lakebase : hôte %s, identité %s, base %s", host, user, database)

    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")
    return (
        f"host={host} port={port} dbname={database} user={user} "
        f"password={password} sslmode={sslmode}"
    )


def _assert_mirror_shape(conn: Any, table: str, columns: tuple[str, ...]) -> None:
    """Refuse to start unless the mirror has the columns about to be written.

    The mirror's tables belong to the application, which creates and migrates
    them at start-up; this job only fills them. When the two get out of step —
    a column added to the source and to the application, but the application not
    yet redeployed — Postgres refuses the very last statement, after the whole
    referential has been read and shipped. Asking the catalogue first turns that
    into an immediate, self-explanatory stop.
    """
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s",
        (table,),
    ).fetchall()
    present = {str(r[0]).lower() for r in rows}
    if not present:
        raise RuntimeError(
            f"La table « {table} » n'existe pas dans le schéma du miroir. "
            "Démarrez l'application une fois : c'est elle qui crée et fait "
            "évoluer ces tables."
        )
    missing = [c for c in columns if c.lower() not in present]
    if missing:
        raise RuntimeError(
            f"Le miroir « {table} » n'a pas la ou les colonnes {', '.join(missing)}. "
            "Elles arrivent avec une migration de l'application : redéployez-la "
            "et laissez-la démarrer une fois, puis relancez cette "
            "synchronisation."
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


def _sdk_version() -> str:
    """La version du SDK, dans le journal du job.

    L'environnement serverless d'un job n'est pas celui de l'App : il apporte sa
    propre version du SDK, et une API absente s'y présente comme une erreur
    d'attribut au milieu d'un appel. Une ligne de journal rend la question
    tranchable en un coup d'œil au lieu d'un aller-retour.
    """
    try:
        from importlib.metadata import version

        return version("databricks-sdk")
    except Exception:  # pragma: no cover — dépend de l'environnement
        return "inconnue"


def _password(client: Any, api: Any, endpoint: str) -> str:
    """Le mot de passe Postgres, par ordre de préférence décroissante.

    Le credential dédié de ``w.postgres`` est le meilleur : il porte sur un
    endpoint précis et expire vite. Mais cette API n'existe qu'à partir de
    databricks-sdk 0.81, et **la version du SDK ne peut pas être relevée dans un
    job** : elle figure dans les contraintes immuables du runtime serverless, si
    bien qu'en demander une autre fait échouer l'installation entière — c'est ce
    qui est arrivé.

    Le repli est le jeton OAuth de l'identité qui exécute le job, que Lakebase
    accepte comme mot de passe. Moins ciblé, disponible partout. Un job qui
    refuserait de tourner faute d'une dépendance impossible à satisfaire ne
    serait utile à personne.
    """
    if api is not None and endpoint:
        return _mint(api, endpoint)

    for source in ("oauth_token", "token"):
        try:
            credential = getattr(client.config, source)()
        except Exception:  # pragma: no cover — dépend de la version du SDK
            continue
        token = getattr(credential, "access_token", None) or getattr(
            credential, "token", None
        )
        if token:
            log.info("Authentification par jeton OAuth (%s)", source)
            return str(token)

    raise RuntimeError(
        "Aucun moyen d'authentifier la connexion Lakebase : ni credential "
        f"dédié (SDK {_sdk_version()}, w.postgres apparaît en 0.81, et sa "
        "version est figée par le runtime), ni jeton OAuth. Exportez "
        "PGPASSWORD depuis un secret scope."
    )


def _read_write_endpoint(api: Any, branch: str) -> tuple[str, str]:
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
        endpoints = list(api.list_endpoints(branch))
    except Exception as exc:
        # La cause décide du geste — droits manquants, branche inexistante,
        # méthode absente d'un SDK plus ancien — et elle ne doit donc jamais
        # être avalée : sans elle, les trois se ressemblent.
        raise RuntimeError(
            f"Impossible de lister les endpoints de « {branch} » : "
            f"{type(exc).__name__}: {exc}. Vérifiez que la branche existe et que "
            "l'identité qui exécute le job a accès au projet Lakebase ; à défaut, "
            "passez --lakebase-endpoint et --pg-host."
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


def _mint(api: Any, endpoint: str) -> str:
    """Un credential Lakebase, comme l'application en demande un.

    ``postgres.generate_database_credential`` prend le chemin de ressource d'un
    endpoint. L'API ``database.*`` de l'ancien palier provisionné, appelée avec
    un nom d'hôte, échoue par « Database instance not found » — elle n'est pas
    tentée.
    """
    try:
        credential = api.generate_database_credential(endpoint)
    except Exception as exc:
        raise RuntimeError(
            f"Credential Lakebase refusé pour « {endpoint} » : "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    token = getattr(credential, "token", None)
    if not token:
        raise RuntimeError("Databricks n'a pas renvoyé de credential Lakebase.")
    return str(token)


if __name__ == "__main__":
    sys.exit(main())
