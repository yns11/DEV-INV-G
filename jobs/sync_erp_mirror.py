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

import sys
from pathlib import Path

# Un `spark_python_task` matérialise le fichier sur le driver ; son voisin
# `lakebase.py` est là, mais le répertoire n'est pas toujours sur le chemin
# d'import selon la façon dont le job est lancé.
sys.path.insert(0, str(Path(__file__).resolve().parent))

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

#: L'écart backflush, à la maille semaine — celle de la source. Un job ne peut
#: pas pré-agréger sur une période qu'il ignore : les bornes sont choisies
#: campagne par campagne. Seules les colonnes que l'application lit sont
#: copiées ; la table gold en porte une vingtaine d'autres.
BACKFLUSH_COLUMNS = (
    "semaine_debut", "parent_itemid", "child_itemid", "child_name", "child_unite",
    "qty_parent_produite", "conso_theorique", "conso_reelle", "ecart_brut",
    "loaded_at",
)

#: Les mouvements de stock : une ligne par référence et par jour, une colonne
#: par flux. Les cinq mesures de la vue Comparaison en sortent, ce qui remplace
#: les trois tables par domaine qu'elle interrogeait auparavant.
MOVEMENT_COLUMNS = (
    "reference", "date_mouvement", "reception", "expedition", "production",
    "conso_theorique", "consommation", "rebut",
)

#: Le snapshot quotidien du stock physique : une ligne par article × entrepôt ×
#: emplacement, pour un jour donné. Seule la photo la plus récente est copiée —
#: c'est un état, pas un historique, et l'application n'en lit qu'un jour.
STOCK_COLUMNS = (
    "item_id", "entrepot", "emplacement", "stock_physique", "unite",
    "snapshot_date",
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
    # La table de faits est publiée par un autre pipeline, dans son propre
    # schéma : la rattacher au schéma silver ferait qu'un renommage de l'un
    # casserait l'autre.
    parser.add_argument(
        "--backflush-schema",
        default=os.environ.get("INV_ERP_BACKFLUSH_UC_SCHEMA", "backflush"),
    )
    parser.add_argument("--backflush-table", default="fact_ecart_backflush")
    parser.add_argument(
        "--backflush-since", default=os.environ.get("INV_BACKFLUSH_SINCE", ""),
        help=(
            "Lundi ISO à partir duquel copier l'écart backflush (AAAA-MM-JJ). "
            "Vide = tout l'historique publié."
        ),
    )
    parser.add_argument(
        "--skip-backflush", action="store_true",
        help="Ne synchronise que les articles et les nomenclatures.",
    )
    parser.add_argument("--stock-table", default="stock_snapshot")
    parser.add_argument(
        "--skip-stock", action="store_true",
        help="Ne synchronise pas le snapshot de stock.",
    )
    parser.add_argument("--movements-table", default="mouvements")
    parser.add_argument(
        "--movements-since", default=os.environ.get("INV_MOVEMENTS_SINCE", ""),
        help=(
            "Date à partir de laquelle copier les mouvements (AAAA-MM-JJ). "
            "Vide = tout l'historique. La table grandit indéfiniment."
        ),
    )
    parser.add_argument(
        "--skip-movements", action="store_true",
        help="Ne synchronise pas les mouvements de stock.",
    )
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
    backflush_fqn = (
        f"{args.catalog}.{args.backflush_schema}.{args.backflush_table}"
    )
    movements_fqn = f"{args.catalog}.{args.schema}.{args.movements_table}"
    stock_fqn = f"{args.catalog}.{args.schema}.{args.stock_table}"

    # La source porte des lignes sans référence — un mouvement rattaché à aucun
    # article. Le miroir les refuserait, sa clé primaire étant la référence, et
    # l'application n'en ferait rien : tout y est indexé par article. Elles sont
    # donc écartées ici, mais comptées et journalisées : une quantité qui
    # disparaît en silence est pire qu'une quantité manquante annoncée.
    movements_where = " AND ".join(
        clause for clause in (
            "reference IS NOT NULL",
            f"date_mouvement >= DATE '{args.movements_since}'"
            if args.movements_since else "",
        ) if clause
    )

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
        if not args.skip_backflush:
            _assert_mirror_shape(conn, "erp_ecart_backflush", BACKFLUSH_COLUMNS)
        if not args.skip_movements:
            _assert_mirror_shape(conn, "erp_mouvements", MOVEMENT_COLUMNS)
        if not args.skip_stock:
            _assert_mirror_shape(conn, "erp_stock_snapshot", STOCK_COLUMNS)

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

        # L'écart backflush est lu après le référentiel, et son échec n'annule
        # pas ce dernier : un pipeline gold indisponible ne doit pas priver
        # l'application de ses articles. Le miroir garde alors sa copie
        # précédente, dont la fraîcheur est affichée à l'écran.
        backflush: list[tuple] = []
        if not args.skip_backflush:
            try:
                backflush = _read(
                    spark, backflush_fqn, BACKFLUSH_COLUMNS, limit=args.limit,
                    where=(
                        f"semaine_debut >= DATE '{args.backflush_since}'"
                        if args.backflush_since else ""
                    ),
                )
                log.info("Lu %d ligne(s) d'écart backflush", len(backflush))
            except Exception as exc:
                log.error(
                    "Écart backflush (%s) illisible, miroir laissé intact : %s",
                    backflush_fqn, exc,
                )
                args.skip_backflush = True

        # Même règle : les mouvements indisponibles ne privent pas
        # l'application de son référentiel, et le miroir garde sa copie.
        movements: list[tuple] = []
        if not args.skip_movements:
            try:
                movements = _read(
                    spark, movements_fqn, MOVEMENT_COLUMNS, limit=args.limit,
                    where=movements_where,
                )
                log.info("Lu %d ligne(s) de mouvement de stock", len(movements))
                _report_orphans(spark, movements_fqn, args.movements_since)
            except Exception as exc:
                log.error(
                    "Mouvements (%s) illisibles, miroir laissé intact : %s",
                    movements_fqn, exc,
                )
                args.skip_movements = True

        # Même règle encore. La photo la plus récente seulement : la source est
        # partitionnée par jour et en garde l'historique, dont l'application n'a
        # que faire — elle compare un comptage à *un* état du système.
        stock: list[tuple] = []
        if not args.skip_stock:
            try:
                stock = _read(
                    spark, stock_fqn, STOCK_COLUMNS, limit=args.limit,
                    where=(
                        f"snapshot_date = (SELECT max(snapshot_date) "
                        f"FROM {stock_fqn})"
                    ),
                )
                log.info("Lu %d ligne(s) de stock physique", len(stock))
            except Exception as exc:
                log.error(
                    "Snapshot de stock (%s) illisible, miroir laissé intact : %s",
                    stock_fqn, exc,
                )
                args.skip_stock = True

        try:
            _swap(conn, "erp_base_article", ITEM_COLUMNS, items,
                  unique_on="item_id")
            _swap(conn, "erp_bom", BOM_COLUMNS, boms)
            # Même règle que pour les deux autres : une lecture vide est une
            # anomalie, pas une mise à jour. On garde la copie précédente.
            if backflush:
                _swap(conn, "erp_ecart_backflush", BACKFLUSH_COLUMNS, backflush)
            elif not args.skip_backflush:
                log.error(
                    "La table %s n'a renvoyé aucune ligne — miroir de l'écart "
                    "backflush laissé intact", backflush_fqn,
                )
            if movements:
                _swap(conn, "erp_mouvements", MOVEMENT_COLUMNS, movements)
            elif not args.skip_movements:
                log.error(
                    "La table %s n'a renvoyé aucune ligne — miroir des "
                    "mouvements laissé intact", movements_fqn,
                )
            if stock:
                _swap(conn, "erp_stock_snapshot", STOCK_COLUMNS, stock)
            elif not args.skip_stock:
                log.error(
                    "La table %s n'a renvoyé aucune ligne — miroir du stock "
                    "laissé intact", stock_fqn,
                )
        except Exception as exc:
            raise RuntimeError(_write_advice(exc, args.pg_schema)) from exc
        conn.commit()

    log.info(
        "Miroir ERP synchronisé (%d articles, %d liens, %d lignes d'écart, "
        "%d mouvements, %d lignes de stock)",
        len(items), len(boms), len(backflush), len(movements), len(stock),
    )
    return 0


def _read(
    spark: Any,
    fqn: str,
    columns: tuple[str, ...],
    *,
    limit: int,
    unique_on: str = "",
    where: str = "",
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
    clause = f" WHERE {where}" if where else ""
    query = f"SELECT {projection} FROM {fqn}{clause}"
    if unique_on and unique_on.lower() in available:
        order = ", ".join(columns)
        query = (
            f"SELECT {', '.join(columns)} FROM ("
            f"  SELECT {projection}, ROW_NUMBER() OVER ("
            f"    PARTITION BY {unique_on} ORDER BY {order}"
            f"  ) AS _rang FROM {fqn}{clause}"
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


def _report_orphans(spark: Any, fqn: str, since: str) -> None:
    """Journalise les mouvements sans référence, et ce qu'ils pesaient.

    Ils sont écartés à la lecture. Les taire ferait disparaître de la
    comparaison une quantité que l'ERP a bel et bien publiée ; les compter
    permet de juger si le total mérite d'être signalé à la plateforme.
    """
    window = f" AND date_mouvement >= DATE '{since}'" if since else ""
    row = spark.sql(
        "SELECT count(*), coalesce(sum(reception + expedition + production "
        f"+ conso_theorique + consommation + rebut), 0) FROM {fqn} "
        f"WHERE reference IS NULL{window}"
    ).collect()[0]
    if row[0]:
        log.warning(
            "%d ligne(s) sans référence écartée(s), %.2f de quantité au total. "
            "Un mouvement sans article ne se rattache à aucun stock.",
            row[0], row[1],
        )


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
    """Chaîne de connexion Lakebase — voir :mod:`lakebase`.

    La logique vivait ici, et le job de publication en portait une version
    périmée. Elle est désormais dans un module que les deux importent : une
    découverte d'endpoint qui se corrige d'un côté sans l'autre est exactement
    ce qui avait rendu la publication non déployable.
    """
    from lakebase import conninfo

    return conninfo(args, client)


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


