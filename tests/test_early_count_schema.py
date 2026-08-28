"""Ce que la migration 025 garantit structurellement.

Trois règles de l'étude des comptages avancés ne sont pas des vérifications que
du code exécute, mais des contraintes que Postgres tient. Elles méritent d'être
contrôlées ici, parce qu'un code applicatif futur qui les oublierait ne casserait
rien de visible — la base, elle, refuserait.

* **un emplacement n'appartient au périmètre que d'un seul journal ERP** — c'est
  ce qui rend vraie la proposition « hors emplacements déjà alloués » sans que
  le calcul qui la produit ait à être exact ;
* **le doublon « journal ERP + numéro de ligne » est impossible**, plutôt que
  détecté après coup par un contrôle qu'on pourrait oublier de brancher ;
* **une ligne ERP ne peut pas pointer vers un journal d'une autre campagne** —
  la règle des clés composites de la migration 018, étendue aux tables neuves.

Ces contrôles ouvrent leur propre base. Une version antérieure travaillait dans
le schéma partagé et le détruisait en fin de test, emportant vingt et un autres
contrôles avec elle.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.postgres

#: Base dédiée : ce test crée et supprime, il ne partage pas.
DB_NAME = "inventaire_comptages_avances"


def _admin_dsn() -> str:
    host = os.environ.get("PGHOST")
    if not host:
        pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
    user = os.environ.get("PGUSER", "postgres")
    password = os.environ.get("PGPASSWORD", "")
    port = os.environ.get("PGPORT", "5432")
    return f"host={host} port={port} user={user} password={password} dbname=postgres"


@pytest.fixture(scope="module")
def db():
    psycopg = pytest.importorskip("psycopg")
    try:
        admin = psycopg.connect(_admin_dsn(), autocommit=True)
    except Exception as exc:  # pragma: no cover - dépend de l'infrastructure
        pytest.skip(f"PostgreSQL injoignable : {exc}")

    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
        admin.execute(f'CREATE DATABASE "{DB_NAME}"')

    from inventory.config import Settings
    from inventory.db.engine import Database
    from inventory.db.migrations import apply_all

    # `Settings(pg_database=…)` ne suffit pas : le champ porte l'alias
    # ``PGDATABASE`` et la variable d'environnement l'emporte sur l'argument.
    # Une première version de ce contrôle croyait donc travailler dans une base
    # dédiée alors qu'elle écrivait dans la base partagée — et une mutation qui
    # aurait dû prouver quelque chose y mourait sur la garde d'empreinte des
    # migrations déjà appliquées, c'est-à-dire pour la mauvaise raison.
    previous = os.environ.get("PGDATABASE")
    os.environ["PGDATABASE"] = DB_NAME
    try:
        database = Database(Settings())
        with database.connection() as conn:
            reached = conn.execute("SELECT current_database() AS d").fetchone()["d"]
        assert reached == DB_NAME, (
            f"Ces contrôles écriraient dans « {reached} », pas dans la base "
            "jetable : ils pollueraient la base partagée et leurs mutations "
            "mourraient sur la garde d'empreinte des migrations."
        )
        apply_all(database)
        yield database
        database.close()
    finally:
        if previous is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = previous

    with psycopg.connect(_admin_dsn(), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')


@pytest.fixture
def campaign(db):
    """Une campagne jetable, et son nettoyage."""
    campaign_id = str(uuid.uuid4())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO campaign (id, code, label, count_date, created_by) "
            "VALUES (%s, %s, '', current_date, 'test')",
            (campaign_id, f"CA-{campaign_id[:8]}"),
        )
    yield campaign_id
    with db.transaction() as conn:
        conn.execute("DELETE FROM audit_event WHERE campaign_id = %s", (campaign_id,))
        conn.execute("DELETE FROM campaign WHERE id = %s", (campaign_id,))


def _journal(db, campaign_id: str, number: str) -> str:
    journal_id = str(uuid.uuid4())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO erp_journal (id, campaign_id, journal_number, kind) "
            "VALUES (%s, %s, %s, 'INVE')",
            (journal_id, campaign_id, number),
        )
    return journal_id


class TestTheMigrationReplays:
    """« Idempotent : rejouable sans effet de bord » n'est pas une formule.

    La première version employait le `DROP CONSTRAINT … ADD CONSTRAINT` de la
    migration 018 pour poser les clés `(id, campagne)`. Ça se rejoue tant que
    rien ne dépend de l'index — ce qui était le cas en 018, dont les dépendants
    vivaient dans d'autres fichiers. Ici les clés étrangères composites sont
    dans le même fichier, et Postgres refuse de retirer un index dont elles
    dépendent : la migration passait une fois et échouait ensuite.

    Un déploiement ne rejoue pas une migration déjà enregistrée, si bien que
    rien ne l'aurait signalé — jusqu'au jour où une reprise, une base recréée à
    partir d'un dump partiel ou un correctif d'empreinte la ferait repasser.
    """

    def test_applying_it_a_second_time_changes_nothing(self, db):
        from inventory.db.migrations import MIGRATIONS_DIR

        sql = (MIGRATIONS_DIR / "025_comptages_avances.sql").read_text(encoding="utf-8")
        with db.transaction() as conn, conn.cursor() as cur:
            cur.execute(sql)
        with db.connection() as conn:
            tables = {
                row["table_name"]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'inventory' "
                    "AND (table_name LIKE 'erp_journal%' OR table_name LIKE 'early_count%')"
                ).fetchall()
            }
        assert tables == {
            "erp_journal", "erp_journal_scope", "erp_journal_line",
            "early_count_batch", "early_count_drift",
        }


class TestOneLocationBelongsToOneJournal:
    """« Hors emplacements déjà alloués à un autre journal », garanti par la base."""

    def test_a_second_journal_cannot_claim_the_same_location(self, db, campaign):
        first = _journal(db, campaign, "NPEM-000001")
        second = _journal(db, campaign, "NPEM-000002")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_scope "
                "(erp_journal_id, campaign_id, warehouse_id, location_id) "
                "VALUES (%s, %s, 'ATP', 'SOL')",
                (first, campaign),
            )
        with pytest.raises(Exception) as caught, db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_scope "
                "(erp_journal_id, campaign_id, warehouse_id, location_id) "
                "VALUES (%s, %s, 'ATP', 'SOL')",
                (second, campaign),
            )
        assert "erp_journal_scope_location_uq" in str(caught.value)

    def test_one_journal_may_cover_many_locations(self, db, campaign):
        """48 journaux sur 73 en couvrent plus d'un : c'est le cas nominal."""
        journal = _journal(db, campaign, "NPEM-000003")
        with db.transaction() as conn:
            for location in ("SOL", "STK P FI", "APQP C0"):
                conn.execute(
                    "INSERT INTO erp_journal_scope "
                    "(erp_journal_id, campaign_id, warehouse_id, location_id) "
                    "VALUES (%s, %s, 'ATP', %s)",
                    (journal, campaign, location),
                )
        with db.connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM erp_journal_scope WHERE erp_journal_id = %s",
                (journal,),
            ).fetchone()["n"]
        assert count == 3

    def test_two_campaigns_may_each_hold_the_same_location(self, db, campaign):
        """Le périmètre est propre à sa campagne, sinon une campagne en bloquerait une autre."""
        other = str(uuid.uuid4())
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO campaign (id, code, label, count_date, created_by) "
                "VALUES (%s, %s, '', current_date, 'test')",
                (other, f"CB-{other[:8]}"),
            )
        try:
            here = _journal(db, campaign, "NPEM-000004")
            there = _journal(db, other, "NPEM-000004")
            with db.transaction() as conn:
                for journal_id, campaign_id in ((here, campaign), (there, other)):
                    conn.execute(
                        "INSERT INTO erp_journal_scope "
                        "(erp_journal_id, campaign_id, warehouse_id, location_id) "
                        "VALUES (%s, %s, 'ATP', 'PARTAGE')",
                        (journal_id, campaign_id),
                    )
        finally:
            with db.transaction() as conn:
                conn.execute("DELETE FROM campaign WHERE id = %s", (other,))


class TestTheDuplicateLineIsImpossible:
    """Le contrôle « doublon Journal ERP + Numéro de ligne » n'a pas à être branché."""

    def test_the_same_line_number_twice_is_refused(self, db, campaign):
        journal = _journal(db, campaign, "NPEM-000010")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, erp_line_number, warehouse_id, "
                " location_id, item_number) "
                "VALUES (%s, %s, %s, 7, 'ATP', 'SOL', 'MASS-1')",
                (str(uuid.uuid4()), journal, campaign),
            )
        with pytest.raises(Exception) as caught, db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, erp_line_number, warehouse_id, "
                " location_id, item_number) "
                "VALUES (%s, %s, %s, 7, 'ATP', 'SOL', 'MASS-2')",
                (str(uuid.uuid4()), journal, campaign),
            )
        assert "erp_journal_line_uq" in str(caught.value)

    def test_a_missing_line_number_is_not_a_duplicate(self, db, campaign):
        """L'index est partiel : un export qui omet le numéro perd sinon tout sauf une ligne.

        Refuser ces lignes-là reviendrait à jeter des quantités comptées pour une
        colonne technique absente.
        """
        journal = _journal(db, campaign, "NPEM-000011")
        with db.transaction() as conn:
            for item in ("MASS-1", "MASS-2", "MASS-3"):
                conn.execute(
                    "INSERT INTO erp_journal_line "
                    "(id, erp_journal_id, campaign_id, warehouse_id, location_id, item_number) "
                    "VALUES (%s, %s, %s, 'ATP', 'SOL', %s)",
                    (str(uuid.uuid4()), journal, campaign, item),
                )
        with db.connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM erp_journal_line WHERE erp_journal_id = %s",
                (journal,),
            ).fetchone()["n"]
        assert count == 3


class TestAChildCannotBelongToAnotherCampaign:
    """La règle de la migration 018, étendue aux tables neuves."""

    def test_a_line_cannot_point_at_another_campaign_s_journal(self, db, campaign):
        other = str(uuid.uuid4())
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO campaign (id, code, label, count_date, created_by) "
                "VALUES (%s, %s, '', current_date, 'test')",
                (other, f"CC-{other[:8]}"),
            )
        try:
            journal = _journal(db, campaign, "NPEM-000020")
            with pytest.raises(Exception) as caught, db.transaction() as conn:
                conn.execute(
                    "INSERT INTO erp_journal_line "
                    "(id, erp_journal_id, campaign_id, warehouse_id, location_id, "
                    " item_number) VALUES (%s, %s, %s, 'ATP', 'SOL', 'MASS-1')",
                    (str(uuid.uuid4()), journal, other),
                )
            assert "erp_journal_line" in str(caught.value).lower()
        finally:
            with db.transaction() as conn:
                conn.execute("DELETE FROM campaign WHERE id = %s", (other,))


class TestTheLabelKeepsItsLeadingZeros:
    """« 001609231 » perd trois caractères au premier passage par un entier."""

    def test_the_column_is_text_and_gives_back_what_was_written(self, db, campaign):
        journal = _journal(db, campaign, "NPEM-000030")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, warehouse_id, location_id, "
                " item_number, label_id, serial_number) "
                "VALUES (%s, %s, %s, 'ATP', 'SOL', 'MASS-1', '001609231', '0012611100220')",
                (str(uuid.uuid4()), journal, campaign),
            )
        with db.connection() as conn:
            row = conn.execute(
                "SELECT label_id, serial_number FROM erp_journal_line "
                "WHERE erp_journal_id = %s",
                (journal,),
            ).fetchone()
        assert row["label_id"] == "001609231"
        assert row["serial_number"] == "0012611100220"
