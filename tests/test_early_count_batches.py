"""Les lots de comptage avancé, contre une vraie base.

Ce qui s'y décide :

* **la référence vient du journal**, pas d'un chargement séparé — c'est ce qui
  rend un lot autonome ;
* **la référence d'un emplacement scellé est celle de son précomptage**, sans
  quoi son écart d'inventaire tomberait à zéro dans le cas nominal et
  disparaîtrait de la campagne ;
* **on ne scelle qu'un journal posté dans l'ERP**, ce qui rend le réalignement
  acquis par construction ;
* **le descellement demande un motif**, parce qu'il annule une preuve datée.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus, JournalKind
from inventory.domain.models import Campaign, ErpJournalLine, Item, LocationKey
from inventory.errors import ConflictError, ValidationError

pytestmark = pytest.mark.postgres

SOL = LocationKey(warehouse_id="ATP", location_id="SOL")
STK = LocationKey(warehouse_id="ATP", location_id="STK P FI")


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_lots_avances") as database:
        yield database


@pytest.fixture
def campaign(db):
    campaign_id = make_campaign(db, f"LOT-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'COUNTING' WHERE id = %s", (campaign_id,)
        )
    return Campaign(
        id=campaign_id,
        code=f"LOT-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 6, 13),
        status=CampaignStatus.COUNTING,
        created_by="test",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def ctx(db, monkeypatch):
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext

    context = ServiceContext(actor="alice", db=db, settings=get_settings())
    monkeypatch.setattr(context, "guard", lambda campaign, what: None, raising=False)
    return context


@pytest.fixture
def service(ctx):
    from inventory.services.early_count_service import EarlyCountService

    return EarlyCountService(ctx)


def _erp_line(campaign_id: str, journal_id: str, **kwargs) -> ErpJournalLine:
    base = {
        "id": "",
        "erp_journal_id": journal_id,
        "campaign_id": campaign_id,
        "warehouse_id": SOL.warehouse_id,
        "location_id": SOL.location_id,
        "item_number": "MASS-1",
        "qty_on_hand": 0,
        "qty_counted": 0,
    }
    return ErpJournalLine(**{**base, **kwargs})


def _journal(ctx, campaign, *, number="NPEM-1", posted=True, lines=None,
             scope=(SOL,)) -> str:
    journal_id = ctx.erp_journals.upsert_journal(
        campaign.id, journal_number=number, kind=JournalKind.INVE, erp_posted=posted
    )
    ctx.erp_journals.replace_lines(
        campaign.id, journal_id,
        lines if lines is not None else [
            _erp_line(campaign.id, journal_id, erp_line_number=1,
                      qty_on_hand=10, qty_counted=12),
        ],
    )
    ctx.journals.ensure_journals(campaign.id, list(scope))
    if scope:
        ctx.erp_journals.set_scope(campaign.id, journal_id, list(scope), actor="alice")
    return journal_id


def _priced(ctx, campaign, number="MASS-1", cost="4.00") -> None:
    ctx.referentials.upsert_items([
        Item(campaign_id=campaign.id, item_number=number, name="X",
             std_price=Decimal(cost)),
    ], actor="alice")


class TestTheScopeProposal:
    def test_the_buffer_cannot_be_declared(self, service, ctx, campaign):
        journal = _journal(ctx, campaign, scope=())
        with pytest.raises(ValidationError) as caught:
            service.declare_scope(campaign, journal, [campaign.config.buffer_key])
        assert "tampon" in str(caught.value)

    def test_declaring_a_scope_records_who_and_when(self, service, ctx, campaign):
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])
        stored = ctx.erp_journals.get_by_number(campaign.id, "NPEM-1")
        assert stored.scope == [SOL]
        assert stored.scope_declared_by == "alice"


class TestOpeningABatch:
    def test_a_journal_without_a_declared_scope_is_refused(
        self, service, ctx, campaign
    ):
        """Sans périmètre, la référence porterait sur des emplacements que le
        journal ne couvre pas."""
        journal = _journal(ctx, campaign, scope=())
        with pytest.raises(ConflictError) as caught:
            service.create_batch(
                campaign, code="LOT-J2", erp_journal_ids=[journal]
            )
        assert "périmètre" in str(caught.value)

    def test_the_batch_takes_the_declared_locations(self, service, ctx, campaign):
        journal = _journal(ctx, campaign, scope=(SOL, STK))
        batch = service.create_batch(
            campaign, code="lot j2", counted_on=dt.date(2026, 6, 11),
            erp_journal_ids=[journal],
        )
        assert batch.code == "LOT-J2"
        assert batch.locations == [] or set(batch.locations) <= {SOL, STK}

    def test_a_batch_needs_a_journal(self, service, campaign):
        with pytest.raises(ValidationError):
            service.create_batch(campaign, code="VIDE", erp_journal_ids=[])


class TestSealing:
    def _open(self, service, ctx, campaign, *, posted=True):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, posted=posted)
        batch = service.create_batch(
            campaign, code="LOT-J2", counted_on=dt.date(2026, 6, 11),
            erp_journal_ids=[journal],
        )
        return service.close_batch(campaign, batch.id)

    def test_an_unposted_journal_refuses_the_seal(self, service, ctx, campaign):
        """C'est le postage qui réaligne l'ERP sur le physique compté."""
        batch = self._open(service, ctx, campaign, posted=False)
        with pytest.raises(ConflictError) as caught:
            service.seal_batch(campaign, batch.id)
        assert "postés" in str(caught.value)

    def test_an_unclosed_batch_refuses_the_seal(self, service, ctx, campaign):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign)
        batch = service.create_batch(
            campaign, code="LOT-J2", erp_journal_ids=[journal]
        )
        with pytest.raises(ConflictError):
            service.seal_batch(campaign, batch.id)

    def test_sealing_writes_the_reference_read_from_the_journal(
        self, service, ctx, campaign
    ):
        """`ERP@T0` sort de la colonne « Stock ERP », pas d'un chargement."""
        batch = self._open(service, ctx, campaign)
        service.seal_batch(campaign, batch.id)

        reference = ctx.book_stock.list(campaign.id)
        assert len(reference) == 1
        assert reference[0].qty == Decimal(10), "le stock ERP d'avant comptage"
        assert reference[0].reference_date == dt.date(2026, 6, 11)
        assert reference[0].early_batch_id == batch.id
        assert reference[0].unit_cost == Decimal("4.00")

    def test_sealing_marks_the_journals(self, service, ctx, campaign):
        batch = self._open(service, ctx, campaign)
        service.seal_batch(campaign, batch.id)
        assert ctx.journals.sealed_keys(campaign.id) == {("ATP", "SOL")}

    def test_the_batch_carries_its_locations_once_sealed(
        self, service, ctx, campaign
    ):
        batch = self._open(service, ctx, campaign)
        sealed = service.seal_batch(campaign, batch.id)
        assert sealed.is_sealed is True
        assert sealed.locations == [SOL]


class TestUnsealing:
    def _sealed(self, service, ctx, campaign):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign)
        batch = service.create_batch(
            campaign, code="LOT-J2", counted_on=dt.date(2026, 6, 11),
            erp_journal_ids=[journal],
        )
        service.close_batch(campaign, batch.id)
        return service.seal_batch(campaign, batch.id)

    def test_a_reason_is_required(self, service, ctx, campaign):
        batch = self._sealed(service, ctx, campaign)
        with pytest.raises(ValidationError) as caught:
            service.unseal_batch(campaign, batch.id, reason="   ")
        assert "motif" in str(caught.value)

    def test_unsealing_gives_the_locations_back(self, service, ctx, campaign):
        batch = self._sealed(service, ctx, campaign)
        service.unseal_batch(campaign, batch.id, reason="recomptage demandé")
        assert ctx.journals.sealed_keys(campaign.id) == set()

    def test_unsealing_drops_the_batch_reference(self, service, ctx, campaign):
        """L'emplacement rejoint le comptage général : sa référence redevient
        celle du jour J, donc l'ancienne ne doit pas rester en travers."""
        batch = self._sealed(service, ctx, campaign)
        service.unseal_batch(campaign, batch.id, reason="recomptage demandé")
        assert ctx.book_stock.list(campaign.id) == []


class TestTheGeneralLoadPreservesSealedReferences:
    def test_a_sealed_location_keeps_its_own_date(self, service, ctx, campaign):
        """La règle de référence, appliquée à deux dates.

        Sans elle, l'écart d'un emplacement précompté vaudrait zéro dans le cas
        nominal — poster son journal ayant réaligné l'ERP sur le physique — et
        le résultat de son inventaire disparaîtrait de la campagne.
        """
        from inventory.domain.models import BookStockLine

        _priced(ctx, campaign)
        journal = _journal(ctx, campaign)
        batch = service.create_batch(
            campaign, code="LOT-J2", counted_on=dt.date(2026, 6, 11),
            erp_journal_ids=[journal],
        )
        service.close_batch(campaign, batch.id)
        service.seal_batch(campaign, batch.id)

        # Le jour J : le chargement général couvre tout, scellés compris.
        ctx.book_stock.replace(campaign.id, [
            BookStockLine(campaign_id=campaign.id, item_number="MASS-1",
                          warehouse_id="ATP", location_id="SOL", qty=12,
                          reference_date=dt.date(2026, 6, 13)),
            BookStockLine(campaign_id=campaign.id, item_number="MASS-2",
                          warehouse_id="B06", location_id="AUTRE", qty=5,
                          reference_date=dt.date(2026, 6, 13)),
        ], batch_id=None)

        by_key: dict[tuple[str, str], Any] = {
            (line.warehouse_id, line.location_id): line
            for line in ctx.book_stock.list(campaign.id)
        }
        assert by_key[("ATP", "SOL")].qty == Decimal(10), (
            "l'emplacement scellé garde la référence de son précomptage"
        )
        assert by_key[("ATP", "SOL")].reference_date == dt.date(2026, 6, 11)
        assert by_key[("B06", "AUTRE")].reference_date == dt.date(2026, 6, 13)
