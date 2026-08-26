"""Copier une table Unity Catalog dans le miroir Lakebase, sans passer par le driver.

Les deux synchronisations — le notebook et le job en ligne de commande —
faisaient la même chose : ``spark.sql(...).collect()``, puis un ``executemany``
par lots. La lecture ramenait **toute** la table dans la mémoire du driver, en
tuples Python.

Sur le référentiel articles cela passe encore. Sur ``mouvements``, qui est un
article × un jour sur toute une période, cela ne passe pas : quelques millions
de lignes, chacune devenant un tuple Python de huit objets, sur un driver qui a
la mémoire d'une machine et pas celle d'un cluster. Le job ne ralentit pas, il
meurt — et il meurt **après** avoir lu, c'est-à-dire au bout du seul travail
coûteux.

Ce module remplace ce chemin par un remplissage distribué : chaque exécuteur
écrit sa partition dans une table d'attente, par JDBC, sans que rien ne
converge vers le driver. Le driver ne fait plus que la substitution, qui est
deux instructions SQL.

**Ce que la substitution garantit, et qui ne change pas.** L'application ne
voit jamais un miroir vide ni à moitié rempli : le ``TRUNCATE`` et
l'``INSERT ... SELECT`` partagent une transaction, et la table d'attente est
remplie avant qu'elle ne s'ouvre. La transaction couvre désormais la
substitution seule, là où elle restait ouverte pendant toute la lecture —
minutes durant, sur une connexion que rien n'utilisait.

**L'hypothèse, dite franchement.** Les exécuteurs doivent joindre Lakebase.
C'est le cas d'un cluster Databricks et de son instance Lakebase, mais c'est
une propriété de l'environnement, pas du code. ``driver_side=True`` retombe sur
un chemin qui passe par le driver — en flux, jamais en bloc : ``toLocalIterator``
ramène une partition à la fois, ce qui garde la mémoire bornée même si la bande
passante, elle, reste celle d'une machine.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["BATCH", "frame_of", "spark_type", "stage", "swap"]

#: Lignes par lot d'insertion, sur le chemin de repli.
BATCH = 5_000

#: Partitions écrites en parallèle vers Lakebase.
#:
#: Une par cœur donnerait des centaines de connexions simultanées sur une base
#: qui en accepte quelques dizaines. Huit tiennent largement dans le pool et
#: suffisent à saturer le lien : au-delà, ce n'est plus la lecture qui limite
#: mais l'écriture côté PostgreSQL.
WRITE_PARTITIONS = 8

#: Le code d'erreur par lequel le calcul serverless refuse l'écriture JDBC.
#:
#: Databricks y restreint le DML à une liste de sources, et le connecteur JDBC
#: générique n'en fait pas partie — quoi qu'en laisse croire le mot
#: « postgresql » qui y figure : celui-là désigne la fédération par connexion
#: Unity Catalog, pas `format("jdbc")` avec une URL et un mot de passe.
_REFUSES_JDBC = "UNSUPPORTED_DATA_SOURCE_WRITE"


#: Comment un type PostgreSQL se dit en SQL Spark.
#:
#: Sert au seul endroit où le type compte : une colonne absente de la source
#: est copiée à NULL, et ce NULL doit avoir le type de la colonne du miroir.
#: Un NULL de type chaîne dans une colonne numérique est refusé par la base — à
#: la dernière instruction, une fois toute la lecture faite.
SPARK_TYPES = {
    "numeric": "DECIMAL(18,6)",
    "integer": "INT",
    "bigint": "BIGINT",
    "smallint": "SMALLINT",
    "double precision": "DOUBLE",
    "real": "FLOAT",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp with time zone": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
}

#: Le type d'une colonne dont le miroir ne dit rien.
DEFAULT_TYPE = "STRING"


def spark_type(postgres_type: str) -> str:
    """Le type Spark correspondant, ou la chaîne à défaut."""
    return SPARK_TYPES.get(postgres_type.strip().lower(), DEFAULT_TYPE)


def frame_of(
    spark: Any,
    fqn: str,
    columns: tuple[str, ...],
    *,
    where: str = "",
    limit: int = 0,
    unique_on: str = "",
    types: dict[str, str] | None = None,
    warn: Any = None,
) -> Any:
    """La projection à copier, **sans la lire**.

    Rend un DataFrame : rien n'est matérialisé ici, et c'est tout l'objet. Les
    colonnes absentes de la source sont copiées à NULL plutôt que de faire
    échouer la lecture — une colonne que la plateforme n'a pas encore publiée
    ne doit pas priver l'application de son référentiel — et le fait est
    signalé.

    ``unique_on`` déduplique de façon déterministe. La source *devrait* être
    unique sur cette clé ; elle ne l'est pas toujours, et l'application lit le
    miroir en supposant qu'elle l'est. On déduplique donc plutôt que de laisser
    l'insertion échouer, et l'écart de volume est journalisé : c'est une
    anomalie de la source, pas une routine.
    """
    warn = warn or log.warning
    available = {f.name.lower() for f in spark.table(fqn).schema.fields}
    missing = [c for c in columns if c.lower() not in available]
    if missing:
        warn(f"{fqn} : colonnes absentes, copiées à NULL — {', '.join(missing)}")

    # Le NULL prend le type de la colonne du miroir : sans cela, une colonne
    # numérique que la source cesse de publier fait échouer l'insertion, et
    # c'est précisément la situation que la copie à NULL existe pour traverser.
    shape = {k.lower(): v for k, v in (types or {}).items()}
    projection = ", ".join(
        c if c.lower() in available
        else f"CAST(NULL AS {shape.get(c.lower(), DEFAULT_TYPE)}) AS {c}"
        for c in columns
    )
    clause = f" WHERE {where}" if where else ""
    query = f"SELECT {projection} FROM {fqn}{clause}"
    if unique_on and unique_on.lower() in available:
        # L'ordre ne porte que sur les colonnes **réellement présentes**.
        #
        # Une colonne absente est projetée en `CAST(NULL AS STRING) AS <nom>` :
        # la nommer dans la fenêtre reviendrait à référencer un alias de la même
        # liste de sélection, ce qu'aucun des deux moteurs ne résout — la requête
        # échoue au lieu de copier. Et elle ne départagerait rien de toute façon,
        # puisqu'elle vaut la même constante sur toutes les lignes.
        #
        # Le cas se produit dès qu'une colonne du contrat n'est pas encore
        # publiée par la plateforme, c'est-à-dire précisément quand la copie à
        # NULL est censée sauver la mise.
        order = ", ".join(c for c in columns if c.lower() in available)
        query = (
            f"SELECT {', '.join(columns)} FROM ("
            f"  SELECT {projection}, ROW_NUMBER() OVER ("
            f"    PARTITION BY {unique_on} ORDER BY {order}"
            f"  ) AS _rang FROM {fqn}{clause}"
            f") WHERE _rang = 1"
        )
    if limit:
        query += f" LIMIT {int(limit)}"
    return spark.sql(query)


def stage(
    conn: Any,
    frame: Any,
    table: str,
    columns: tuple[str, ...],
    *,
    jdbc_url: str = "",
    jdbc_properties: dict[str, str] | None = None,
    driver_side: bool = False,
    say: Any = None,
) -> int:
    """Remplit ``<table>_staging`` et rend le nombre de lignes écrites.

    Hors transaction, et c'est délibéré : c'est la partie longue, et la tenir
    dans la transaction de substitution garderait un verrou ouvert pendant
    toute la lecture. La table d'attente est vidée d'abord — un remplissage
    interrompu y laisse ses lignes, et les ajouter à celles du suivant
    écrirait le double.

    Le nombre est rendu parce que l'appelant en a besoin **avant** d'écrire :
    une source qui ne renvoie rien est une anomalie, pas une mise à jour, et
    substituer un vide effacerait un miroir valide.
    """
    say = say or log.info
    staging = f"{table}_staging"
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {staging} (LIKE {table} INCLUDING DEFAULTS)"
    )
    conn.execute(f"TRUNCATE {staging}")
    conn.commit()

    if driver_side or not jdbc_url:
        return _stage_through_driver(conn, frame, staging, columns)

    try:
        (
            frame.repartition(WRITE_PARTITIONS)
            .write.format("jdbc")
            .option("url", jdbc_url)
            .option("dbtable", staging)
            .option("batchsize", BATCH)
            .options(**(jdbc_properties or {}))
            .mode("append")
            .save()
        )
    except Exception as exc:
        # Le refus du serverless, et lui seul. Toute autre panne d'écriture —
        # identifiants, colonne absente, base injoignable — se reproduirait à
        # l'identique par le driver : la rattraper ici ne ferait que payer la
        # lecture une seconde fois pour aboutir à la même erreur, plus tard et
        # sous un autre nom.
        if _REFUSES_JDBC not in str(exc):
            raise
        say(
            f"{table} : le calcul serverless refuse l'écriture JDBC distribuée "
            f"({_REFUSES_JDBC}). Repli sur le driver, en flux. La copie est "
            "plus lente — c'est la bande passante d'une machine — mais elle "
            "aboutit ; --driver-side l'impose d'emblée et évite d'y venir "
            "après la lecture."
        )
        # La transaction de la connexion de contrôle n'a rien à voir avec
        # l'échec Spark, mais le refus survient à l'analyse : rien n'a été
        # écrit. On vide quand même avant de reprendre — une écriture partielle
        # laisserait ses lignes, et le repli les doublerait.
        conn.rollback()
        conn.execute(f"TRUNCATE {staging}")
        conn.commit()
        return _stage_through_driver(conn, frame, staging, columns)
    return int(conn.execute(f"SELECT count(*) FROM {staging}").fetchone()[0])


def _stage_through_driver(
    conn: Any, frame: Any, staging: str, columns: tuple[str, ...]
) -> int:
    """Le repli, pour un environnement où les exécuteurs ne joignent pas la base.

    ``toLocalIterator`` et non ``collect`` : une partition à la fois. La bande
    passante reste celle d'une machine, mais la mémoire ne dépend plus de la
    taille de la table — c'est ce qui séparait un job lent d'un job mort.
    """
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    written = 0
    batch: list[tuple] = []
    with conn.cursor() as cur:
        for row in frame.toLocalIterator():
            batch.append(tuple(row))
            if len(batch) >= BATCH:
                cur.executemany(
                    f"INSERT INTO {staging} ({names}) VALUES ({placeholders})", batch
                )
                written += len(batch)
                batch.clear()
        if batch:
            cur.executemany(
                f"INSERT INTO {staging} ({names}) VALUES ({placeholders})", batch
            )
            written += len(batch)
    conn.commit()
    return written


def swap(
    conn: Any,
    table: str,
    columns: tuple[str, ...],
    *,
    unique_on: str = "",
    say: Any = None,
) -> None:
    """Substitue la table d'attente à la table du miroir, en une transaction.

    ``unique_on`` filtre une dernière fois. La déduplication a déjà eu lieu à
    la lecture ; c'est ici que l'échec coûterait le plus cher, sur la dernière
    instruction d'un travail terminé. Deux mots de SQL rendent la violation
    impossible plutôt que rare.
    """
    say = say or log.info
    staging = f"{table}_staging"
    names = ", ".join(columns)
    distinct = f"DISTINCT ON ({unique_on}) " if unique_on else ""
    order = f" ORDER BY {unique_on}, {names}" if unique_on else ""

    before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    conn.execute(f"TRUNCATE {table}")
    conn.execute(
        f"INSERT INTO {table} ({names}, synced_at) "
        f"SELECT {distinct}{names}, now() FROM {staging}{order}"
    )
    after = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    # Le remplacement est intégral, pas un ajout : le dire en chiffres évite
    # d'avoir à le déduire. Une référence retirée de l'ERP doit disparaître du
    # miroir, et rien à l'écran ne le montrait.
    say(f"{table} : {before} ligne(s) supprimée(s), {after} écrite(s)")
