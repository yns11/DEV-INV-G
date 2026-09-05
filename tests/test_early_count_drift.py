"""La dérive d'un emplacement scellé, et ses deux issues.

Ce qui s'y décide :

* la dérive se mesure contre le **physique** de T0, pas contre la référence —
  la mesurer contre `ERP@T0` reviendrait à recopier l'écart d'inventaire et à
  crier au scellement rompu sur chaque emplacement qui a un écart ;
* le rapprochement est une **jointure externe complète** : un article apparu
  dans le stock du jour J sans jamais avoir été compté, ou disparu, sont les
  deux cas qu'une jointure interne perdrait ;
* un recalcul **conserve les issues déjà données**, parce que le notebook est
  rejoué toutes les quelques minutes le jour J ;
* une dérive matérielle sans issue **bloque le passage en analyse**.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import (
    CampaignStatus,
    DriftResolution,
    ItemType,
    JournalKind,
    JournalStatus,
)
from inventory.domain.models import (
    BookStockLine,
    Campaign,
    ErpJournalLine,
    Item,
    LocationKey,
    Thresholds,
)
from inventory.errors import ValidationError

pytestmark = pytest.mark.postgres

SOL = LocationKey(warehouse_id="ATP", location_id="SOL")


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_derives") as database:
        yield database


@pytest.fixture
def campaign(db):
    campaign_id = make_campaign(db, f"DER-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'COUNTING' WHERE id = %s", (campaign_id,)
        )
    return Campaign(
        id=campaign_id,
        code=f"DER-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 6, 13),
        status=CampaignStatus.COUNTING,
        created_by="test",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        # Un euro de seuil : ces contrôles portent sur la mécanique, pas sur le
        # réglage. Les seuils de campagne restent la seule source de matérialité.
        thresholds=[Thresholds(item_type=ItemType.UNKNOWN, value_abs_eur=1)],
    )


@pytest.fixture
def ctx(db, monkeypatch):
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext

    context = ServiceContext(actor="alice", db=db, settings=get_settings())
    monkeypatch.setattr(context, "guard", lambda campaign, what: None, raising=False)
    return context


@pytest.fixture
def drift(ctx):
    from inventory.services.drift_service import DriftService

    return DriftService(ctx)


def _sealed_location(ctx, campaign, *, counted: int, reference: int,
                     item="MASS-1", cost="10.00") -> None:
    """Un emplacement précompté puis scellé, avec sa référence et son comptage."""
    from inventory.services.early_count_service import EarlyCountService

    ctx.referentials.upsert_items([
        Item(campaign_id=campaign.id, item_number=item, name="X",
             std_price=Decimal(cost)),
    ], actor="alice")
    journal_id = ctx.erp_journals.upsert_journal(
        campaign.id, journal_number="NPEM-AVANCE", kind=JournalKind.INVE,
        erp_posted=True,
    )
    ctx.erp_journals.replace_lines(campaign.id, journal_id, [
        ErpJournalLine(
            id="", erp_journal_id=journal_id, campaign_id=campaign.id,
            erp_line_number=1, warehouse_id=SOL.warehouse_id,
            location_id=SOL.location_id, item_number=item,
            qty_on_hand=reference, qty_counted=counted,
        ),
    ])
    ctx.journals.ensure_journals(campaign.id, [SOL])
    ctx.erp_journals.set_scope(campaign.id, journal_id, [SOL], actor="alice")

    # Le comptage côté application, au grain emplacement + article.
    from inventory.db import new_id
    from inventory.domain.models import CountJournalLine

    journal = next(j for j in ctx.journals.list(campaign.id) if j.key == SOL)
    ctx.journals.replace_imported_lines(campaign.id, [journal.id], [
        CountJournalLine(
            id=new_id(), journal_id=journal.id, campaign_id=campaign.id,
            item_number=item, qty_imported=counted, qty_on_hand=reference,
        ),
    ])
    ctx.journals.set_status(
        campaign.id, [journal.id], JournalStatus.POSTED, actor="alice"
    )

    # Déclarer le périmètre scelle : un seul geste, et il pose la référence.
    EarlyCountService(ctx).declare_scope(campaign, journal_id, [SOL])


def _day_j(campaign, qty: int, item="MASS-1") -> list[BookStockLine]:
    return [
        BookStockLine(
            campaign_id=campaign.id, item_number=item,
            warehouse_id=SOL.warehouse_id, location_id=SOL.location_id,
            qty=qty, unit_cost=Decimal("10.00"),
            reference_date=dt.date(2026, 6, 13),
        )
    ]


class TestTheNominalCase:
    """La dérive nulle est calculée, conservée — et **pas affichée**.

    Ces deux contrôles lisaient le dépôt à travers la vue, et la vue ne montre
    plus que ce qui a dérivé : une ligne à zéro est le cas normal, donc
    l'absence d'information, et sur un précomptage de cinquante emplacements à
    trois cents références elle enterrait les quelques lignes à trancher.

    Ce qu'ils vérifient reste entier — la confrontation a bien eu lieu sur cette
    ligne et n'a rien trouvé — mais là où la trace vit.
    """

    def test_the_barrier_held_and_the_drift_is_null(self, drift, ctx, campaign):
        """`ERP@J` vaut le physique posté : c'est ce qu'on attend."""
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 12))

        stored = ctx.drifts.list(campaign.id)
        assert len(stored) == 1
        assert stored[0].drift_qty == 0
        assert stored[0].is_material is False
        assert stored[0].blocks_analysis is False

    def test_it_is_not_shown(self, drift, ctx, campaign):
        """« N'affiche que les lignes où la dérive n'est pas nulle. »"""
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 12))
        assert drift.list_drifts(campaign.id) == []

    def test_the_inventory_variance_is_not_a_drift(self, drift, ctx, campaign):
        """Contre `ERP@T0`, cette dérive vaudrait 2 — l'écart d'inventaire.

        Les confondre ferait crier au scellement rompu sur chaque emplacement
        qui a un écart, c'est-à-dire sur ceux qui méritent d'être regardés pour
        une tout autre raison.
        """
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 12))

        line = ctx.drifts.list(campaign.id)[0]
        assert line.qty_erp_t0 == 10
        assert line.qty_physical_t0 == 12
        assert line.qty_erp_j == 12
        assert line.drift_qty == 0


class TestAMovementAfterSealing:
    def test_it_shows_up_and_blocks(self, drift, ctx, campaign):
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))

        line = drift.list_drifts(campaign.id)[0]
        assert line.drift_qty == Decimal(-3)
        assert line.drift_value == Decimal("-30.00")
        assert line.is_material is True
        assert line.blocks_analysis is True

    def test_an_article_that_appeared_is_seen(self, drift, ctx, campaign):
        """Jamais compté, pourtant présent dans le stock du jour J.

        C'est l'un des deux cas qu'une jointure interne perdrait.
        """
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(
            campaign, _day_j(campaign, 12) + _day_j(campaign, 4, item="MASS-2")
        )
        by_item = {line.item_number: line for line in drift.list_drifts(campaign.id)}
        assert by_item["MASS-2"].qty_physical_t0 == 0
        assert by_item["MASS-2"].qty_erp_j == 4

    def test_an_article_that_vanished_is_seen(self, drift, ctx, campaign):
        """L'autre cas : compté à T0, absent du stock du jour J."""
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, [])
        line = drift.list_drifts(campaign.id)[0]
        assert line.qty_erp_j == 0
        assert line.drift_qty == Decimal(-12)


class TestRecomputingKeepsTheDecisions:
    def test_a_resolution_survives_the_next_import(self, drift, ctx, campaign):
        """Le notebook est rejoué toutes les quelques minutes le jour J.

        Repartir de zéro ferait qu'un exploitant tranche une dérive à neuf
        heures et la retrouve vierge à neuf heures cinq, sans que rien ne le
        dise.
        """
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))
        line = drift.list_drifts(campaign.id)[0]
        drift.resolve(
            campaign, [line.id], DriftResolution.KEEP_EARLY,
            cause_code="MOUVEMENT_APRES_SCELLEMENT", comment="régularisation",
        )

        drift.record_general_load(campaign, _day_j(campaign, 9))
        again = drift.list_drifts(campaign.id)[0]
        assert again.resolution is DriftResolution.KEEP_EARLY
        assert again.cause_code == "MOUVEMENT_APRES_SCELLEMENT"
        assert again.resolved_by == "alice"

    def test_the_quantities_are_refreshed_all_the_same(self, drift, ctx, campaign):
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))
        line = drift.list_drifts(campaign.id)[0]
        drift.resolve(campaign, [line.id], DriftResolution.RECOUNT)

        drift.record_general_load(campaign, _day_j(campaign, 7))
        again = drift.list_drifts(campaign.id)[0]
        assert again.qty_erp_j == 7
        assert again.resolution is DriftResolution.RECOUNT


class TestTheTwoResolutions:
    def test_keeping_the_early_count_demands_a_cause(self, drift, ctx, campaign):
        """Cette issue laisse la campagne et l'ERP en désaccord : on la nomme."""
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))
        line = drift.list_drifts(campaign.id)[0]
        with pytest.raises(ValidationError) as caught:
            drift.resolve(campaign, [line.id], DriftResolution.KEEP_EARLY)
        assert "cause" in str(caught.value)

    def test_recounting_needs_no_cause(self, drift, ctx, campaign):
        """L'emplacement rejoint le comptage général : il n'y a rien à excuser."""
        _sealed_location(ctx, campaign, counted=12, reference=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))
        line = drift.list_drifts(campaign.id)[0]
        assert drift.resolve(campaign, [line.id], DriftResolution.RECOUNT) == 1
        assert drift.unresolved_material(campaign.id) == 0


class TestNothingSealedNothingToDo:
    def test_a_campaign_without_a_sealed_location_computes_nothing(
        self, drift, campaign
    ):
        assert drift.record_general_load(campaign, _day_j(campaign, 9)) == 0
        assert drift.list_drifts(campaign.id) == []


class TestTheTransitionBlocker:
    def test_a_material_unresolved_drift_blocks_analysis(self):
        from inventory.domain.workflow import campaign_transition_blockers

        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING,
            CampaignStatus.ANALYSIS,
            book_stock_frozen=True,
            unresolved_drift=2,
        )
        codes = [b.code for b in blockers]
        assert "EARLY_COUNT_DRIFT_UNRESOLVED" in codes

    def test_it_does_not_block_the_closure(self):
        """Les dérives se tranchent avant l'analyse, pas au moment de clôturer."""
        from inventory.domain.workflow import campaign_transition_blockers

        blockers = campaign_transition_blockers(
            CampaignStatus.ANALYSIS,
            CampaignStatus.CLOSED,
            unresolved_drift=2,
            publication_done=True,
        )
        assert [b.code for b in blockers] == []
