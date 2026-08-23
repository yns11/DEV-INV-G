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
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any

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
# Le bloc est recopié dans `publish_campaign_to_delta.py` : c'est lui qui met le
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
        "--driver-side",
        action="store_true",
        help=(
            "Faire transiter la copie par le driver plutôt que par les "
            "exécuteurs. Repli pour un environnement où les exécuteurs ne "
            "joignent pas Lakebase : la mémoire reste bornée (lecture en flux, "
            "partition par partition) mais la bande passante est celle d'une "
            "seule machine."
        ),
    )
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
    from lakebase import jdbc_of

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
        # La forme du miroir est vérifiée *et* retenue : ses types servent à
        # copier à NULL une colonne que la source ne publierait pas.
        shapes = {
            "erp_base_article": _assert_mirror_shape(
                conn, "erp_base_article", ITEM_COLUMNS
            ),
            "erp_bom": _assert_mirror_shape(conn, "erp_bom", BOM_COLUMNS),
        }
        if not args.skip_backflush:
            shapes["erp_ecart_backflush"] = _assert_mirror_shape(
                conn, "erp_ecart_backflush", BACKFLUSH_COLUMNS
            )
        if not args.skip_movements:
            shapes["erp_mouvements"] = _assert_mirror_shape(
                conn, "erp_mouvements", MOVEMENT_COLUMNS
            )
        if not args.skip_stock:
            shapes["erp_stock_snapshot"] = _assert_mirror_shape(
                conn, "erp_stock_snapshot", STOCK_COLUMNS
            )

        # Chaque table est *préparée* dans sa table d'attente, hors
        # transaction : c'est la partie longue, et la tenir dans la transaction
        # de substitution garderait un verrou ouvert pendant toute la lecture.
        # Le nombre de lignes écrites revient de la base, jamais du driver.
        jdbc = None if args.driver_side else jdbc_of(_lakebase_conninfo(args))

        def prepare(
            fqn: str, table: str, columns: tuple[str, ...], **kwargs: Any
        ) -> int:
            frame = _frame(
                spark, fqn, columns, limit=args.limit,
                types=shapes.get(table, {}), **kwargs,
            )
            return _stage(
                conn, frame, table, columns,
                jdbc=jdbc, driver_side=args.driver_side,
            )

        items = prepare(items_fqn, "erp_base_article", ITEM_COLUMNS,
                        unique_on="item_id")
        boms = prepare(bom_fqn, "erp_bom", BOM_COLUMNS)
        log.info("Préparé %d articles et %d liens de nomenclature", items, boms)

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

        # L'écart backflush est préparé après le référentiel, et son échec
        # n'annule pas ce dernier : un pipeline gold indisponible ne doit pas
        # priver l'application de ses articles. Le miroir garde alors sa copie
        # précédente, dont la fraîcheur est affichée à l'écran.
        backflush = 0
        if not args.skip_backflush:
            try:
                backflush = prepare(
                    backflush_fqn, "erp_ecart_backflush", BACKFLUSH_COLUMNS,
                    where=(
                        f"semaine_debut >= DATE '{args.backflush_since}'"
                        if args.backflush_since else ""
                    ),
                )
                log.info("Préparé %d ligne(s) d'écart backflush", backflush)
            except Exception as exc:
                log.error(
                    "Écart backflush (%s) illisible, miroir laissé intact : %s",
                    backflush_fqn, exc,
                )
                args.skip_backflush = True

        # Même règle : les mouvements indisponibles ne privent pas
        # l'application de son référentiel, et le miroir garde sa copie.
        movements = 0
        if not args.skip_movements:
            try:
                movements = prepare(
                    movements_fqn, "erp_mouvements", MOVEMENT_COLUMNS,
                    where=movements_where,
                )
                log.info("Préparé %d ligne(s) de mouvement de stock", movements)
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
        stock = 0
        if not args.skip_stock:
            try:
                stock = prepare(
                    stock_fqn, "erp_stock_snapshot", STOCK_COLUMNS,
                    where=(
                        f"snapshot_date = (SELECT max(snapshot_date) "
                        f"FROM {stock_fqn})"
                    ),
                )
                log.info("Préparé %d ligne(s) de stock physique", stock)
            except Exception as exc:
                log.error(
                    "Snapshot de stock (%s) illisible, miroir laissé intact : %s",
                    stock_fqn, exc,
                )
                args.skip_stock = True

        try:
            _swap(conn, "erp_base_article", ITEM_COLUMNS, unique_on="item_id")
            _swap(conn, "erp_bom", BOM_COLUMNS)
            # Même règle que pour les deux autres : une lecture vide est une
            # anomalie, pas une mise à jour. On garde la copie précédente.
            if backflush:
                _swap(conn, "erp_ecart_backflush", BACKFLUSH_COLUMNS)
            elif not args.skip_backflush:
                log.error(
                    "La table %s n'a renvoyé aucune ligne — miroir de l'écart "
                    "backflush laissé intact", backflush_fqn,
                )
            if movements:
                _swap(conn, "erp_mouvements", MOVEMENT_COLUMNS)
            elif not args.skip_movements:
                log.error(
                    "La table %s n'a renvoyé aucune ligne — miroir des "
                    "mouvements laissé intact", movements_fqn,
                )
            if stock:
                _swap(conn, "erp_stock_snapshot", STOCK_COLUMNS)
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
        items, boms, backflush, movements, stock,
    )
    return 0


def _frame(
    spark: Any,
    fqn: str,
    columns: tuple[str, ...],
    *,
    where: str = "",
    limit: int = 0,
    unique_on: str = "",
    types: dict[str, str] | None = None,
) -> Any:
    """La projection à copier — voir :mod:`mirror`. Rien n'est lu ici."""
    from mirror import frame_of

    return frame_of(
        spark, fqn, columns, where=where, limit=limit, unique_on=unique_on,
        types=types, warn=log.warning,
    )


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


def _stage(conn: Any, frame: Any, table: str, columns: tuple[str, ...],
           *, jdbc: tuple[str, dict[str, str]] | None, driver_side: bool) -> int:
    """Remplit la table d'attente — voir :mod:`mirror`."""
    from mirror import stage

    url, properties = jdbc or ("", {})
    return stage(
        conn, frame, table, columns,
        jdbc_url=url, jdbc_properties=properties, driver_side=driver_side,
    )


def _swap(conn: Any, table: str, columns: tuple[str, ...],
          *, unique_on: str = "") -> None:
    """Substitue la table d'attente au miroir — voir :mod:`mirror`."""
    from mirror import swap

    swap(conn, table, columns, unique_on=unique_on, say=log.info)


def _lakebase_conninfo(args: Any, client: Any = None) -> str:
    """Chaîne de connexion Lakebase — voir :mod:`lakebase`.

    La logique vivait ici, et le job de publication en portait une version
    périmée. Elle est désormais dans un module que les deux importent : une
    découverte d'endpoint qui se corrige d'un côté sans l'autre est exactement
    ce qui avait rendu la publication non déployable.
    """
    from lakebase import conninfo

    return conninfo(args, client)


def _assert_mirror_shape(
    conn: Any, table: str, columns: tuple[str, ...]
) -> dict[str, str]:
    """Refuse to start unless the mirror has the columns about to be written.

    The mirror's tables belong to the application, which creates and migrates
    them at start-up; this job only fills them. When the two get out of step —
    a column added to the source and to the application, but the application not
    yet redeployed — Postgres refuses the very last statement, after the whole
    referential has been read and shipped. Asking the catalogue first turns that
    into an immediate, self-explanatory stop.
    """
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
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
    # Les types, pour la copie à NULL d'une colonne que la source ne publie
    # pas : ils sont déjà lus ici, les redemander serait une seconde vérité.
    from mirror import spark_type

    return {str(r[0]).lower(): spark_type(str(r[1])) for r in rows}


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


