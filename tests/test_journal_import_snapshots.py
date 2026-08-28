"""L'import des journaux, de bout en bout et contre une vraie base.

Quatre règles s'y jouent, toutes tirées de la note métier sur les journaux de
comptage :

* **une ligne par étiquette côté ERP, une ligne par article et emplacement côté
  application** — c'est le grain sur lequel écarts, consolidation et écrans sont
  écrits, et un journal INVE poserait sinon des dizaines de milliers de lignes ;
* **le remplacement se fait par journal**, jamais globalement, ce qui est
  exactement ce qui laisse survivre les journaux d'un lot avancé quand la
  photographie du jour J ne les contient pas ;
* **un emplacement scellé ne se recharge pas** : son comptage est une preuve
  datée, et le remplacer effacerait la dérive qu'on cherche à mesurer ;
* **toutes les lignes sont conservées**, tampon et hors périmètre compris, parce
  que c'est la trace et parce que le contrôle par étiquette les relit.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.models import Campaign, LocationKey

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_import_journaux") as database:
        yield database


@pytest.fixture
def campaign(db):
    from inventory.domain.enums import CampaignStatus

    campaign_id = make_campaign(db, f"IMP-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'COUNTING' WHERE id = %s", (campaign_id,)
        )
    import datetime as dt

    yield Campaign(
        id=campaign_id,
        code=f"IMP-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 6, 13),
        status=CampaignStatus.COUNTING,
        created_by="test",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )
    # Pas de nettoyage : le journal d'audit est en ajout seul, et la clé
    # étrangère qui protège la trace refuse de laisser partir une campagne qui
    # en a une. C'est le comportement voulu — la base entière est jetable, elle
    # part au démontage du module.


@pytest.fixture
def service(db, monkeypatch):
    """Le vrai service, sur la vraie base, avec la lecture du fichier dictée."""
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext
    from inventory.services.import_service import ImportService

    ctx = ServiceContext(actor="chef@usine", db=db, settings=get_settings())
    # La garde de phase et le rôle passent par la campagne en base ; ici on
    # teste l'import, pas les permissions, qui ont leurs propres contrôles.
    monkeypatch.setattr(ctx, "guard", lambda campaign, what: None, raising=False)
    built = ImportService(ctx)
    built.batches.archive = lambda *a, **k: None  # type: ignore[method-assign]
    return built


def _feed(service, monkeypatch, rows: list[dict[str, Any]]) -> None:
    from inventory.ingest import ParseResult

    monkeypatch.setattr(
        service.parser, "parse",
        lambda contract, **kw: (
            None,
            ParseResult(contract_key=contract, rows=rows, rows_received=len(rows)),
        ),
    )


def _row(**kwargs) -> dict[str, Any]:
    base = {
        "journal_number": "NPEM-1",
        "erp_line_number": 1,
        "warehouse_id": "ATP",
        "location_id": "SOL",
        "item_number": "MASS-1",
        "counted_quantity": 1,
        "qty_on_hand": 1,
        "journal_name_id": "INVE",
        "is_posted": False,
        "unit": "PCE",
    }
    return {**base, **kwargs}


class TestOneLinePerArticleAndLocation:
    def test_ten_labels_become_one_counted_line(self, service, campaign, monkeypatch):
        _feed(service, monkeypatch, [
            _row(erp_line_number=n, label_id=f"0016092{n:02d}",
                 counted_quantity=1, qty_on_hand=1)
            for n in range(1, 11)
        ])
        outcome = service.import_journal_lines(campaign, payload=b"x", filename="j.csv")

        assert outcome.rows_accepted == 1, "dix étiquettes, un article, un emplacement"
        lines = service.ctx.journals.lines_by_journal(campaign.id)
        counted = [line for group in lines.values() for line in group]
        assert len(counted) == 1
        assert counted[0].qty == Decimal(10)
        assert counted[0].qty_on_hand == Decimal(10)
        assert counted[0].label_count == 10
        assert counted[0].erp_journal_number == "NPEM-1"

    def test_two_articles_stay_two_lines(self, service, campaign, monkeypatch):
        _feed(service, monkeypatch, [
            _row(erp_line_number=1, item_number="MASS-1"),
            _row(erp_line_number=2, item_number="MASS-2"),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        counted = [
            line
            for group in service.ctx.journals.lines_by_journal(campaign.id).values()
            for line in group
        ]
        assert sorted(l.item_number for l in counted) == ["MASS-1", "MASS-2"]

    def test_two_locations_stay_two_lines(self, service, campaign, monkeypatch):
        _feed(service, monkeypatch, [
            _row(erp_line_number=1, location_id="SOL"),
            _row(erp_line_number=2, location_id="STK P FI"),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        journals = service.ctx.journals.list(campaign.id)
        assert {j.location_id for j in journals} == {"SOL", "STK P FI"}


class TestEveryRawLineIsKept:
    def test_the_erp_journal_and_its_lines_are_stored(
        self, service, campaign, monkeypatch
    ):
        _feed(service, monkeypatch, [
            _row(erp_line_number=1, label_id="001609231", counted_quantity=0,
                 qty_on_hand=1),
            _row(erp_line_number=2, warehouse_id="QUAL", location_id="APQP C0",
                 label_id="001609231", counted_quantity=1, qty_on_hand=0),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")

        journals = service.ctx.erp_journals.list(campaign.id)
        assert [j.journal_number for j in journals] == ["NPEM-1"]
        raw = service.ctx.erp_journals.lines(campaign.id, journals[0].id)
        assert len(raw) == 2
        assert {line.label_id for line in raw} == {"001609231"}

    def test_the_buffer_lines_are_kept_too(self, service, campaign, monkeypatch):
        """« Les lignes doivent néanmoins être importées et conservées. »"""
        _feed(service, monkeypatch, [
            _row(erp_line_number=1),
            _row(erp_line_number=2, warehouse_id="INV", location_id="01",
                 counted_quantity=5, qty_on_hand=0),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        journals = service.ctx.erp_journals.list(campaign.id)
        raw = service.ctx.erp_journals.lines(campaign.id, journals[0].id)
        assert any(line.warehouse_id == "INV" for line in raw)

    def test_the_import_time_is_stamped(self, service, campaign, monkeypatch):
        """Le notebook est rejoué très régulièrement : de quand datent ces chiffres ?"""
        _feed(service, monkeypatch, [_row()])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        refreshed = service.ctx.campaigns.get(campaign.id)
        assert refreshed.journals_imported_at is not None


class TestAReplacementIsPerJournal:
    def test_a_journal_absent_from_the_snapshot_keeps_its_lines(
        self, service, campaign, monkeypatch
    ):
        """C'est exactement ce qui fait survivre un lot avancé au jour J.

        La photographie du jour J est filtrée sur sa fenêtre de dates : elle ne
        rapporte pas les journaux de J-2. Un remplacement global les effacerait.
        """
        _feed(service, monkeypatch, [
            _row(journal_number="NPEM-AVANCE", erp_line_number=1, counted_quantity=7),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="j2.csv")

        _feed(service, monkeypatch, [
            _row(journal_number="NPEM-JOURJ", erp_line_number=1,
                 location_id="STK P FI", counted_quantity=3),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="jj.csv")

        numbers = {j.journal_number: j for j in service.ctx.erp_journals.list(campaign.id)}
        assert set(numbers) == {"NPEM-AVANCE", "NPEM-JOURJ"}
        kept = service.ctx.erp_journals.lines(campaign.id, numbers["NPEM-AVANCE"].id)
        assert [line.qty_counted for line in kept] == [Decimal(7)]

    def test_reimporting_the_same_journal_refreshes_it(
        self, service, campaign, monkeypatch
    ):
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=3)])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=8)])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")

        journals = service.ctx.erp_journals.list(campaign.id)
        raw = service.ctx.erp_journals.lines(campaign.id, journals[0].id)
        assert [line.qty_counted for line in raw] == [Decimal(8)]


class TestASealedLocationIsNotReloaded:
    def test_its_counted_lines_survive_the_import(
        self, service, campaign, monkeypatch
    ):
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=7)])
        service.import_journal_lines(campaign, payload=b"x", filename="j2.csv")
        service.ctx.journals.seal(
            campaign.id, [("ATP", "SOL")], actor="alice"
        )

        # Le jour J, la même référence est comptée autrement.
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=2)])
        outcome = service.import_journal_lines(campaign, payload=b"x", filename="jj.csv")

        counted = [
            line
            for group in service.ctx.journals.lines_by_journal(campaign.id).values()
            for line in group
        ]
        assert [line.qty for line in counted] == [Decimal(7)], (
            "le comptage avancé fait foi ; le recharger effacerait la dérive"
        )
        assert any("scellé" in w.message for w in outcome.warnings)
        assert outcome.details["sealedLocationsKept"] == ["ATP / SOL"]

    def test_its_raw_lines_are_still_recorded(self, service, campaign, monkeypatch):
        """Sans quoi le contrôle par étiquette n'aurait rien à rapprocher."""
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=7)])
        service.import_journal_lines(campaign, payload=b"x", filename="j2.csv")
        service.ctx.journals.seal(
            campaign.id, [("ATP", "SOL")], actor="alice"
        )
        _feed(service, monkeypatch, [
            _row(journal_number="NPEM-JOURJ", erp_line_number=1,
                 label_id="001609231", counted_quantity=2),
        ])
        service.import_journal_lines(campaign, payload=b"x", filename="jj.csv")

        numbers = {j.journal_number: j for j in service.ctx.erp_journals.list(campaign.id)}
        raw = service.ctx.erp_journals.lines(campaign.id, numbers["NPEM-JOURJ"].id)
        assert [line.label_id for line in raw] == ["001609231"]

    def test_an_unsealed_location_is_reloaded_normally(
        self, service, campaign, monkeypatch
    ):
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=7)])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        _feed(service, monkeypatch, [_row(erp_line_number=1, counted_quantity=2)])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        counted = [
            line
            for group in service.ctx.journals.lines_by_journal(campaign.id).values()
            for line in group
        ]
        assert [line.qty for line in counted] == [Decimal(2)]


class TestTheReportNamesWhatIsUndeclared:
    def test_a_journal_without_a_scope_is_listed(self, service, campaign, monkeypatch):
        _feed(service, monkeypatch, [_row()])
        outcome = service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        assert outcome.details["scopeUndeclared"] == ["NPEM-1"]

    def test_a_declared_journal_leaves_the_list(self, service, campaign, monkeypatch):
        _feed(service, monkeypatch, [_row()])
        service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        journal = service.ctx.erp_journals.get_by_number(campaign.id, "NPEM-1")
        service.ctx.erp_journals.set_scope(
            campaign.id, journal.id,
            [LocationKey(warehouse_id="ATP", location_id="SOL")],
            actor="alice",
        )
        _feed(service, monkeypatch, [_row()])
        outcome = service.import_journal_lines(campaign, payload=b"x", filename="j.csv")
        assert outcome.details["scopeUndeclared"] == []
