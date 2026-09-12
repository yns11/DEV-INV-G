"""La dérive d'un emplacement scellé : un indice, et rien de plus.

::

    dérive = ERP@J − compté@T0

Ce qui s'y décide :

* la dérive se mesure contre le **comptage** de T0, et contre lui seul —
  ``ERP@T0`` n'existe plus, et l'ajustement des précomptages non plus ;
* le rapprochement est une **jointure externe complète** : un article apparu
  dans le stock du jour J sans jamais avoir été compté, ou disparu, sont les
  deux cas qu'une jointure interne perdrait ;
* un recalcul repart **à neuf**, puisqu'une dérive ne porte plus d'issue ;
* une dérive **ne bloque rien** — ni le passage en analyse, ni la clôture.

Pourquoi elle ne bloque plus
----------------------------
Un journal de précomptage est posté dans l'ERP *avant* que la photo du jour J
ne soit prise, et cette photo l'a donc déjà intégré. Ce qui subsiste après ce
réalignement n'est pas un écart d'inventaire — celui-là se mesure contre la
référence unique du jour J — mais ce qui a bougé entre les deux dates. Il n'y a
rien à trancher, donc rien qui puisse rester non tranché.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import (
    CampaignStatus,
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


def _sealed_location(ctx, campaign, *, counted: int, on_hand: int,
                     item="MASS-1", cost="10.00") -> None:
    """Un emplacement précompté puis scellé, avec son comptage.

    ``on_hand`` est ce que l'ERP annonçait au moment du précomptage. Il est posé
    parce que l'export le porte, et pas parce qu'il sert de référence : il n'en
    sert plus à rien depuis que la référence est unique.
    """
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
            warehouse_id=SOL.warehouse_id,
            location_id=SOL.location_id, item_number=item,
            qty_on_hand=on_hand, qty_counted=counted,
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
            item_number=item, qty_imported=counted, qty_on_hand=on_hand,
        ),
    ])
    ctx.journals.set_status(
        campaign.id, [journal.id], JournalStatus.POSTED, actor="alice"
    )

    # Déclarer le périmètre scelle. Un seul geste, et il ne pose aucune
    # référence : la référence de la campagne est le stock ERP du jour J.
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

    Une ligne à zéro est le cas normal, donc l'absence d'information : sur un
    précomptage de cinquante emplacements à trois cents références, elle
    enterrait les quelques lignes qui apprennent quelque chose. Ce que ces
    contrôles vérifient reste entier — la confrontation a bien eu lieu sur
    cette ligne et n'a rien trouvé — mais là où la trace vit.
    """

    def test_the_barrier_held_and_the_drift_is_null(self, drift, ctx, campaign):
        """`ERP@J` vaut ce que le précomptage a compté : c'est ce qu'on attend.

        Le journal a été posté dans l'ERP, et la photo du jour J l'a intégré.
        """
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, _day_j(campaign, 12))

        stored = ctx.drifts.list(campaign.id)
        assert len(stored) == 1
        assert stored[0].drift_qty == 0

    def test_it_is_not_shown(self, drift, ctx, campaign):
        """« N'affiche que les lignes où la dérive n'est pas nulle. »"""
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, _day_j(campaign, 12))
        assert drift.list_drifts(campaign.id) == []

    def test_the_inventory_variance_is_not_a_drift(self, drift, ctx, campaign):
        """Contre l'ancien `ERP@T0`, cette dérive vaudrait 2 — l'écart d'inventaire.

        Cet écart-là a été **posté dans l'ERP** avant la photo du jour J, qui
        l'a donc déjà intégré : le mesurer une seconde fois ici reviendrait à
        compter deux fois la même correction. C'est exactement ce que la
        suppression de `ERP@T0` évite, et c'est pourquoi la ligne ne porte plus
        que deux quantités.
        """
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, _day_j(campaign, 12))

        line = ctx.drifts.list(campaign.id)[0]
        assert not hasattr(line, "qty_erp_t0")
        assert line.qty_counted_t0 == 12
        assert line.qty_erp_j == 12
        assert line.drift_qty == 0


class TestAMovementAfterSealing:
    def test_it_shows_up_without_blocking(self, drift, ctx, campaign):
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))

        line = drift.list_drifts(campaign.id)[0]
        assert line.drift_qty == Decimal(-3)
        assert line.drift_value == Decimal("-30.00")
        # Deux champs, et pas plus : la ligne ne porte ni matérialité ni issue.
        assert not hasattr(line, "is_material")
        assert not hasattr(line, "blocks_analysis")

    def test_an_article_that_appeared_is_seen(self, drift, ctx, campaign):
        """Jamais compté, pourtant présent dans le stock du jour J.

        C'est l'un des deux cas qu'une jointure interne perdrait.
        """
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(
            campaign, _day_j(campaign, 12) + _day_j(campaign, 4, item="MASS-2")
        )
        by_item = {line.item_number: line for line in drift.list_drifts(campaign.id)}
        assert by_item["MASS-2"].qty_counted_t0 == 0
        assert by_item["MASS-2"].qty_erp_j == 4

    def test_an_article_that_vanished_is_seen(self, drift, ctx, campaign):
        """L'autre cas : compté à T0, absent du stock du jour J."""
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, [])
        line = drift.list_drifts(campaign.id)[0]
        assert line.qty_erp_j == 0
        assert line.drift_qty == Decimal(-12)


class TestRecomputingStartsAfresh:
    def test_the_quantities_are_refreshed(self, drift, ctx, campaign):
        """Le notebook est rejoué toutes les quelques minutes le jour J."""
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))
        assert drift.list_drifts(campaign.id)[0].qty_erp_j == 9

        drift.record_general_load(campaign, _day_j(campaign, 7))
        assert drift.list_drifts(campaign.id)[0].qty_erp_j == 7

    def test_a_line_that_stops_drifting_stops_being_shown(
        self, drift, ctx, campaign
    ):
        """Rien n'est reporté d'un calcul à l'autre.

        L'ancien recalcul recopiait les issues déjà données, faute de quoi une
        décision prise à neuf heures se perdait à neuf heures cinq. Il n'y a
        plus d'issue à sauver, et une ligne qui cesse de dériver doit donc
        cesser d'être montrée — un report l'aurait figée sur son premier état.
        """
        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        drift.record_general_load(campaign, _day_j(campaign, 9))
        assert len(drift.list_drifts(campaign.id)) == 1

        drift.record_general_load(campaign, _day_j(campaign, 12))
        assert drift.list_drifts(campaign.id) == []


class TestAdjustmentsDoNotEnterTheDrift:
    def test_an_adjustment_does_not_move_the_counted_side(
        self, drift, ctx, campaign
    ):
        """``compté@T0``, et rien d'autre.

        Le terme s'appelait « physique » et valait ``compté + ajusté``. Un
        ajustement de campagne porte pourtant sur le **jour J** — c'est un
        mouvement postérieur au comptage — et l'ajouter ici corrigeait un terme
        par une correction destinée à l'autre : la dérive bougeait de la valeur
        de l'ajustement, sur un emplacement où rien n'avait bougé.
        """
        from inventory.db import new_id
        from inventory.domain.enums import AdjustmentKind
        from inventory.domain.models import AdjustmentLine

        _sealed_location(ctx, campaign, counted=12, on_hand=10)
        ctx.adjustments.upsert([
            AdjustmentLine(
                id=new_id(), campaign_id=campaign.id, item_number="MASS-1",
                warehouse_id=SOL.warehouse_id, location_id=SOL.location_id,
                qty=Decimal(5), kind=AdjustmentKind.ADJUSTMENT,
            ),
        ], actor="alice")
        assert len(ctx.adjustments.list(campaign.id)) == 1
        drift.record_general_load(campaign, _day_j(campaign, 12))

        assert ctx.drifts.list(campaign.id)[0].qty_counted_t0 == 12
        assert drift.list_drifts(campaign.id) == []


class TestNothingSealedNothingToDo:
    def test_a_campaign_without_a_sealed_location_computes_nothing(
        self, drift, campaign
    ):
        assert drift.record_general_load(campaign, _day_j(campaign, 9)) == 0
        assert drift.list_drifts(campaign.id) == []


class TestNothingIsBlockedAnyMore:
    """Une dérive n'entraîne aucune action requise et aucun constat bloquant."""

    def test_no_blocker_mentions_a_drift(self):
        from inventory.domain.workflow import campaign_transition_blockers

        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING,
            CampaignStatus.ANALYSIS,
            book_stock_frozen=True,
        )
        assert [b.code for b in blockers] == []

    def test_the_parameter_itself_is_gone(self):
        """Épinglé sur la signature, et non sur une liste vide.

        Un paramètre laissé en place et ignoré passerait ce contrôle-ci sans
        rien bloquer, et le prochain lecteur croirait la règle encore vivante.
        """
        import inspect

        from inventory.domain.workflow import campaign_transition_blockers

        parameters = inspect.signature(campaign_transition_blockers).parameters
        assert "unresolved_drift" not in parameters

    def test_the_service_has_no_resolution_left(self):
        from inventory.services.drift_service import DriftService

        assert not hasattr(DriftService, "resolve")
        assert not hasattr(DriftService, "unresolved_material")
