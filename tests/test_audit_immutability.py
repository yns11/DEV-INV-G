"""La trace d'audit ne se réécrit pas, et ne se vide pas non plus.

`audit_event` porte deux règles depuis la migration 001 — pas d'`UPDATE`, pas de
`DELETE`. Le rapport d'audit externe soupçonnait qu'une cascade de clé étrangère
ou un `TRUNCATE` puisse les contourner. Vérifié contre PostgreSQL avant
d'écrire quoi que ce soit, le constat était à moitié juste :

* ``UPDATE`` direct : sans effet. La règle fait son travail.
* ``DELETE`` direct : sans effet. Idem.
* suppression physique de la campagne : **échouait déjà**, mais avec
  ``referential integrity query gave unexpected result``, un message qui ne dit
  rien à qui le lit à trois heures du matin. La migration 020 le remplace par un
  ``ON DELETE RESTRICT`` explicite.
* ``TRUNCATE`` : **vidait la table**. Un `TRUNCATE` ne passe pas par la
  réécriture de requête, donc aucune règle ne s'y applique. C'était le seul vrai
  trou, et un trigger `BEFORE TRUNCATE` le ferme.

**Ces contrôles ouvrent une vraie base.** Une doublure ne prouverait rien ici :
ce qui est en jeu est exactement le comportement du moteur, et c'est ce
comportement-là qui avait surpris. Sans PostgreSQL joignable, ils sont ignorés
plutôt que faussement verts — mais le contrôle de forme, lui, tourne toujours.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.postgres


def _database():
    """Une base réelle, ou la raison de ne pas essayer."""
    if not os.environ.get("PGHOST"):
        pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
    from inventory.config import get_settings
    from inventory.db.engine import Database

    get_settings.cache_clear()
    try:
        db = Database(get_settings())
        if not db.ping():
            pytest.skip("PostgreSQL injoignable")
    except Exception as exc:  # pragma: no cover - dépend de l'infrastructure
        pytest.skip(f"PostgreSQL injoignable : {exc}")
    return db


@pytest.fixture
def db():
    database = _database()
    from inventory.db.migrations import apply_all

    apply_all(database)
    return database


@pytest.fixture
def traced(db):
    """Une campagne et sa trace, toutes deux jetables… sauf que non.

    C'est tout le sujet : le nettoyage de ce test ne peut pas supprimer ce qu'il
    a créé, parce que c'est précisément ce que la base refuse désormais. Les
    identifiants sont donc uniques à chaque exécution.
    """
    campaign_id, event_id = str(uuid.uuid4()), str(uuid.uuid4())
    code = f"TEST-{campaign_id[:8]}"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO campaign (id, code, label, count_date, status, created_by) "
            "VALUES (%s,%s,'Contrôle','2026-09-01','PREPARATION','test')",
            (campaign_id, code),
        )
        cur.execute(
            "INSERT INTO audit_event (id, campaign_id, actor, action, entity_type, "
            "summary) VALUES (%s,%s,'test','CREATE','campaign','trace d''origine')",
            (event_id, campaign_id),
        )
    return db, campaign_id, event_id


def summary_of(db, event_id: str) -> str | None:
    with db.cursor() as cur:
        cur.execute("SELECT summary FROM audit_event WHERE id = %s", (event_id,))
        row = cur.fetchone()
    return None if row is None else row["summary"]


class TestNothingRewritesHistory:
    def test_an_update_leaves_the_summary_alone(self, traced):
        db, _, event_id = traced
        with db.cursor() as cur:
            cur.execute(
                "UPDATE audit_event SET summary = 'réécrit' WHERE id = %s",
                (event_id,),
            )
        assert summary_of(db, event_id) == "trace d'origine"

    def test_a_delete_leaves_the_row_alone(self, traced):
        db, _, event_id = traced
        with db.cursor() as cur:
            cur.execute("DELETE FROM audit_event WHERE id = %s", (event_id,))
        assert summary_of(db, event_id) is not None


class TestNothingEmptiesTheTable:
    def test_a_truncate_is_refused(self, traced):
        """Le seul chemin qui vidait réellement la table avant la migration 020."""
        import psycopg

        db, _, _ = traced
        with pytest.raises(psycopg.errors.RaiseException), db.cursor() as cur:
            cur.execute("TRUNCATE audit_event")

    def test_the_refusal_says_why(self, traced):
        import psycopg

        db, _, _ = traced
        with pytest.raises(psycopg.errors.RaiseException) as raised, db.cursor() as cur:
            cur.execute("TRUNCATE audit_event")
        assert "trace d'audit ne se vide pas" in str(raised.value)

    def test_the_rows_are_still_there_afterwards(self, traced):
        import psycopg

        db, _, event_id = traced
        with pytest.raises(psycopg.errors.Error), db.cursor() as cur:
            cur.execute("TRUNCATE audit_event")
        assert summary_of(db, event_id) == "trace d'origine"


class TestACampaignWithAHistoryCannotBeErased:
    """La suppression logique reste possible ; la suppression physique, non."""

    def test_a_hard_delete_of_the_campaign_is_refused(self, traced):
        import psycopg

        db, campaign_id, _ = traced
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db.cursor() as cur:
            cur.execute("DELETE FROM campaign WHERE id = %s", (campaign_id,))

    def test_the_trace_survives_the_attempt(self, traced):
        import psycopg

        db, campaign_id, event_id = traced
        with pytest.raises(psycopg.errors.Error), db.cursor() as cur:
            cur.execute("DELETE FROM campaign WHERE id = %s", (campaign_id,))
        assert summary_of(db, event_id) == "trace d'origine"

    def test_the_soft_delete_the_application_uses_still_works(self, traced):
        """L'application ne supprime que logiquement : rien ne doit la gêner."""
        db, campaign_id, _ = traced
        with db.cursor() as cur:
            cur.execute(
                "UPDATE campaign SET deleted_at = now() WHERE id = %s",
                (campaign_id,),
            )
            cur.execute(
                "SELECT deleted_at FROM campaign WHERE id = %s", (campaign_id,)
            )
            assert cur.fetchone()["deleted_at"] is not None


# --------------------------------------------------------------------------- #
# Sans base : ce que la migration déclare
# --------------------------------------------------------------------------- #

@pytest.mark.no_postgres
class TestWhatTheMigrationDeclares:
    """Un contrôle de forme, qui tourne partout.

    Il ne prouve rien du comportement du moteur — c'est le travail des contrôles
    ci-dessus, et ils exigent une vraie base. Il garde en revanche la migration
    de perdre l'une de ses deux moitiés au fil d'une réécriture.
    """

    def source(self) -> str:
        """Le SQL de la migration, commentaires retirés.

        Les commentaires y citent l'ancien `ON DELETE CASCADE` pour expliquer ce
        qui change ; les lire comme du SQL ferait échouer le contrôle sur sa
        propre explication.
        """
        from inventory.db.migrations import MIGRATIONS_DIR

        text = (MIGRATIONS_DIR / "020_audit_truncate_guard.sql").read_text()
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("--")
        )

    def test_the_truncate_trigger_is_declared(self):
        source = self.source()
        assert "BEFORE TRUNCATE ON audit_event" in source
        assert "FOR EACH STATEMENT" in source

    def test_the_foreign_key_restricts_instead_of_cascading(self):
        source = self.source()
        assert "ON DELETE RESTRICT" in source
        assert "ON DELETE CASCADE" not in source

    def test_the_original_rules_are_not_dropped(self):
        """Elles couvrent l'`UPDATE` et le `DELETE` directs, et restent utiles."""
        assert "DROP RULE" not in self.source()
