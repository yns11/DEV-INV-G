"""Le calcul serverless refuse l'écriture JDBC, et la copie aboutit quand même.

Ce qui est arrivé
-----------------
Première exécution réelle du job de synchronisation, en production :

    [UNSUPPORTED_DATA_SOURCE_WRITE] The input query contains unsupported data
    source(s). Only csv, json, avro, delta, kafka, parquet, orc, text,
    unity_catalog, binaryFile, xml, excel, simplescan, iceberg, file, mysql,
    postgresql, sqlserver, snowflake, redshift data sources are allowed to run
    DML on serverless compute.

Le remplissage distribué écrit par ``format("jdbc")`` ; Databricks restreint le
DML à une liste de sources sur le calcul serverless, et le connecteur JDBC
générique n'en fait pas partie. Le mot « postgresql » y figure et ne sauve rien :
il désigne la fédération par connexion Unity Catalog, pas une URL avec un mot de
passe.

Le repli existait déjà — ``driver_side=True`` — mais il fallait le demander. Le
job mourait donc à la première table, **après** avoir lu, c'est-à-dire au bout
du seul travail coûteux, et il fallait connaître le drapeau pour en sortir.

Ce que ces contrôles tiennent
-----------------------------
Le repli sur ce refus précis, le fait qu'il soit dit, et — ce qui compte
autant — le fait qu'aucune **autre** panne d'écriture ne soit rattrapée : les
identifiants et les colonnes manquantes doivent continuer d'échouer tout de
suite, sous leur propre nom.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

JOBS = Path(__file__).resolve().parents[1] / "jobs"

REFUSAL = (
    "[UNSUPPORTED_DATA_SOURCE_WRITE] The input query contains unsupported data "
    "source(s). Only csv, json, avro, delta, kafka, parquet, orc, text, "
    "unity_catalog, binaryFile, xml, excel, simplescan, iceberg, file, mysql, "
    "postgresql, sqlserver, snowflake, redshift data sources are allowed to run "
    "DML on serverless compute. SQLSTATE: 0A000;"
)


def load_mirror() -> Any:
    spec = importlib.util.spec_from_file_location("mirror_fallback", JOBS / "mirror.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


mirror = load_mirror()

COLUMNS = ("item_id", "item_name")
ROWS = [("P-1", "STATOR"), ("P-2", "ROTOR")]


class FakeCursor:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.inserted: list[tuple] = []

    def executemany(self, sql: str, batch: list[tuple]) -> None:
        self._log.append(f"executemany:{sql.split()[2]}")
        self.inserted.extend(batch)

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self._log.append(sql.split()[0].upper())
        return self

    def fetchone(self) -> tuple[int]:
        return (len(self.inserted),)

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class FakeConnection:
    """Le strict nécessaire : ce qui est exécuté, dans l'ordre."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self._cursor = FakeCursor(self.log)

    def cursor(self) -> FakeCursor:
        return self._cursor

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self.log.append(sql.split()[0].upper())
        return self._cursor

    def commit(self) -> None:
        self.log.append("COMMIT")

    def rollback(self) -> None:
        self.log.append("ROLLBACK")


class FakeWriter:
    def __init__(self, frame: FakeFrame) -> None:
        self._frame = frame

    def format(self, _: str) -> FakeWriter:
        return self

    def option(self, *_: Any) -> FakeWriter:
        return self

    def options(self, **_: Any) -> FakeWriter:
        return self

    def mode(self, _: str) -> FakeWriter:
        return self

    def save(self) -> None:
        self._frame.jdbc_attempts += 1
        if self._frame.refusal is not None:
            raise self._frame.refusal


class FakeFrame:
    """Un DataFrame qui refuse l'écriture distribuée, ou non."""

    def __init__(self, refusal: Exception | None) -> None:
        self.refusal = refusal
        self.jdbc_attempts = 0
        self.local_iterations = 0

    def repartition(self, _: int) -> FakeFrame:
        return self

    @property
    def write(self) -> FakeWriter:
        return FakeWriter(self)

    def toLocalIterator(self):  # noqa: N802 — c'est le nom de PySpark
        self.local_iterations += 1
        return iter(ROWS)


def run(refusal: Exception | None, **kwargs: Any) -> tuple[int, FakeFrame, list[str]]:
    frame = FakeFrame(refusal)
    conn = FakeConnection()
    said: list[str] = []
    written = mirror.stage(
        conn, frame, "erp_base_article", COLUMNS,
        jdbc_url="jdbc:postgresql://host/db",
        jdbc_properties={"user": "u", "password": "p"},
        say=said.append,
        **kwargs,
    )
    return written, frame, said


class TestTheRefusalIsCaught:
    def test_the_copy_still_lands(self):
        """C'est tout l'enjeu : le job aboutit au lieu de mourir à la lecture."""
        written, frame, _ = run(RuntimeError(REFUSAL))
        assert written == len(ROWS)
        assert frame.local_iterations == 1

    def test_the_distributed_path_was_tried_first(self):
        """Le repli est un repli : sur un cluster classique, il ne sert pas."""
        _, frame, _ = run(RuntimeError(REFUSAL))
        assert frame.jdbc_attempts == 1

    def test_it_says_so(self):
        """Une copie deux fois plus lente sans explication passe pour la normale."""
        _, _, said = run(RuntimeError(REFUSAL))
        assert len(said) == 1
        assert "serverless" in said[0]
        assert "driver-side" in said[0]

    def test_the_staging_table_is_emptied_before_the_retry(self):
        """Une écriture partielle laisserait ses lignes ; le repli les doublerait."""
        _, _, _ = run(RuntimeError(REFUSAL))
        # Deux TRUNCATE : celui d'avant la tentative, celui d'avant le repli.
        conn = FakeConnection()
        frame = FakeFrame(RuntimeError(REFUSAL))
        mirror.stage(
            conn, frame, "erp_base_article", COLUMNS,
            jdbc_url="jdbc:postgresql://host/db", say=lambda _: None,
        )
        assert conn.log.count("TRUNCATE") == 2
        assert "ROLLBACK" in conn.log


class TestNothingElseIsCaught:
    """Rattraper large ferait payer la lecture deux fois pour la même erreur.

    Et pire : elle reviendrait sous le nom du chemin de repli, à un endroit du
    journal qui ne désigne plus la cause.
    """

    @pytest.mark.parametrize(
        "message",
        [
            'FATAL: password authentication failed for user "app"',
            'column "programme" of relation "erp_base_article_staging" does not exist',
            "connection to server at 'host' failed: timeout expired",
        ],
    )
    def test_a_real_write_failure_still_stops_the_job(self, message):
        with pytest.raises(RuntimeError) as raised:
            run(RuntimeError(message))
        # Et sous son propre nom : ré-emballer la cause la rendrait
        # introuvable dans un journal de job.
        assert message in str(raised.value)

    def test_and_the_driver_path_is_not_even_tried(self):
        frame = FakeFrame(RuntimeError("FATAL: password authentication failed"))
        with pytest.raises(RuntimeError):
            mirror.stage(
                FakeConnection(), frame, "erp_base_article", COLUMNS,
                jdbc_url="jdbc:postgresql://host/db", say=lambda _: None,
            )
        assert frame.local_iterations == 0


class TestTheHappyPathIsUnchanged:
    def test_a_cluster_that_accepts_jdbc_never_reaches_the_driver(self):
        written, frame, said = run(None)
        assert frame.jdbc_attempts == 1
        assert frame.local_iterations == 0
        assert said == []
        # Le compte revient de la base, pas du repli.
        assert written == 0

    def test_asking_for_the_driver_skips_the_attempt(self):
        _, frame, said = run(RuntimeError(REFUSAL), driver_side=True)
        assert frame.jdbc_attempts == 0
        assert frame.local_iterations == 1
        assert said == []


class TestBothSynchronisationsSeeIt:
    """Corriger un seul des deux chemins est le défaut déjà connu ici."""

    @staticmethod
    def _stage_call(name: str) -> str:
        """L'appel à ``stage(`` du fichier, parenthèses équilibrées.

        Chercher ``say=`` dans tout le fichier ne prouverait rien : la
        substitution en passe un aussi, et le contrôle resterait vert sur un
        `stage` redevenu muet.
        """
        source = (JOBS / name).read_text(encoding="utf-8")
        start = source.index("return stage(\n") + len("return stage")
        depth = 0
        for index in range(start, len(source)):
            depth += {"(": 1, ")": -1}.get(source[index], 0)
            if depth == 0:
                return source[start : index + 1]
        raise AssertionError(f"appel à stage( non refermé dans {name}")

    @pytest.mark.parametrize(
        "name", ["sync_erp_mirror.py", "sync_erp_mirror_notebook.py"]
    )
    def test_the_caller_passes_somewhere_to_say_it(self, name):
        assert "say=" in self._stage_call(name), (
            f"{name} appelle `stage` sans `say` : le repli serait silencieux."
        )

    @pytest.mark.parametrize(
        "name", ["sync_erp_mirror.py", "sync_erp_mirror_notebook.py"]
    )
    def test_the_call_was_actually_found(self, name):
        """Un extrait vide satisferait n'importe quelle assertion suivante."""
        assert len(self._stage_call(name)) > 60
