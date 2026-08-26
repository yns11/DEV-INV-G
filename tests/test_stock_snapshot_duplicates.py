"""Le miroir du stock accepte ce que la source publie vraiment.

Ce qui est arrivé
-----------------
Deuxième exécution réelle du job de synchronisation. Articles, nomenclatures,
écart backflush et mouvements passent ; la substitution du stock échoue :

    duplicate key value violates unique constraint "erp_stock_snapshot_pkey"
    DETAIL: Key (snapshot_date, item_id, entrepot, emplacement)
            = (2026-08-21, mass-00037799, QUAL VRAC, PRISON QO) already exists.

La migration 013 avait posé une clé primaire sur ces quatre colonnes, sur la
foi de ce que la source annonce — « une ligne par article × entrepôt ×
emplacement ». Elle ne le tient pas, et c'est **normal** : le stock d'un
emplacement se répartit sur plusieurs lignes dès qu'une dimension que le miroir
ne copie pas — lot, statut qualité — les distingue. L'emplacement du refus,
« PRISON QO » dans l'entrepôt « QUAL VRAC », est une zone de quarantaine.

L'application le sait depuis toujours : ``map_book_stock`` **somme** les
doublons, et sa docstring dit pourquoi — n'en garder qu'un sous-évaluerait le
stock ERP. La contrainte contredisait donc à la fois la source et le seul
endroit du code qui a un avis sur la question.

La fenêtre de sept photos (`stock_days`) n'a pas créé ce défaut, elle l'a
révélé : tant qu'une seule journée était copiée, le doublon d'un jour plus
ancien restait hors du miroir.

Ce que ces contrôles tiennent
-----------------------------
Le refus tel qu'il s'est produit, sa disparition, et surtout ce qui **ne doit
pas** être fait à la place : dédupliquer garderait une ligne sur deux et
sous-évaluerait le stock sans que rien ne le signale.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "app" / "inventory" / "db" / "migrations"

#: La ligne du refus, telle que la production l'a rendue — deux fois.
DUPLICATE = ("2026-08-21", "mass-00037799", "QUAL VRAC", "PRISON QO")
#: La base jetable de ces contrôles. Séparée, parce qu'ils reposent le schéma.
SCRATCH = "inventaire_controle_stock"

INSERT = (
    "INSERT INTO inventory.erp_stock_snapshot "
    "(snapshot_date, item_id, entrepot, emplacement, stock_physique, unite) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


class TestTheSourceIsAllowedToSplitALocation:
    """Contre un vrai PostgreSQL : une contrainte ne se teste pas en doublure.

    C'est exactement la classe de défaut qu'une doublure a déjà laissé passer
    ici — un appel dont la forme est juste et que la base refuse.
    """

    @pytest.fixture
    def connection(self):
        """Une base **à part**, jamais celle de l'application.

        Ces contrôles reposent le schéma `inventory` entre chaque cas : le faire
        dans la base partagée effaçait celle des autres contrôles PostgreSQL, qui
        tombaient alors en cascade sans rapport avec ce qu'ils vérifient. Un
        contrôle qui casse ses voisins est un contrôle faux.
        """
        if not os.environ.get("PGHOST"):
            pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
        import psycopg

        def dsn(database: str) -> str:
            return (
                f"host={os.environ['PGHOST']} "
                f"user={os.environ.get('PGUSER', 'postgres')} "
                f"dbname={database} "
                f"password={os.environ.get('PGPASSWORD', '')} "
                f"sslmode={os.environ.get('PGSSLMODE', 'disable')}"
            )

        try:
            admin = psycopg.connect(
                dsn(os.environ.get("PGDATABASE", "postgres")), autocommit=True
            )
        except Exception as exc:  # pragma: no cover - dépend de l'infrastructure
            pytest.skip(f"PostgreSQL injoignable : {exc}")
        with admin:
            admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")
            admin.execute(f"CREATE DATABASE {SCRATCH}")
        try:
            with psycopg.connect(dsn(SCRATCH), autocommit=True) as conn:
                yield conn
        finally:
            with psycopg.connect(
                dsn(os.environ.get("PGDATABASE", "postgres")), autocommit=True
            ) as admin:
                admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")

    @staticmethod
    def _apply(conn: Any, *versions: str) -> None:
        conn.execute("DROP SCHEMA IF EXISTS inventory CASCADE; CREATE SCHEMA inventory")
        for version in versions:
            [path] = MIGRATIONS.glob(f"{version}_*.sql")
            conn.execute(path.read_text(encoding="utf8"))

    @staticmethod
    def _insert_both(conn: Any) -> None:
        for quantity in (3, 5):
            conn.execute(INSERT, (*DUPLICATE, quantity, "PCE"))

    def test_the_shipped_constraint_did_refuse_it(self, connection):
        """Le défaut, rejoué. Sans lui, la correction ne prouverait rien."""
        import psycopg

        self._apply(connection, "013")
        with pytest.raises(psycopg.errors.UniqueViolation) as refused:
            self._insert_both(connection)
        assert "erp_stock_snapshot_pkey" in str(refused.value)

    def test_and_no_longer_does(self, connection):
        self._apply(connection, "013", "024")
        self._insert_both(connection)
        [(count,)] = connection.execute(
            "SELECT count(*) FROM inventory.erp_stock_snapshot"
        ).fetchall()
        assert count == 2

    def test_both_quantities_survive(self, connection):
        """C'est le point : garder une ligne sur deux sous-évaluerait le stock.

        Trois et cinq font huit — le total que ``map_book_stock`` calculera. Une
        déduplication en aurait rendu trois, ou cinq, et l'écart d'inventaire
        aurait été faux sans que rien ne le dise.
        """
        self._apply(connection, "013", "024")
        self._insert_both(connection)
        [(total,)] = connection.execute(
            "SELECT sum(stock_physique) FROM inventory.erp_stock_snapshot"
        ).fetchall()
        assert int(total) == 8

    def test_replaying_the_migration_changes_nothing(self, connection):
        """Les migrations de ce dépôt se rejouent ; celle-ci ne fait pas exception."""
        self._apply(connection, "013", "024")
        [path] = MIGRATIONS.glob("024_*.sql")
        connection.execute(path.read_text(encoding="utf8"))
        self._insert_both(connection)
        [(count,)] = connection.execute(
            "SELECT count(*) FROM inventory.erp_stock_snapshot"
        ).fetchall()
        assert count == 2

    def test_the_read_still_has_its_index(self, connection):
        """La clé partait, l'index qu'elle fournissait reste.

        La lecture résout d'abord la date, puis lit les lignes de ce jour :
        c'est cet ordre-là qui est servi, et le perdre transformerait chaque
        import de stock en balayage complet de sept journées.
        """
        self._apply(connection, "013", "024")
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'inventory' AND tablename = 'erp_stock_snapshot'"
            ).fetchall()
        }
        assert "erp_stock_snapshot_key_idx" in indexes
        assert "erp_stock_snapshot_day_idx" in indexes


class TestNobodyDeduplicatesTheStockOnTheWayIn:
    """La correction d'une ligne qu'il ne fallait pas écrire.

    ``swap`` sait dédupliquer — ``unique_on`` — et l'appliquer au stock aurait
    fait passer le job au vert en sous-évaluant le miroir. Ce contrôle refuse
    cette correction-là, dans les deux synchronisations, pour que personne ne la
    reprenne au prochain refus.
    """

    JOBS = Path(__file__).resolve().parents[1] / "jobs"

    @classmethod
    def _swap_call(cls, name: str) -> str:
        """L'appel qui substitue le stock, parenthèses équilibrées.

        Ancré sur ``swap(conn,`` et non sur le seul nom de la table : celui-ci
        paraît trois fois par fichier, et la première occurrence est la
        vérification de forme. Une version antérieure de ce contrôle lisait
        cette occurrence-là — elle ne pouvait donc jamais voir un `unique_on`
        posé sur la substitution, et deux mutations lui sont passées sous le nez.
        """
        source = (cls.JOBS / name).read_text(encoding="utf8")
        anchor = 'swap(conn, "erp_stock_snapshot"'
        start = source.index(anchor) + len(anchor) - len('conn, "erp_stock_snapshot"') - 1
        depth = 0
        for index in range(start, len(source)):
            depth += {"(": 1, ")": -1}.get(source[index], 0)
            if depth == 0:
                return source[start : index + 1]
        raise AssertionError(f"appel à swap( non refermé dans {name}")

    @pytest.mark.parametrize(
        "name", ["sync_erp_mirror.py", "sync_erp_mirror_notebook.py"]
    )
    def test_the_stock_swap_asks_for_no_unique_key(self, name):
        assert "unique_on" not in self._swap_call(name), (
            "dédupliquer le stock garderait une ligne sur deux et sous-évaluerait "
            "le miroir — voir la docstring de map_book_stock."
        )

    @pytest.mark.parametrize(
        "name", ["sync_erp_mirror.py", "sync_erp_mirror_notebook.py"]
    )
    def test_the_extract_is_really_the_swap(self, name):
        """Un extrait pris ailleurs satisferait l'assertion précédente sans rien
        vérifier — c'est exactement ce qui est arrivé."""
        call = self._swap_call(name)
        assert call.startswith("(conn,")
        assert "erp_stock_snapshot" in call
        assert "STOCK_COLUMNS" in call

    def test_the_application_still_sums_them(self):
        """La règle vit dans `map_book_stock` ; c'est elle qui rend la copie sûre.

        Si elle disparaissait, garder les deux lignes ne suffirait plus : la
        dernière écraserait la première, et le stock serait sous-évalué malgré
        un miroir fidèle.
        """
        from inventory.ingest import mappers

        assert "summed" in (mappers.map_book_stock.__doc__ or "")
