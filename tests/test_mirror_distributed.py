"""Le miroir ne passe plus par le driver.

Les deux synchronisations faisaient la même chose : ``spark.sql(...).collect()``,
puis un ``executemany`` par lots. La lecture ramenait **toute** la table dans la
mémoire du driver, en tuples Python.

Sur le référentiel articles cela passe encore. Sur ``mouvements`` — un article
× un jour sur toute une période — cela ne passe pas : quelques millions de
lignes devenues autant de tuples Python, sur un driver qui a la mémoire d'une
machine et pas celle d'un cluster. Le job ne ralentit pas, il meurt, et il meurt
**après** avoir lu : au bout du seul travail coûteux.

Trois choses sont vérifiées ici, et la première est la seule qui compte
vraiment.

**Plus aucun ``collect()`` sur le chemin de copie.** C'est le geste exact qui
matérialise la table entière. Un contrôle qui lirait « le job est distribué »
ne voudrait rien dire ; celui-ci nomme l'appel.

**La substitution garde ce qu'elle garantissait.** L'application ne voit jamais
un miroir vide ni à moitié rempli. Ce qui change est la durée de la
transaction : elle couvre la substitution seule, là où elle restait ouverte
pendant toute la lecture.

**Une lecture vide ne remplace toujours rien.** Le compte vient désormais de la
base plutôt que d'un ``len()`` sur une liste — c'est le même garde-fou, sur une
autre source, et c'est exactement le genre de détail qu'une réécriture perd.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
sys.path.insert(0, str(JOBS))

import mirror  # noqa: E402

CLI = JOBS / "sync_erp_mirror.py"
NOTEBOOK = JOBS / "sync_erp_mirror_notebook.py"
SHARED = JOBS / "mirror.py"


def code_of(path: Path) -> str:
    """Le code d'un module, docstrings et commentaires retirés.

    Les docstrings expliquent ce que `collect()` faisait ; les lire comme du
    code ferait échouer le contrôle sur sa propre explication.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            node.value.value = ""
    return ast.unparse(tree)


def body_of(path: Path, name: str) -> str:
    """Le corps d'une fonction, isolé.

    Chercher dans le module entier ferait passer un contrôle grâce à une autre
    fonction ; découper sur un nombre de caractères le ferait échouer dès qu'un
    commentaire s'allonge.
    """
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(ast.unparse(statement) for statement in node.body)
    raise AssertionError(f"{name} n'est pas défini dans {path.name}")


# --------------------------------------------------------------------------- #
# Rien ne converge plus vers le driver
# --------------------------------------------------------------------------- #

class TestNothingIsCollected:
    def test_the_shared_copy_never_collects(self):
        """`collect()` est le geste qui matérialise la table entière."""
        assert "collect()" not in code_of(SHARED)

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_neither_job_collects_a_mirror_table(self, path):
        """Il reste des `collect()` sur des agrégats — un `count(*)`, une
        identité — et c'est légitime : une ligne unique n'est pas une table."""
        source = code_of(path)
        for forbidden in (
            "spark.sql(query).collect()",
            "for row in spark.sql(query).collect()",
        ):
            assert forbidden not in source, forbidden

    def test_the_fallback_streams_rather_than_materialises(self):
        """`toLocalIterator` ramène une partition à la fois : la bande passante
        reste celle d'une machine, la mémoire ne dépend plus de la table."""
        source = code_of(SHARED)
        assert "toLocalIterator()" in source

    def test_the_reader_returns_a_frame_and_not_rows(self):
        """Rendre une liste, c'est avoir déjà tout lu."""
        assert "return spark.sql(query)" in body_of(SHARED, "frame_of")


# --------------------------------------------------------------------------- #
# Le remplissage distribué
# --------------------------------------------------------------------------- #

class FakeFrame:
    """Un DataFrame qui note ce qu'on lui demande, sans Spark."""

    def __init__(self, rows: list[tuple] = ()) -> None:
        self.rows = list(rows)
        self.calls: list[Any] = []
        #: Ce que l'écriture a reçu — `option(...)` et `options(**...)` mêlés,
        #: comme Spark les mêle.
        self.given: dict[str, Any] = {}

    def repartition(self, n: int) -> FakeFrame:
        self.calls.append(("repartition", n))
        return self

    @property
    def write(self) -> FakeFrame:
        return self

    def format(self, name: str) -> FakeFrame:
        self.calls.append(("format", name))
        return self

    def option(self, key: str, value: Any) -> FakeFrame:
        self.given[key] = value
        return self

    def options(self, **kwargs: Any) -> FakeFrame:
        self.given.update(kwargs)
        return self

    def mode(self, name: str) -> FakeFrame:
        self.calls.append(("mode", name))
        return self

    def save(self) -> None:
        self.calls.append(("save", None))

    def toLocalIterator(self):  # noqa: N802 - nom imposé par Spark
        self.calls.append(("toLocalIterator", None))
        return iter(self.rows)


class FakeConn:
    """Une connexion qui note les instructions et rend un compte fixe."""

    def __init__(self, count: int = 0) -> None:
        self.statements: list[str] = []
        self.count = count
        self.commits = 0
        self.inserted: list[tuple] = []

    def execute(self, statement: str) -> Any:
        self.statements.append(statement)
        outer = self

        class Result:
            def fetchone(self):
                return (outer.count,)

        return Result()

    def commit(self) -> None:
        self.commits += 1

    def cursor(self):
        outer = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def executemany(self, statement, rows):
                outer.statements.append(statement)
                outer.inserted.extend(rows)

        return Cur()


class TestTheStagingTable:
    def test_it_is_created_if_it_does_not_exist(self):
        conn, frame = FakeConn(), FakeFrame()
        mirror.stage(conn, frame, "erp_bom", ("a",), jdbc_url="jdbc:x")
        assert any("CREATE TABLE IF NOT EXISTS erp_bom_staging" in s
                   for s in conn.statements)

    def test_it_borrows_the_shape_of_the_table_it_will_replace(self):
        """Laisser Spark inventer les types écrirait un texte dans une colonne
        numérique, et la substitution échouerait à la dernière instruction."""
        conn, frame = FakeConn(), FakeFrame()
        mirror.stage(conn, frame, "erp_bom", ("a",), jdbc_url="jdbc:x")
        assert any("(LIKE erp_bom INCLUDING DEFAULTS)" in s
                   for s in conn.statements)

    def test_it_is_emptied_before_being_filled(self):
        """Un remplissage interrompu y laisse ses lignes ; les ajouter à celles
        du suivant écrirait le double."""
        conn, frame = FakeConn(), FakeFrame()
        mirror.stage(conn, frame, "erp_bom", ("a",), jdbc_url="jdbc:x")
        assert "TRUNCATE erp_bom_staging" in conn.statements

    def test_the_preparation_is_committed_before_the_write(self):
        """Les exécuteurs ouvrent leurs propres connexions : ils ne verraient
        pas une table créée dans une transaction restée ouverte."""
        conn, frame = FakeConn(), FakeFrame()
        mirror.stage(conn, frame, "erp_bom", ("a",), jdbc_url="jdbc:x")
        assert conn.commits >= 1


class TestTheDistributedWrite:
    def stage(self, **kwargs):
        conn, frame = FakeConn(count=42), FakeFrame()
        written = mirror.stage(
            conn, frame, "erp_bom", ("a", "b"),
            jdbc_url="jdbc:postgresql://h:5432/d",
            jdbc_properties={"user": "u", "password": "p"},
            **kwargs,
        )
        return conn, frame, written

    def test_it_writes_through_jdbc(self):
        _, frame, _ = self.stage()
        assert ("format", "jdbc") in frame.calls

    def test_it_appends_rather_than_overwrites(self):
        """`overwrite` ferait DROP puis CREATE avec les types devinés par Spark,
        et la table d'attente cesserait de ressembler à sa cible."""
        _, frame, _ = self.stage()
        assert ("mode", "append") in frame.calls

    def test_it_spreads_the_write_over_several_partitions(self):
        _, frame, _ = self.stage()
        assert ("repartition", mirror.WRITE_PARTITIONS) in frame.calls

    def test_the_parallelism_stays_within_what_a_pool_accepts(self):
        """Une partition par cœur donnerait des centaines de connexions
        simultanées sur une base qui en accepte quelques dizaines."""
        assert 2 <= mirror.WRITE_PARTITIONS <= 32

    def test_it_targets_the_staging_table_and_not_the_mirror(self):
        """Écrire directement dans le miroir le laisserait à moitié rempli
        pendant plusieurs minutes, visible par l'application."""
        _, frame, _ = self.stage()
        assert frame.given["dbtable"] == "erp_bom_staging"

    def test_it_writes_where_the_caller_said(self):
        """L'URL vient de l'appelant, qui l'a dérivée de la connexion déjà
        établie : la redécouvrir ici ferait deux résolutions dont une périmée."""
        _, frame, _ = self.stage()
        assert frame.given["url"] == "jdbc:postgresql://h:5432/d"

    def test_the_credentials_reach_the_executors(self):
        """Ils ouvrent leurs propres connexions : sans identifiants, chaque
        partition échoue à l'authentification, et seulement à l'exécution."""
        _, frame, _ = self.stage()
        assert frame.given["user"] == "u"
        assert frame.given["password"] == "p"

    def test_the_executors_send_rows_by_batches(self):
        """Un aller-retour par ligne rendrait l'écriture distribuée plus lente
        que celle qu'elle remplace."""
        _, frame, _ = self.stage()
        assert frame.given["batchsize"] == mirror.BATCH

    def test_the_row_count_comes_from_the_database(self):
        """Le driver n'a rien vu passer : il ne peut pas compter lui-même."""
        conn, _, written = self.stage()
        assert written == 42
        assert any(s.startswith("SELECT count(*) FROM erp_bom_staging")
                   for s in conn.statements)

    def test_nothing_is_streamed_through_the_driver(self):
        _, frame, _ = self.stage()
        assert ("toLocalIterator", None) not in frame.calls


class TestTheDriverSideFallback:
    def test_it_streams_partition_by_partition(self):
        conn = FakeConn()
        frame = FakeFrame([("a", 1), ("b", 2)])
        mirror.stage(conn, frame, "erp_bom", ("x", "y"), driver_side=True)
        assert ("toLocalIterator", None) in frame.calls

    def test_it_writes_what_it_streamed(self):
        conn = FakeConn()
        frame = FakeFrame([("a", 1), ("b", 2)])
        written = mirror.stage(conn, frame, "erp_bom", ("x", "y"), driver_side=True)
        assert written == 2
        assert conn.inserted == [("a", 1), ("b", 2)]

    def test_it_is_chosen_when_no_jdbc_url_is_available(self):
        """Sans URL, écrire par JDBC échouerait à l'exécution plutôt que de
        retomber sur ce qui marche."""
        conn = FakeConn()
        frame = FakeFrame([("a", 1)])
        mirror.stage(conn, frame, "erp_bom", ("x", "y"))
        assert ("toLocalIterator", None) in frame.calls

    def test_the_batches_are_bounded(self):
        """Une seule instruction de deux millions de valeurs est ce qu'on évite."""
        assert 100 <= mirror.BATCH <= 50_000

    def test_it_flushes_along_the_way_rather_than_at_the_end(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Accumuler jusqu'au bout referait ce que ce module existe pour éviter.

        Cinq lots de deux lignes, et non un lot de dix : la mémoire du driver
        reste bornée par ``BATCH``, pas par la taille de la table.
        """
        monkeypatch.setattr(mirror, "BATCH", 2)
        conn = FakeConn()
        frame = FakeFrame([(str(i), i) for i in range(10)])
        written = mirror.stage(conn, frame, "erp_bom", ("x", "y"), driver_side=True)
        inserts = [s for s in conn.statements if s.startswith("INSERT INTO")]
        assert len(inserts) == 5
        assert written == 10

    def test_it_counts_every_batch_and_not_only_the_last(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Le compte décide de la substitution : sous-compter à zéro ferait
        renoncer à un miroir correctement rempli."""
        monkeypatch.setattr(mirror, "BATCH", 2)
        conn = FakeConn()
        frame = FakeFrame([(str(i), i) for i in range(7)])
        written = mirror.stage(conn, frame, "erp_bom", ("x", "y"), driver_side=True)
        # Sept lignes = trois lots pleins et un reste : les deux branches du
        # compteur passent, et seule leur somme donne sept.
        assert written == 7
        assert len(conn.inserted) == 7


# --------------------------------------------------------------------------- #
# La substitution garde ce qu'elle garantissait
# --------------------------------------------------------------------------- #

class TestTheSwap:
    def statements(self, **kwargs) -> list[str]:
        conn = FakeConn()
        mirror.swap(conn, "erp_base_article", ("item_id", "item_name"), **kwargs)
        return conn.statements

    def test_the_table_is_emptied_before_being_written(self):
        assert "TRUNCATE erp_base_article" in self.statements()

    def test_it_reads_from_the_staging_table(self):
        assert any("FROM erp_base_article_staging" in s for s in self.statements())

    def test_the_truncate_comes_before_the_insert(self):
        """Dans la transaction de l'appelant : l'application ne voit jamais un
        miroir vide ni à moitié rempli."""
        statements = self.statements()
        truncate = next(
            i for i, s in enumerate(statements) if s.startswith("TRUNCATE")
        )
        insert = next(
            i for i, s in enumerate(statements)
            if s.startswith("INSERT INTO erp_base_article (")
        )
        assert truncate < insert

    def test_it_never_commits_by_itself(self):
        """Le commit appartient à l'appelant, qui substitue cinq tables : en
        valider une seule laisserait le miroir mi-neuf mi-vieux."""
        conn = FakeConn()
        mirror.swap(conn, "erp_bom", ("a",))
        assert conn.commits == 0

    def test_the_last_filter_keeps_one_row_per_key(self):
        """L'échec le plus cher est la dernière instruction d'un travail fini."""
        assert any("DISTINCT ON (item_id)" in s
                   for s in self.statements(unique_on="item_id"))

    def test_and_orders_so_two_runs_choose_the_same_one(self):
        assert any("ORDER BY item_id" in s
                   for s in self.statements(unique_on="item_id"))

    def test_a_table_with_no_key_keeps_every_row(self):
        """Une nomenclature a plusieurs versions par couple : c'est normal."""
        assert not any("DISTINCT" in s for s in self.statements())

    def test_the_rows_are_counted_on_both_sides(self):
        counts = [s for s in self.statements() if s.startswith("SELECT count(*)")]
        assert len(counts) == 2, "avant et après, sinon le chiffre ne dit rien"


# --------------------------------------------------------------------------- #
# Ce que les deux jobs partagent maintenant
# --------------------------------------------------------------------------- #

class TestOneCopyForBothJobs:
    """Les deux synchronisations avaient déjà divergé une fois : la reprise des
    mouvements n'était passée que par le notebook. La copie est justement la
    partie qu'on ne veut pas voir diverger deux fois."""

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_the_job_imports_the_shared_copy(self, path):
        source = path.read_text()
        assert "from mirror import" in source

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_neither_job_redefines_the_swap(self, path):
        """`_swap` du job en ligne de commande est une façade d'une ligne.

        Ce qui compte est qu'aucun des deux ne réécrive la substitution
        elle-même : deux TRUNCATE + INSERT du même miroir finiraient par ne
        plus garantir la même chose, et c'est déjà arrivé une fois sur la
        reprise des mouvements.
        """
        assert "TRUNCATE" not in code_of(path)

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_neither_job_writes_the_mirror_itself(self, path):
        """L'insertion dans le miroir vient du module, pas de l'appelant."""
        assert "INSERT INTO" not in code_of(path)

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_the_batch_size_is_declared_once(self, path):
        """Deux constantes du même nom finissent par ne plus valoir la même."""
        assert "BATCH = " in SHARED.read_text()
        assert "BATCH = " not in code_of(path)


class TestTheGuardsThatMustSurviveTheRewrite:
    """Une réécriture perd les garde-fous avant de perdre la fonction."""

    def test_an_empty_read_still_leaves_the_mirror_intact(self):
        """Le compte vient de la base plutôt que d'un `len()` : même règle,
        autre source, et c'est ce qu'une réécriture perd en premier."""
        source = code_of(CLI)
        assert "miroir laissé intact" in CLI.read_text()
        assert "if not loaded:" in source

    @pytest.mark.parametrize(
        "table",
        ["erp_base_article", "erp_bom", "erp_ecart_backflush", "erp_mouvements",
         "erp_stock_snapshot"],
    )
    def test_every_table_written_has_its_shape_checked_first(self, table):
        """Échouer en une seconde plutôt qu'au bout du seul travail coûteux.

        Table par table : une seule vérification en tête suffirait à faire
        passer un contrôle qui cherche l'appel, alors que c'est précisément
        celle qu'on a oubliée qui refusera la dernière instruction.
        """
        source = code_of(CLI)
        checked = source.index(f"_assert_mirror_shape(conn, '{table}'")
        assert checked < source.index("prepare(")

    def test_the_rows_without_a_reference_are_still_filtered_out(self):
        assert "reference IS NOT NULL" in CLI.read_text()

    def test_the_fallback_is_reachable_from_the_command_line(self):
        """Un repli qu'on ne peut pas choisir n'est pas un repli."""
        assert '"--driver-side"' in CLI.read_text()
