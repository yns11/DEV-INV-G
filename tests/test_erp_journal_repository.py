"""Le dépôt des journaux ERP, contre une vraie base.

Ce sont des requêtes : elles doivent s'exécuter, pas seulement se relire. Les
données de ces contrôles reprennent la forme de l'export du 13 juin 2026 — une
palette qui part d'un emplacement et arrive dans un autre, un journal qui couvre
plusieurs emplacements, des lignes de passage qui n'appartiennent au périmètre
de personne.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import JournalKind
from inventory.domain.models import ErpJournalLine, LocationKey

pytestmark = pytest.mark.postgres

BUFFER = LocationKey(warehouse_id="INV", location_id="01")


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_depot_journaux_erp") as database:
        yield database


@pytest.fixture
def repo(db):
    from inventory.db.repositories import ErpJournalRepository

    return ErpJournalRepository(db)


@pytest.fixture
def campaign(db):
    import uuid

    campaign_id = make_campaign(db, f"DEP-{uuid.uuid4().hex[:8]}")
    yield campaign_id
    with db.transaction() as conn:
        conn.execute("DELETE FROM audit_event WHERE campaign_id = %s", (campaign_id,))
        conn.execute("DELETE FROM campaign WHERE id = %s", (campaign_id,))


def _line(**kwargs) -> ErpJournalLine:
    base = {
        "id": "",
        "erp_journal_id": "",
        "campaign_id": "",
        "warehouse_id": "ATP",
        "location_id": "SOL",
        "item_number": "MASS-1",
        "qty_on_hand": 0,
        "qty_counted": 0,
    }
    return ErpJournalLine(**{**base, **kwargs})


class TestTheHeaderIsRefreshedNotDuplicated:
    """Le notebook est rejoué très régulièrement le jour J."""

    def test_a_second_import_updates_in_place(self, repo, campaign):
        first = repo.upsert_journal(
            campaign, journal_number="NPEM-1", kind=JournalKind.INVE,
            description="Inventaire par étiquette", erp_posted=False, line_count=3,
        )
        second = repo.upsert_journal(
            campaign, journal_number="NPEM-1", kind=JournalKind.INVE,
            description="Inventaire par étiquette", erp_posted=True, line_count=5,
        )
        assert first == second
        journals = repo.list(campaign)
        assert len(journals) == 1
        assert journals[0].erp_posted is True
        assert journals[0].line_count == 5

    def test_a_reimport_does_not_erase_the_declared_scope(self, repo, campaign):
        """Un réimport rafraîchit ce que l'ERP annonce, pas ce qu'un humain a décidé."""
        journal = repo.upsert_journal(
            campaign, journal_number="NPEM-2", kind=JournalKind.INVE
        )
        repo.set_scope(
            campaign, journal,
            [LocationKey(warehouse_id="ATP", location_id="SOL")],
            actor="alice",
        )
        repo.upsert_journal(
            campaign, journal_number="NPEM-2", kind=JournalKind.INVE, line_count=9
        )
        refreshed = repo.get_by_number(campaign, "NPEM-2")
        assert refreshed.scope_declared is True
        assert refreshed.scope_declared_by == "alice"
        assert refreshed.scope == [LocationKey(warehouse_id="ATP", location_id="SOL")]


class TestLinesAreReplacedByJournalNeverGlobally:
    """« Jamais d'addition de photographies, sauf pour les comptages avancés. »"""

    def test_reimporting_one_journal_leaves_the_others_alone(self, repo, campaign):
        early = repo.upsert_journal(
            campaign, journal_number="NPEM-AVANCE", kind=JournalKind.INVE
        )
        general = repo.upsert_journal(
            campaign, journal_number="NPEM-JOURJ", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, early, [_line(erp_line_number=1, qty_counted=7)])
        repo.replace_lines(campaign, general, [_line(erp_line_number=1, qty_counted=3)])

        # La photographie du jour J ne rapporte que le journal du jour J.
        repo.replace_lines(campaign, general, [_line(erp_line_number=1, qty_counted=4)])

        assert [l.qty_counted for l in repo.lines(campaign, early)] == [7]
        assert [l.qty_counted for l in repo.lines(campaign, general)] == [4]

    def test_replacing_with_nothing_empties_that_journal_only(self, repo, campaign):
        one = repo.upsert_journal(campaign, journal_number="NPEM-A", kind=JournalKind.INVV)
        two = repo.upsert_journal(campaign, journal_number="NPEM-B", kind=JournalKind.INVV)
        repo.replace_lines(campaign, one, [_line(erp_line_number=1)])
        repo.replace_lines(campaign, two, [_line(erp_line_number=1)])
        repo.replace_lines(campaign, one, [])
        assert repo.lines(campaign, one) == []
        assert len(repo.lines(campaign, two)) == 1


class TestTheCandidateLocations:
    """L'application propose, l'utilisateur tranche."""

    def _journal_with_lines(self, repo, campaign, number: str):
        journal = repo.upsert_journal(
            campaign, journal_number=number, kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, journal, [
            # Le périmètre probable : trois lignes au même endroit.
            _line(erp_line_number=1, warehouse_id="ATP", location_id="SOL",
                  qty_on_hand=1, qty_counted=1),
            _line(erp_line_number=2, warehouse_id="ATP", location_id="SOL",
                  qty_on_hand=1, qty_counted=0),
            _line(erp_line_number=3, warehouse_id="ATP", location_id="SOL",
                  qty_on_hand=2, qty_counted=2),
            # Une ligne de passage : la palette a été retrouvée ailleurs.
            _line(erp_line_number=4, warehouse_id="QUAL", location_id="APQP C0",
                  qty_on_hand=0, qty_counted=1),
            # Le tampon, qui n'est le périmètre de personne.
            _line(erp_line_number=5, warehouse_id="INV", location_id="01",
                  qty_on_hand=0, qty_counted=1),
        ])
        return journal

    def test_the_likely_perimeter_comes_first(self, repo, campaign):
        journal = self._journal_with_lines(repo, campaign, "NPEM-10")
        candidates = repo.candidate_locations(campaign, journal, buffer_key=BUFFER)
        assert (candidates[0]["warehouse_id"], candidates[0]["location_id"]) == (
            "ATP", "SOL"
        )
        assert candidates[0]["line_count"] == 3

    def test_the_buffer_is_never_proposed(self, repo, campaign):
        journal = self._journal_with_lines(repo, campaign, "NPEM-11")
        candidates = repo.candidate_locations(campaign, journal, buffer_key=BUFFER)
        assert all(c["warehouse_id"] != "INV" for c in candidates)

    def test_a_location_already_taken_is_not_proposed(self, repo, campaign):
        """Sinon deux journaux revendiqueraient le même emplacement, et la base
        refuserait la seconde déclaration sans que l'écran l'ait annoncé."""
        first = self._journal_with_lines(repo, campaign, "NPEM-12")
        repo.set_scope(
            campaign, first,
            [LocationKey(warehouse_id="QUAL", location_id="APQP C0")],
            actor="alice",
        )
        second = self._journal_with_lines(repo, campaign, "NPEM-13")
        candidates = repo.candidate_locations(campaign, second, buffer_key=BUFFER)
        assert all(c["location_id"] != "APQP C0" for c in candidates)
        assert any(c["location_id"] == "SOL" for c in candidates)


class TestTheReferenceComesFromTheJournal:
    """C'est ce qui rend un comptage avancé autonome."""

    def test_the_scope_decides_what_is_counted(self, repo, campaign):
        journal = repo.upsert_journal(
            campaign, journal_number="NPEM-20", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, journal, [
            _line(erp_line_number=1, location_id="SOL", qty_on_hand=1, qty_counted=1),
            _line(erp_line_number=2, location_id="SOL", qty_on_hand=1, qty_counted=0),
            # Ligne de passage : hors périmètre, elle ne doit rien produire.
            _line(erp_line_number=3, warehouse_id="QUAL", location_id="APQP C0",
                  qty_on_hand=0, qty_counted=1),
        ])
        repo.set_scope(
            campaign, journal,
            [LocationKey(warehouse_id="ATP", location_id="SOL")],
            actor="alice",
        )
        rows = repo.aggregate_in_scope(campaign)
        assert len(rows) == 1
        assert rows[0]["warehouse_id"] == "ATP"
        assert rows[0]["qty_on_hand"] == Decimal(2)
        assert rows[0]["qty_counted"] == Decimal(1)
        assert rows[0]["label_count"] == 2

    def test_an_undeclared_journal_produces_nothing(self, repo, campaign):
        """Tant que le périmètre n'est pas déclaré, rien n'est calculable —
        et surtout rien n'est deviné."""
        journal = repo.upsert_journal(
            campaign, journal_number="NPEM-21", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, journal, [
            _line(erp_line_number=1, qty_on_hand=5, qty_counted=5)
        ])
        assert repo.aggregate_in_scope(campaign) == []


class TestTheLabelControl:
    """Ce que la dérive ne voit pas, l'étiquette le rattrape."""

    def test_a_sealed_label_counted_in_another_journal_is_reported(
        self, repo, campaign
    ):
        sealed = repo.upsert_journal(
            campaign, journal_number="NPEM-AVANCE", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, sealed, [
            _line(erp_line_number=1, location_id="SOL",
                  label_id="001609231", qty_on_hand=1, qty_counted=1),
        ])
        # L'emplacement scellé appartient à ce journal : c'est ce qui fait de
        # sa ligne une preuve plutôt qu'un passage.
        repo.set_scope(
            campaign, sealed,
            [LocationKey(warehouse_id="ATP", location_id="SOL")], actor="test",
        )
        elsewhere = repo.upsert_journal(
            campaign, journal_number="NPEM-JOURJ", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, elsewhere, [
            _line(erp_line_number=1, warehouse_id="B06", location_id="STK P FI",
                  label_id="001609231", qty_on_hand=0, qty_counted=1),
        ])

        found = repo.labels_counted_elsewhere(
            campaign, [LocationKey(warehouse_id="ATP", location_id="SOL")]
        )
        assert len(found) == 1
        assert found[0]["label_id"] == "001609231"
        assert found[0]["sealed_location_id"] == "SOL"
        assert found[0]["other_location_id"] == "STK P FI"
        assert found[0]["other_journal_number"] == "NPEM-JOURJ"

    def test_the_same_label_inside_one_journal_is_not_reported(self, repo, campaign):
        """Une palette qui part d'un emplacement et arrive dans un autre, au sein
        du même journal, est le cas dominant de l'export : ce n'est pas une
        anomalie et ça n'a rien à voir avec un scellement."""
        journal = repo.upsert_journal(
            campaign, journal_number="NPEM-30", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, journal, [
            _line(erp_line_number=1, location_id="SOL",
                  label_id="001609231", qty_on_hand=1, qty_counted=0),
            _line(erp_line_number=2, warehouse_id="QUAL", location_id="APQP C0",
                  label_id="001609231", qty_on_hand=0, qty_counted=1),
        ])
        repo.set_scope(
            campaign, journal,
            [LocationKey(warehouse_id="ATP", location_id="SOL")], actor="test",
        )
        found = repo.labels_counted_elsewhere(
            campaign, [LocationKey(warehouse_id="ATP", location_id="SOL")]
        )
        assert found == []

    def test_a_label_left_uncounted_on_the_sealed_side_is_not_reported(
        self, repo, campaign
    ):
        """Le contrôle porte sur ce qui a été *compté* au précomptage.

        Une étiquette que le journal scellé mentionne sans la compter — une
        ligne de départ — n'établit rien : elle dit justement que la pièce
        n'était pas là.
        """
        sealed = repo.upsert_journal(
            campaign, journal_number="NPEM-31", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, sealed, [
            _line(erp_line_number=1, location_id="SOL",
                  label_id="001609231", qty_on_hand=1, qty_counted=0),
        ])
        repo.set_scope(
            campaign, sealed,
            [LocationKey(warehouse_id="ATP", location_id="SOL")], actor="test",
        )
        other = repo.upsert_journal(
            campaign, journal_number="NPEM-32", kind=JournalKind.INVE
        )
        repo.replace_lines(campaign, other, [
            _line(erp_line_number=1, warehouse_id="B06", location_id="STK P FI",
                  label_id="001609231", qty_on_hand=0, qty_counted=1),
        ])
        found = repo.labels_counted_elsewhere(
            campaign, [LocationKey(warehouse_id="ATP", location_id="SOL")]
        )
        assert found == []

    def test_nothing_sealed_means_nothing_to_report(self, repo, campaign):
        assert repo.labels_counted_elsewhere(campaign, []) == []


class TestSealing:
    def test_sealing_marks_the_journals_of_the_batch(self, db, campaign):
        from inventory.db.repositories import JournalRepository

        journals = JournalRepository(db)
        journals.ensure_journals(campaign, [
            LocationKey(warehouse_id="ATP", location_id="SOL"),
            LocationKey(warehouse_id="ATP", location_id="STK P FI"),
            LocationKey(warehouse_id="B06", location_id="AUTRE"),
        ])
        touched = journals.seal(
            campaign, [("ATP", "SOL"), ("ATP", "STK P FI")],
            actor="alice",
        )
        assert touched == 2
        assert journals.sealed_keys(campaign) == {("ATP", "SOL"), ("ATP", "STK P FI")}

    def test_unsealing_gives_the_location_back(self, db, campaign):
        from inventory.db.repositories import JournalRepository

        journals = JournalRepository(db)
        journals.ensure_journals(
            campaign, [LocationKey(warehouse_id="ATP", location_id="SOL")]
        )
        journals.seal(campaign, [("ATP", "SOL")], actor="alice")
        journals.unseal(campaign, [("ATP", "SOL")], actor="bob")
        assert journals.sealed_keys(campaign) == set()

    def test_a_journal_of_another_campaign_is_not_touched(self, db, campaign):
        """La permission se vérifie sur la campagne de l'URL, les identifiants
        arrivent dans le corps : le filtre n'est pas une ceinture de plus."""
        import uuid

        from inventory.db.repositories import JournalRepository

        other = make_campaign(db, f"AUT-{uuid.uuid4().hex[:8]}")
        journals = JournalRepository(db)
        try:
            for campaign_id in (campaign, other):
                journals.ensure_journals(
                    campaign_id, [LocationKey(warehouse_id="ATP", location_id="SOL")]
                )
            journals.seal(campaign, [("ATP", "SOL")], actor="alice")
            assert journals.sealed_keys(other) == set()
        finally:
            with db.transaction() as conn:
                conn.execute("DELETE FROM campaign WHERE id = %s", (other,))
