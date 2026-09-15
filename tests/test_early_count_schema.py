"""Ce que la migration 025 garantit structurellement.

Trois règles de l'étude des comptages avancés ne sont pas des vérifications que
du code exécute, mais des contraintes que Postgres tient. Elles méritent d'être
contrôlées ici, parce qu'un code applicatif futur qui les oublierait ne casserait
rien de visible — la base, elle, refuserait.

* **un emplacement n'appartient au périmètre que d'un seul journal ERP** — c'est
  ce qui rend vraie la proposition « hors emplacements déjà alloués » sans que
  le calcul qui la produit ait à être exact ;
* **le doublon est impossible**, plutôt que détecté après coup par un contrôle
  qu'on pourrait oublier de brancher — sur la clé de la migration 031 : journal,
  site, entrepôt, emplacement, étiquette, article ;
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
    rien ne l'aurait signalé — jusqu'au jour où une reprise ou une base recréée
    à partir d'un dump partiel la ferait repasser.

    Ce que la 029 change, et ce qu'elle ne peut pas rendre
    -----------------------------------------------------
    Elle retire `is_material` de la table des dérives, et la 025 crée un index
    partiel `WHERE is_material AND resolution IS NULL`. Postgres analyse le
    prédicat **avant** de regarder si le nom est déjà pris : un `CREATE INDEX IF
    NOT EXISTS` échoue donc sur une colonne absente, même quand l'index existe.

    Rejouer la 025 seule sur un schéma déjà à jour n'est donc plus possible, et
    aucune écriture de la 029 ne le rendrait possible sans garder en base deux
    colonnes que plus rien ne remplit. Ce qui reste vrai — et qui est ce qu'une
    reprise exécute réellement — est la séquence complète sur les tables qui
    manquent : la 025 les repose telles qu'elle les connaît, et la 029 puis la
    031 les ramènent à leur forme actuelle.

    La 031 est dans le même cas que la 029, pour la même raison : elle supprime
    la colonne `erp_line_number` sur laquelle la 025 crée un index unique.
    """

    def _early_tables(self, db) -> set[str]:
        with db.connection() as conn:
            return {
                row["table_name"]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'inventory' "
                    "AND (table_name LIKE 'erp_journal%' "
                    "     OR table_name LIKE 'early_count%')"
                ).fetchall()
            }

    def _replay(self, db, *names: str) -> None:
        from inventory.db.migrations import MIGRATIONS_DIR

        for name in names:
            sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
            with db.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql)

    def test_the_sequence_rebuilds_what_a_partial_dump_lost(self, db):
        """Le scénario que la reprise exécute vraiment.

        Les tables des comptages avancés manquent ; les quatre fichiers qui les
        façonnent repassent dans l'ordre. La 025 les repose telles qu'elle les
        connaît — avec `is_material`, la table des décisions d'étiquette, la
        référence portée par un journal, le numéro de ligne ERP et son index —
        puis la 029 et la 031 les ramènent à leur forme actuelle. Rejouer la 025
        seule décrirait un schéma qui n'existe nulle part : c'est la séquence qui
        est idempotente, pas chaque fichier pris isolément.
        """
        with db.transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS early_count_label_decision")
            conn.execute("DROP TABLE IF EXISTS early_count_drift")
            conn.execute("DROP TABLE IF EXISTS erp_journal_line")
            conn.execute("DROP TABLE IF EXISTS erp_journal_scope")
            conn.execute("DROP TABLE IF EXISTS erp_journal CASCADE")

        self._replay(
            db,
            "025_comptages_avances.sql",
            "026_le_journal_est_le_precomptage.sql",
            "029_une_seule_reference.sql",
            "031_ligne_erp_sans_numero.sql",
        )

        assert self._early_tables(db) == {
            "erp_journal", "erp_journal_scope", "erp_journal_line",
            "early_count_drift",
        }, "la table des décisions d'étiquette ne revient pas"

    def test_the_drift_comes_back_with_two_quantities_and_no_decision(self, db):
        """La 029 a bien le dernier mot sur la forme de la table."""
        with db.connection() as conn:
            columns = {
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'inventory' "
                    "AND table_name = 'early_count_drift'"
                ).fetchall()
            }
        assert "qty_counted_t0" in columns
        assert "qty_erp_j" in columns
        for gone in ("qty_erp_t0", "qty_physical_t0", "is_material", "resolution",
                     "cause_code", "resolved_at", "resolved_by"):
            assert gone not in columns, gone

    def test_the_book_stock_no_longer_points_at_a_journal(self, db):
        """La référence ne vient plus d'un précomptage : elle est unique."""
        with db.connection() as conn:
            columns = {
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'inventory' AND table_name = 'book_stock'"
                ).fetchall()
            }
        assert "erp_journal_id" not in columns
        assert "reference_date" in columns, "elle garde sa date, qui est celle du jour J"

    @pytest.mark.parametrize(
        "name", ["029_une_seule_reference.sql", "031_ligne_erp_sans_numero.sql"]
    )
    def test_the_new_files_replay_on_their_own(self, db, name):
        """Deux fois de suite, sur un schéma complet.

        C'est ce qu'un correctif d'empreinte ferait repasser, et c'est la seule
        forme de rejeu dont ces fichiers répondent : la 025 et la 026, elles,
        s'appuient chacune sur des colonnes qu'une suivante retire.
        """
        self._replay(db, name)
        self._replay(db, name)

        assert self._early_tables(db) == {
            "erp_journal", "erp_journal_scope", "erp_journal_line",
            "early_count_drift",
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
    """Ce qui identifie une ligne, ce sont les coordonnées de ce qu'elle compte.

    Le doublon est impossible plutôt que détecté après coup par un contrôle
    qu'on pourrait oublier de brancher. La clé est celle de la migration 031 :
    journal, site, entrepôt, emplacement, étiquette et article.
    """

    def test_the_same_coordinates_twice_are_refused(self, db, campaign):
        journal = _journal(db, campaign, "NPEM-000010")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, warehouse_id, "
                " location_id, label_id, item_number) "
                "VALUES (%s, %s, %s, 'ATP', 'SOL', '001609231', 'MASS-1')",
                (str(uuid.uuid4()), journal, campaign),
            )
        with pytest.raises(Exception) as caught, db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, warehouse_id, "
                " location_id, label_id, item_number) "
                "VALUES (%s, %s, %s, 'ATP', 'SOL', '001609231', 'MASS-1')",
                (str(uuid.uuid4()), journal, campaign),
            )
        assert "erp_journal_line_uq" in str(caught.value)

    def test_two_labels_at_the_same_place_are_two_lines(self, db, campaign):
        """Deux palettes du même article au même endroit restent deux lignes.

        C'est précisément ce que l'ancien numéro de ligne servait à départager,
        et l'étiquette le fait mieux : elle ne dépend d'aucun ordre d'export.
        """
        journal = _journal(db, campaign, "NPEM-000011")
        with db.transaction() as conn:
            for label in ("001609231", "001609232", "001609233"):
                conn.execute(
                    "INSERT INTO erp_journal_line "
                    "(id, erp_journal_id, campaign_id, warehouse_id, location_id, "
                    " label_id, item_number) "
                    "VALUES (%s, %s, %s, 'ATP', 'SOL', %s, 'MASS-1')",
                    (str(uuid.uuid4()), journal, campaign, label),
                )
        with db.connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM erp_journal_line WHERE erp_journal_id = %s",
                (journal,),
            ).fetchone()["n"]
        assert count == 3

    def test_a_journal_without_labels_keeps_one_line_per_item(self, db, campaign):
        """Un journal en quantité (INVV) ne porte pas d'étiquette.

        La clé y devient « une ligne par article et par emplacement », ce qui est
        exactement son grain : rien à excepter, donc aucune clause `WHERE` sur
        l'index.
        """
        journal = _journal(db, campaign, "NPEM-000012")
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

    def test_the_same_item_twice_without_a_label_is_refused(self, db, campaign):
        """Le revers de l'index sans clause `WHERE`, et il est voulu.

        Sur un journal en quantité, deux lignes pour le même article au même
        emplacement ne sont pas deux palettes : c'est un doublon d'extraction,
        et les additionner fausserait le comptage.
        """
        journal = _journal(db, campaign, "NPEM-000013")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, warehouse_id, location_id, item_number) "
                "VALUES (%s, %s, %s, 'ATP', 'SOL', 'MASS-1')",
                (str(uuid.uuid4()), journal, campaign),
            )
        with pytest.raises(Exception) as caught, db.transaction() as conn:
            conn.execute(
                "INSERT INTO erp_journal_line "
                "(id, erp_journal_id, campaign_id, warehouse_id, location_id, item_number) "
                "VALUES (%s, %s, %s, 'ATP', 'SOL', 'MASS-1')",
                (str(uuid.uuid4()), journal, campaign),
            )
        assert "erp_journal_line_uq" in str(caught.value)

    def test_the_line_number_column_is_gone(self, db):
        """Une colonne que plus rien n'écrit finit par être lue comme si elle voulait dire quelque chose."""
        with db.connection() as conn:
            columns = {
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'inventory' AND table_name = 'erp_journal_line'"
                ).fetchall()
            }
        assert "erp_line_number" not in columns


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
