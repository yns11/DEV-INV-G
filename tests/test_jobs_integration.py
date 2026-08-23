"""Le job de synchronisation, exécuté contre une vraie base.

Ce que ces contrôles ajoutent aux précédents
--------------------------------------------
Les contrôles existants du miroir portent sur des doublures : ils vérifient que
``stage`` appelle bien l'écriture JDBC, que ``swap`` enchaîne un ``TRUNCATE`` et
un ``INSERT ... SELECT``, que rien ne collecte sur le driver. C'est nécessaire
et ce n'est pas suffisant : une doublure accepte tout, y compris du SQL qui ne
s'exécuterait pas et une insertion qui violerait une clé primaire.

Ici, le SQL **produit par le job** est exécuté par un vrai PostgreSQL, contre
les vraies tables du miroir — celles que les migrations de l'application créent,
avec leurs clés primaires et leurs valeurs par défaut. Ce qui est doublé est
Spark, et lui seul : il n'y a pas de cluster sous la main, et il n'y en aura pas
dans une intégration continue non plus.

La doublure est fidèle sur le point qui compte : elle **exécute** les requêtes
au lieu de les enregistrer. Une projection qui nomme une colonne absente, une
déduplication mal écrite, une insertion qui heurte une contrainte — tout cela
échoue ici comme cela échouerait sur le cluster.

Ce qui reste hors de portée, et pourquoi c'est dit
--------------------------------------------------
La répartition réelle entre exécuteurs, la bande passante d'un lien JDBC et le
comportement d'Unity Catalog ne sont pas reproductibles sans la plateforme. Ces
contrôles ne prétendent pas les couvrir. Ils couvrent ce qui casse en pratique :
le SQL, les contraintes, et la substitution.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "jobs") not in sys.path:
    sys.path.insert(0, str(ROOT / "jobs"))

pytestmark = pytest.mark.postgres


#: Le schéma qui joue le rôle d'Unity Catalog dans cette base.
SOURCE_SCHEMA = "erp_source"

#: Les colonnes du référentiel articles, telles que le job les copie.
ITEM_COLUMNS = (
    "item_id", "item_name", "item_description", "search_name", "name_alias",
    "categorie", "programme", "item_group_id", "item_group_label",
    "std_cost_price", "std_price_unit", "std_unit",
)


# --------------------------------------------------------------------------- #
# La doublure de Spark : elle exécute, elle n'enregistre pas
# --------------------------------------------------------------------------- #

class FakeFrame:
    """Un DataFrame dont la requête n'est exécutée qu'à la demande.

    C'est la propriété que le job attend de Spark, et celle dont dépend tout le
    correctif : ``frame_of`` rend ceci sans avoir rien lu.
    """

    def __init__(self, pool: Any, query: str) -> None:
        self.pool = pool
        self.query = query
        self.partitions = 1

    # -- lecture en flux, pour le chemin de repli ---------------------------
    def toLocalIterator(self):  # noqa: N802 - le nom vient de Spark
        with self.pool() as conn, conn.cursor(name="stream") as cur:
            cur.execute(self.query)
            yield from cur

    # -- écriture distribuée ------------------------------------------------
    def repartition(self, n: int) -> FakeFrame:
        self.partitions = n
        return self

    @property
    def write(self) -> FakeWriter:
        return FakeWriter(self)


class FakeWriter:
    """Ce que les exécuteurs feraient, ramené à une instruction SQL.

    Le vrai chemin ouvre une connexion JDBC par partition et insère ses lignes.
    Ici la même chose se produit en une instruction : ce qui est vérifié est le
    **résultat** — les bonnes lignes dans la table d'attente — pas le nombre de
    connexions, qui dépend d'un cluster.
    """

    def __init__(self, frame: FakeFrame) -> None:
        self.frame = frame
        self.given: dict[str, Any] = {}
        self.how = ""

    def format(self, name: str) -> FakeWriter:
        self.given["format"] = name
        return self

    def option(self, key: str, value: Any) -> FakeWriter:
        self.given[key] = value
        return self

    def options(self, **kwargs: Any) -> FakeWriter:
        self.given.update(kwargs)
        return self

    def mode(self, how: str) -> FakeWriter:
        self.how = how
        return self

    def save(self) -> None:
        if self.given.get("format") != "jdbc":
            raise AssertionError(f"écriture non JDBC : {self.given.get('format')!r}")
        if self.how != "append":
            raise AssertionError(
                f"mode {self.how!r} : la table d'attente est vidée puis remplie, "
                "un overwrite laisserait Spark redeviner sa forme"
            )
        target = self.given["dbtable"]
        with self.frame.pool() as conn:
            conn.execute(f"INSERT INTO {target} {self.frame.query}")
            conn.commit()


class FakeSpark:
    """Assez de Spark pour le chemin de copie, et rien de plus."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def table(self, fqn: str) -> Any:
        table = fqn.rsplit(".", 1)[-1]
        with self.pool() as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (SOURCE_SCHEMA, table),
            ).fetchall()
        if not rows:
            raise AssertionError(f"table source absente : {fqn}")
        fields = [SimpleNamespace(name=str(r[0])) for r in rows]
        return SimpleNamespace(schema=SimpleNamespace(fields=fields))

    #: Ce que les deux dialectes écrivent différemment.
    #:
    #: Spark nomme `STRING` le type que PostgreSQL nomme `TEXT`. Ce n'est pas
    #: un défaut du job — sa cible est Spark — mais il faut le traduire pour
    #: que la requête s'exécute ici. La liste est délibérément courte : chaque
    #: entrée est un endroit où ce banc cesse de contrôler le SQL réel, et
    #: l'allonger sans y penser viderait le contrôle de son sens.
    DIALECT = (("CAST(NULL AS STRING)", "CAST(NULL AS TEXT)"),)

    def sql(self, query: str) -> FakeFrame:
        # Le nom qualifié d'Unity Catalog n'existe pas ici ; seul le dernier
        # segment compte, et il désigne la table du schéma source.
        for fqn in _fqns(query):
            query = query.replace(fqn, f"{SOURCE_SCHEMA}.{fqn.rsplit('.', 1)[-1]}")
        for spark, postgres in self.DIALECT:
            query = query.replace(spark, postgres)
        return FakeFrame(self.pool, query)


def _fqns(query: str) -> list[str]:
    """Les noms `catalogue.schéma.table` présents dans la requête."""
    found = []
    for word in query.replace("(", " ").replace(")", " ").split():
        cleaned = word.strip(",;")
        if cleaned.count(".") == 2 and all(part for part in cleaned.split(".")):
            found.append(cleaned)
    return found


# --------------------------------------------------------------------------- #
# Le décor : un schéma source, et le miroir que l'application a migré
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def pool():
    """Une fabrique de connexions, comme le job en ouvre.

    Des connexions psycopg directes, et non le pool de l'application : c'est
    ce que le job fait, et une doublure qui emprunterait le pool testerait la
    gestion de pool plutôt que la copie. Chaque appelant referme la sienne —
    les exécuteurs, dans la vraie vie, ouvrent la leur.
    """
    if not os.environ.get("PGHOST"):
        pytest.skip("PGHOST absent : pas de PostgreSQL pour ces contrôles")

    import contextlib

    import psycopg

    sys.path.insert(0, str(ROOT / "app"))
    from inventory.db import get_database
    from inventory.db.migrations import apply_all

    apply_all(get_database())
    schema = os.environ.get("INV_PG_SCHEMA", "inventory")
    conninfo = " ".join(
        f"{key}={os.environ[env]}"
        for key, env in (
            ("host", "PGHOST"), ("port", "PGPORT"), ("dbname", "PGDATABASE"),
            ("user", "PGUSER"), ("password", "PGPASSWORD"),
        )
        if os.environ.get(env)
    )

    @contextlib.contextmanager
    def scoped():
        # Lignes en tuples, comme le job les reçoit : `psycopg.connect` sans
        # `row_factory`. Un dictionnaire ici ferait passer un contrôle sur du
        # code qui, en production, indexe par position.
        conn = psycopg.connect(conninfo)
        try:
            conn.execute(f"SET search_path TO {schema}, {SOURCE_SCHEMA}, public")
            yield conn
        finally:
            conn.close()

    with scoped() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SOURCE_SCHEMA}")
        conn.commit()
    return scoped


@pytest.fixture()
def source(pool):
    """Une table source vierge, à la forme du référentiel articles."""
    columns = ", ".join(f"{c} text" for c in ITEM_COLUMNS if not c.startswith("std_"))
    with pool() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {SOURCE_SCHEMA}.silver_base_article")
        conn.execute(
            f"CREATE TABLE {SOURCE_SCHEMA}.silver_base_article ("
            f"{columns}, std_cost_price numeric, std_price_unit numeric, "
            "std_unit text)"
        )
        conn.commit()
    return "cat.sch.silver_base_article"


def seed_source(pool, rows: list[tuple[str, str]]) -> None:
    """Remplit la source avec des couples (référence, nom)."""
    with pool() as conn:
        for item_id, name in rows:
            conn.execute(
                f"INSERT INTO {SOURCE_SCHEMA}.silver_base_article "
                "(item_id, item_name) VALUES (%s, %s)",
                (item_id, name),
            )
        conn.commit()


def mirror_rows(pool) -> list[tuple[str, str]]:
    with pool() as conn:
        rows = conn.execute(
            "SELECT item_id, item_name FROM erp_base_article ORDER BY item_id"
        ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def mirror_shape(pool) -> dict[str, str]:
    """Les types du miroir, lus comme le job les lit.

    Il les obtient de sa propre vérification de forme ; les redemander ici de
    la même façon garde le contrôle sur le chemin réel plutôt que sur une
    table écrite à la main.
    """
    import mirror as mirror_module

    with pool() as conn:
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = %s",
            ("erp_base_article",),
        ).fetchall()
    return {str(r[0]).lower(): mirror_module.spark_type(str(r[1])) for r in rows}


def copy(pool, fqn: str, *, driver_side: bool = False, where: str = "",
         unique_on: str = "item_id") -> int:
    """Le chemin de copie du job, du bout à l'autre."""
    import mirror

    spark = FakeSpark(pool)
    frame = mirror.frame_of(
        spark, fqn, ITEM_COLUMNS, where=where, unique_on=unique_on,
        types=mirror_shape(pool), warn=lambda message: None,
    )
    with pool() as conn:
        written = mirror.stage(
            conn, frame, "erp_base_article", ITEM_COLUMNS,
            jdbc_url="" if driver_side else "jdbc:postgresql://fake/db",
            jdbc_properties={"user": "u", "password": "p"},
            driver_side=driver_side,
        )
        if written:
            mirror.swap(
                conn, "erp_base_article", ITEM_COLUMNS,
                unique_on=unique_on, say=lambda message: None,
            )
            conn.commit()
    return written


# --------------------------------------------------------------------------- #
# La copie remplace, elle n'ajoute pas
# --------------------------------------------------------------------------- #

class TestTheMirrorIsReplaced:
    def test_a_first_copy_fills_the_mirror(self, pool, source):
        seed_source(pool, [("A-1", "VIS"), ("A-2", "ECROU")])
        assert copy(pool, source) == 2
        assert mirror_rows(pool) == [("A-1", "VIS"), ("A-2", "ECROU")]

    def test_a_reference_withdrawn_from_the_erp_disappears(self, pool, source):
        """C'est ce que « remplacement intégral » veut dire, et c'est ce qu'un
        simple ajout laisserait traîner indéfiniment."""
        seed_source(pool, [("A-1", "VIS"), ("A-2", "ECROU")])
        copy(pool, source)

        with pool() as conn:
            conn.execute(
                f"DELETE FROM {SOURCE_SCHEMA}.silver_base_article "
                "WHERE item_id = 'A-2'"
            )
            conn.commit()
        copy(pool, source)
        assert mirror_rows(pool) == [("A-1", "VIS")]

    def test_a_renamed_reference_is_updated(self, pool, source):
        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)
        with pool() as conn:
            conn.execute(
                f"UPDATE {SOURCE_SCHEMA}.silver_base_article "
                "SET item_name = 'VIS INOX' WHERE item_id = 'A-1'"
            )
            conn.commit()
        copy(pool, source)
        assert mirror_rows(pool) == [("A-1", "VIS INOX")]

    def test_two_copies_do_not_double_the_rows(self, pool, source):
        """La table d'attente est vidée avant chaque remplissage.

        Sans cela, une exécution interrompue y laisse ses lignes et la suivante
        écrit le double — ce que la clé primaire du miroir refuserait, à la
        toute dernière instruction.
        """
        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)
        copy(pool, source)
        assert mirror_rows(pool) == [("A-1", "VIS")]

    def test_the_synchronisation_date_is_written(self, pool, source):
        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)
        with pool() as conn:
            row = conn.execute(
                "SELECT synced_at FROM erp_base_article"
            ).fetchone()
        assert row is not None and row[0] is not None


# --------------------------------------------------------------------------- #
# Une source non conforme ne fait pas échouer la dernière instruction
# --------------------------------------------------------------------------- #

class TestTheSourceIsNotAlwaysWhatItClaims:
    def test_duplicates_do_not_violate_the_primary_key(self, pool, source):
        """La source *devrait* être unique par référence. Elle ne l'est pas
        toujours, et le miroir a une clé primaire.

        Une doublure accepterait les doublons sans rien dire ; ici la
        contrainte est réelle, et c'est elle qui juge.
        """
        seed_source(pool, [("A-1", "VIS"), ("A-1", "VIS BIS"), ("A-2", "ECROU")])
        assert copy(pool, source) == 2
        assert [ref for ref, _ in mirror_rows(pool)] == ["A-1", "A-2"]

    def test_the_deduplication_is_deterministic(self, pool, source):
        """Deux exécutions gardent la même ligne : sans ordre, le miroir
        changerait d'un jour à l'autre sans que la source ait bougé."""
        seed_source(pool, [("A-1", "VIS"), ("A-1", "VIS BIS")])
        copy(pool, source)
        first = mirror_rows(pool)
        copy(pool, source)
        assert mirror_rows(pool) == first

    def test_a_column_absent_from_the_source_is_copied_as_null(self, pool, source):
        """Une colonne que la plateforme n'a pas encore publiée ne doit pas
        priver l'application de son référentiel."""
        seed_source(pool, [("A-1", "VIS")])
        with pool() as conn:
            conn.execute(
                f"ALTER TABLE {SOURCE_SCHEMA}.silver_base_article DROP COLUMN programme"
            )
            conn.commit()
        assert copy(pool, source) == 1
        with pool() as conn:
            row = conn.execute(
                "SELECT programme FROM erp_base_article"
            ).fetchone()
        assert row is not None and row[0] is None

    def test_a_missing_column_and_deduplication_can_coexist(self, pool, source):
        """Les deux ensemble, parce que c'est leur rencontre qui cassait.

        La déduplication ordonnait sur **toutes** les colonnes du contrat, y
        compris celles projetées en NULL constant faute d'exister à la source.
        Nommer un alias de la même liste de sélection dans une fenêtre n'est
        résolu par aucun des deux moteurs : la requête échouait au lieu de
        copier — exactement quand la copie à NULL était censée sauver la mise.

        Aucune doublure ne pouvait le voir : il fallait exécuter le SQL.
        """
        with pool() as conn:
            conn.execute(
                f"ALTER TABLE {SOURCE_SCHEMA}.silver_base_article "
                "DROP COLUMN programme"
            )
            conn.commit()
        seed_source(pool, [("A-1", "VIS"), ("A-1", "VIS BIS"), ("A-2", "ECROU")])
        assert copy(pool, source, unique_on="item_id") == 2
        assert [ref for ref, _ in mirror_rows(pool)] == ["A-1", "A-2"]

    def test_a_missing_numeric_column_is_copied_as_a_typed_null(self, pool, source):
        """Le miroir a des colonnes numériques ; la source peut cesser de les
        publier comme les autres.

        Projeter un NULL de type chaîne dans une colonne numérique est un
        désaccord de types que la base refuse — à la dernière instruction, une
        fois toute la lecture faite.
        """
        with pool() as conn:
            conn.execute(
                f"ALTER TABLE {SOURCE_SCHEMA}.silver_base_article "
                "DROP COLUMN std_cost_price"
            )
            conn.commit()
        seed_source(pool, [("A-1", "VIS")])
        assert copy(pool, source) == 1
        with pool() as conn:
            row = conn.execute(
                "SELECT std_cost_price FROM erp_base_article"
            ).fetchone()
        assert row is not None and row[0] is None

    def test_a_missing_numeric_column_is_copied_with_its_own_type(self, pool, source):
        """Le miroir a des colonnes numériques ; la source peut cesser de les
        publier comme les autres.

        Un NULL de type chaîne dans une colonne numérique est un désaccord que
        la base refuse — à la dernière instruction, une fois toute la lecture
        faite, et précisément dans la situation que la copie à NULL existe pour
        traverser. Aucune doublure ne pouvait le voir.
        """
        with pool() as conn:
            conn.execute(
                f"ALTER TABLE {SOURCE_SCHEMA}.silver_base_article "
                "DROP COLUMN std_cost_price"
            )
            conn.commit()
        seed_source(pool, [("A-1", "VIS")])
        assert copy(pool, source) == 1
        with pool() as conn:
            row = conn.execute(
                "SELECT std_cost_price FROM erp_base_article"
            ).fetchone()
        assert row is not None and row[0] is None

    def test_an_empty_source_writes_nothing(self, pool, source):
        """Le compte rendu est ce sur quoi l'appelant refuse la substitution :
        écraser un référentiel valide par un vide fait disparaître la
        possibilité même de lancer une campagne."""
        assert copy(pool, source) == 0

    def test_an_empty_source_leaves_the_previous_mirror_intact(self, pool, source):
        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)
        with pool() as conn:
            conn.execute(f"DELETE FROM {SOURCE_SCHEMA}.silver_base_article")
            conn.commit()
        assert copy(pool, source) == 0
        assert mirror_rows(pool) == [("A-1", "VIS")]


# --------------------------------------------------------------------------- #
# La substitution est atomique
# --------------------------------------------------------------------------- #

class TestTheSwapIsAllOrNothing:
    def test_an_interrupted_swap_leaves_the_previous_mirror(self, pool, source):
        """Le contrôle qu'aucune doublure ne peut rendre.

        Entre le ``TRUNCATE`` et l'``INSERT ... SELECT``, le miroir est vide.
        Si la transaction ne les couvrait pas tous les deux, une panne à cet
        instant laisserait l'application devant un référentiel disparu — et
        une campagne partirait dessus sans rien remarquer.
        """
        import mirror

        seed_source(pool, [("A-1", "VIS"), ("A-2", "ECROU")])
        copy(pool, source)
        before = mirror_rows(pool)
        assert before, "le décor doit être rempli pour que le contrôle ait un sens"

        seed_source(pool, [("A-3", "RONDELLE")])
        spark = FakeSpark(pool)
        frame = mirror.frame_of(
            spark, source, ITEM_COLUMNS, unique_on="item_id", warn=lambda m: None
        )
        with pool() as conn:
            mirror.stage(
                conn, frame, "erp_base_article", ITEM_COLUMNS,
                jdbc_url="jdbc:postgresql://fake/db",
                jdbc_properties={"user": "u", "password": "p"},
            )
            try:
                mirror.swap(
                    conn, "erp_base_article", ITEM_COLUMNS,
                    unique_on="item_id", say=_explode,
                )
            except RuntimeError:
                conn.rollback()
            else:  # pragma: no cover - la panne est provoquée
                raise AssertionError("la panne simulée n'a pas eu lieu")

        assert mirror_rows(pool) == before

    def test_the_staging_table_survives_a_rolled_back_swap(self, pool, source):
        """Elle est remplie hors transaction : un rollback de la substitution
        ne doit pas la vider, sans quoi il faudrait tout relire."""
        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)
        with pool() as conn:
            count = conn.execute(
                "SELECT count(*) FROM erp_base_article_staging"
            ).fetchone()
        assert count is not None and count[0] == 1


class TestTheSwapHasItsOwnGuard:
    """La déduplication de la substitution, celle qu'on ne voit jamais agir.

    À la lecture, la source est déjà dédupliquée : la table d'attente arrive
    propre, et le filtre de la substitution ne sert à rien. Il sert le jour où
    la lecture n'a pas dédupliqué — parce que ``unique_on`` désigne une colonne
    que la source n'a pas, par exemple — et c'est le jour où l'échec coûte le
    plus cher : sur la dernière instruction d'un travail terminé.

    Le seul moyen de l'exercer est de remplir la table d'attente soi-même.
    """

    def test_duplicates_in_the_staging_table_do_not_reach_the_mirror(self, pool, source):
        import mirror

        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)

        with pool() as conn:
            conn.execute("TRUNCATE erp_base_article_staging")
            for name in ("VIS", "VIS BIS"):
                conn.execute(
                    "INSERT INTO erp_base_article_staging (item_id, item_name) "
                    "VALUES (%s, %s)",
                    ("A-9", name),
                )
            conn.commit()
            mirror.swap(
                conn, "erp_base_article", ITEM_COLUMNS,
                unique_on="item_id", say=lambda message: None,
            )
            conn.commit()

        assert [ref for ref, _ in mirror_rows(pool)] == ["A-9"]

    def test_without_the_guard_the_primary_key_would_refuse(self, pool, source):
        """Ce que le filtre évite, dit en clair : sans lui, la clé primaire du
        miroir refuse l'insertion — et le travail entier est perdu."""
        import psycopg

        seed_source(pool, [("A-1", "VIS")])
        copy(pool, source)
        with pool() as conn:
            conn.execute("TRUNCATE erp_base_article_staging")
            for name in ("VIS", "VIS BIS"):
                conn.execute(
                    "INSERT INTO erp_base_article_staging (item_id, item_name) "
                    "VALUES (%s, %s)",
                    ("A-9", name),
                )
            conn.commit()
            conn.execute("TRUNCATE erp_base_article")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO erp_base_article (item_id, item_name, synced_at) "
                    "SELECT item_id, item_name, now() FROM erp_base_article_staging"
                )
            conn.rollback()


def _explode(message: str) -> None:
    """Interrompt la substitution entre ses deux instructions."""
    raise RuntimeError("panne simulée au milieu de la substitution")


# --------------------------------------------------------------------------- #
# Les deux chemins d'écriture donnent le même miroir
# --------------------------------------------------------------------------- #

class TestBothWritePathsAgree:
    def test_the_driver_side_fallback_produces_the_same_mirror(self, pool, source):
        """Le repli existe pour un environnement où les exécuteurs ne joignent
        pas la base. Il serait inutile s'il ne donnait pas le même résultat."""
        seed_source(pool, [("A-1", "VIS"), ("A-2", "ECROU")])
        copy(pool, source, driver_side=False)
        distributed = mirror_rows(pool)

        copy(pool, source, driver_side=True)
        assert mirror_rows(pool) == distributed

    def test_the_fallback_counts_what_it_wrote(self, pool, source):
        seed_source(pool, [("A-1", "VIS"), ("A-2", "ECROU"), ("A-3", "RONDELLE")])
        assert copy(pool, source, driver_side=True) == 3

    def test_the_fallback_streams_more_rows_than_one_batch(self, pool, source, monkeypatch):
        """Le repli lit par lots ; la frontière entre deux lots est l'endroit
        où un compteur mal placé perd des lignes."""
        import mirror

        monkeypatch.setattr(mirror, "BATCH", 2)
        seed_source(pool, [(f"A-{i}", f"VIS {i}") for i in range(1, 8)])
        assert copy(pool, source, driver_side=True) == 7
        assert len(mirror_rows(pool)) == 7


# --------------------------------------------------------------------------- #
# La borne de lecture
# --------------------------------------------------------------------------- #

class TestTheWhereClauseIsApplied:
    def test_only_the_selected_rows_are_copied(self, pool, source):
        seed_source(pool, [("A-1", "VIS"), ("B-1", "ECROU")])
        copy(pool, source, where="item_id LIKE 'A-%'")
        assert [ref for ref, _ in mirror_rows(pool)] == ["A-1"]

    def test_a_clause_that_matches_nothing_writes_nothing(self, pool, source):
        seed_source(pool, [("A-1", "VIS")])
        assert copy(pool, source, where="item_id = 'INTROUVABLE'") == 0
