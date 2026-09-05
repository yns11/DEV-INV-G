"""Le scellement d'un journal de précomptage, contre une vraie base.

Le journal ERP *est* le précomptage : il n'y a pas d'objet « lot » entre les
deux, et déclarer le périmètre d'un journal **scelle** ses emplacements.

Ce qui s'y décide :

* **la référence vient du journal**, pas d'un chargement séparé — c'est ce qui
  rend un précomptage autonome ;
* **sa date vient des lignes du journal**, pas d'un formulaire ;
* **la référence d'un emplacement scellé est celle de son précomptage**, sans
  quoi son écart d'inventaire tomberait à zéro dans le cas nominal et
  disparaîtrait de la campagne ;
* **le descellement demande un motif**, parce qu'il annule une preuve datée.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.db import new_id
from inventory.domain.enums import (
    CampaignStatus,
    JournalKind,
    JournalStatus,
    LabelResolution,
)
from inventory.domain.models import (
    Campaign,
    CountJournalLine,
    ErpJournalLine,
    Item,
    LocationKey,
)
from inventory.errors import ConflictError, ValidationError

pytestmark = pytest.mark.postgres

SOL = LocationKey(warehouse_id="ATP", location_id="SOL")
STK = LocationKey(warehouse_id="ATP", location_id="STK P FI")


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_scellement") as database:
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
             scope=(SOL,), counted_on=dt.date(2026, 6, 11)) -> str:
    # La date de comptage vient de l'en-tête, alimenté à l'import par la
    # colonne « Date de comptage » des lignes. C'est elle qui datera la
    # référence des emplacements scellés.
    journal_id = ctx.erp_journals.upsert_journal(
        campaign.id, journal_number=number, kind=JournalKind.INVE,
        erp_posted=posted, counted_on=counted_on,
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


class TestDeclaringSeals:
    """Un seul geste. C'est tout l'intérêt de la révision."""

    def test_a_journal_without_locations_is_refused(self, service, ctx, campaign):
        """Un périmètre vide ne scelle rien, et prétendre le contraire mentirait."""
        journal = _journal(ctx, campaign, scope=())
        with pytest.raises(ValidationError) as caught:
            service.declare_scope(campaign, journal, [])
        assert "vide" in str(caught.value)

    def test_the_buffer_cannot_be_declared(self, service, ctx, campaign):
        journal = _journal(ctx, campaign, scope=())
        with pytest.raises(ValidationError) as caught:
            service.declare_scope(campaign, journal, [campaign.config.buffer_key])
        assert "tampon" in str(caught.value)

    def test_declaring_seals_the_locations(self, service, ctx, campaign):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])
        assert ctx.journals.sealed_keys(campaign.id) == {("ATP", "SOL")}

    def test_declaring_writes_the_reference_read_from_the_journal(
        self, service, ctx, campaign
    ):
        """`ERP@T0` sort de la colonne « Stock ERP », pas d'un chargement."""
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])

        reference = ctx.book_stock.list(campaign.id)
        assert len(reference) == 1
        assert reference[0].qty == Decimal(10), "le stock ERP d'avant comptage"
        assert reference[0].erp_journal_id == journal
        assert reference[0].reference_date == dt.date(2026, 6, 11), (
            "la date vient du journal, pas d'un formulaire"
        )
        assert reference[0].unit_cost == Decimal("4.00")

    def test_an_unposted_journal_seals_all_the_same(self, service, ctx, campaign):
        """La garde d'origine exigeait le postage ; le métier l'a retirée.

        Un journal de précomptage se charge une fois posté et validé dans l'ERP
        — il y en a peu, et ils n'ont pas l'urgence du jour J. Une garde qui ne
        se déclenche jamais est une garde qu'on ne sait pas maintenir.
        """
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, posted=False, scope=())
        service.declare_scope(campaign, journal, [SOL])
        assert ctx.journals.sealed_keys(campaign.id) == {("ATP", "SOL")}

    def test_redeclaring_replaces_the_reference(self, service, ctx, campaign):
        """Un réimport remplace et met à jour : c'est la règle métier."""
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])
        ctx.erp_journals.replace_lines(campaign.id, journal, [
            _erp_line(campaign.id, journal, erp_line_number=1,
                      qty_on_hand=99, qty_counted=99),
        ])
        service.declare_scope(campaign, journal, [SOL])

        reference = ctx.book_stock.list(campaign.id)
        assert [line.qty for line in reference] == [Decimal(99)]


class TestUnsealing:
    def _sealed(self, service, ctx, campaign) -> str:
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])
        return journal

    def test_a_reason_is_required(self, service, ctx, campaign):
        journal = self._sealed(service, ctx, campaign)
        with pytest.raises(ValidationError) as caught:
            service.unseal(campaign, journal, reason="   ")
        assert "motif" in str(caught.value)

    def test_unsealing_gives_the_locations_back(self, service, ctx, campaign):
        journal = self._sealed(service, ctx, campaign)
        service.unseal(campaign, journal, reason="recomptage demandé")
        assert ctx.journals.sealed_keys(campaign.id) == set()

    def test_unsealing_drops_the_reference(self, service, ctx, campaign):
        """L'emplacement rejoint le comptage général : sa référence redevient
        celle du jour J, donc l'ancienne ne doit pas rester en travers."""
        journal = self._sealed(service, ctx, campaign)
        service.unseal(campaign, journal, reason="recomptage demandé")
        assert ctx.book_stock.list(campaign.id) == []

    def test_unsealing_takes_the_scope_with_it(self, service, ctx, campaign):
        """Sans périmètre, plus rien à couvrir : redéclarer est ce qui rescelle."""
        journal = self._sealed(service, ctx, campaign)
        service.unseal(campaign, journal, reason="recomptage demandé")
        stored = ctx.erp_journals.get_by_number(campaign.id, "NPEM-1")
        assert stored.scope == [] and stored.is_sealed is False


class TestTheGeneralLoadPreservesSealedReferences:
    def test_a_sealed_location_keeps_its_own_date(self, service, ctx, campaign):
        """La règle de référence, appliquée à deux dates.

        Sans elle, l'écart d'un emplacement précompté vaudrait zéro dans le cas
        nominal — poster son journal ayant réaligné l'ERP sur le physique — et
        le résultat de son inventaire disparaîtrait de la campagne.
        """
        from inventory.domain.models import BookStockLine

        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])

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


class TestSealingOverAnExistingReference:
    """Sceller un emplacement que le stock ERP général sert déjà.

    L'ordre nominal est : sceller, puis charger. Mais rien ne l'impose, et un
    chargement partiel fait le jour même suffit à inverser les deux. La
    suppression ne portait alors que sur ``erp_journal_id`` : l'insertion
    tombait sur ``book_stock_uq``, et le scellement remontait un 500 en
    production, sur un geste que l'écran proposait lui-même.
    """

    def _loaded(self, ctx, campaign) -> None:
        from inventory.domain.models import BookStockLine

        ctx.book_stock.replace(campaign.id, [
            BookStockLine(campaign_id=campaign.id, item_number="MASS-1",
                          warehouse_id="ATP", location_id="SOL", qty=99,
                          reference_date=dt.date(2026, 6, 13)),
        ], batch_id=None)

    def test_the_precount_reference_replaces_the_general_one(
        self, service, ctx, campaign
    ):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        self._loaded(ctx, campaign)

        service.declare_scope(campaign, journal, [SOL])

        [line] = ctx.book_stock.list(campaign.id)
        assert line.qty == Decimal(10), "celle du précomptage, pas celle du jour"
        assert line.reference_date == dt.date(2026, 6, 11)
        assert line.erp_journal_id == journal

    def test_the_rest_of_the_stock_is_left_alone(self, service, ctx, campaign):
        """Remplacer par clé ne doit pas devenir remplacer tout court."""
        from inventory.domain.models import BookStockLine

        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        ctx.book_stock.replace(campaign.id, [
            BookStockLine(campaign_id=campaign.id, item_number="MASS-1",
                          warehouse_id="ATP", location_id="SOL", qty=99),
            BookStockLine(campaign_id=campaign.id, item_number="MASS-2",
                          warehouse_id="B06", location_id="AUTRE", qty=5),
        ], batch_id=None)

        service.declare_scope(campaign, journal, [SOL])

        by_key = {
            (line.warehouse_id, line.location_id): line
            for line in ctx.book_stock.list(campaign.id)
        }
        assert by_key[("B06", "AUTRE")].qty == Decimal(5)


class TestSealingDeclaresTheLocationCounted:
    """Sceller sans dire « compté » produisait un manquant fantôme.

    Seuls les journaux `IN_PROGRESS` et `POSTED` entrent dans les quantités
    comptées. Un emplacement scellé dont le journal de comptage restait
    `PENDING` apportait donc sa référence au stock ERP et **rien** au stock
    physique : un manquant de la totalité de sa quantité, sur un emplacement
    dont le scellement affirme précisément qu'il est compté.
    """

    def test_the_counting_journal_stops_being_pending(self, service, ctx, campaign):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])

        counting = {j.key: j for j in ctx.journals.list(campaign.id)}[SOL]
        assert counting.status is not JournalStatus.PENDING

    def test_a_posted_erp_journal_posts_it(self, service, ctx, campaign):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, posted=True, scope=())
        service.declare_scope(campaign, journal, [SOL])

        counting = {j.key: j for j in ctx.journals.list(campaign.id)}[SOL]
        assert counting.status is JournalStatus.POSTED

    def test_an_unposted_one_only_starts_it(self, service, ctx, campaign):
        """Poster est irréversible côté ERP : on ne l'invente pas ici."""
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, posted=False, scope=())
        service.declare_scope(campaign, journal, [SOL])

        counting = {j.key: j for j in ctx.journals.list(campaign.id)}[SOL]
        assert counting.status is JournalStatus.IN_PROGRESS

    def test_and_the_quantity_reaches_the_counted_total(self, service, ctx, campaign):
        """Le contrôle qui porte : ce que les KPI additionnent réellement."""
        from inventory.services.analysis_service import AnalysisService

        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])

        kpi = AnalysisService(ctx).kpis(campaign).as_dict()
        assert kpi["bookQty"] == 10.0, "le stock ERP du précomptage"
        assert kpi["countedQty"] == 12.0, "et son comptage, qui doit y répondre"
        assert kpi["physicalQty"] == 12.0


class TestOnceTheStockIsFrozen:
    """Le gel ferme la fenêtre du précomptage, et c'en est la définition.

    « Précompter » veut dire *avant* la référence générale. Après le gel,
    l'emplacement a déjà la sienne, le journal du jour apporte son comptage par
    l'import, et il n'y a rien à sceller. L'écran proposait pourtant « Déclarer
    et sceller » sur les journaux du jour J, et le geste écrivait une seconde
    référence sur des clés déjà servies.
    """

    def _frozen(self, ctx, campaign) -> Campaign:
        with ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE campaign SET book_stock_frozen_at = now() WHERE id = %s",
                (campaign.id,),
            )
        return campaign.model_copy(
            update={"book_stock_frozen_at": dt.datetime(2026, 6, 13, tzinfo=dt.UTC)}
        )

    def test_declaring_is_refused_and_says_why(self, service, ctx, campaign):
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        frozen = self._frozen(ctx, campaign)

        with pytest.raises(ConflictError) as caught:
            service.declare_scope(frozen, journal, [SOL])

        assert "gelé" in str(caught.value)
        assert "jour J" in str(caught.value)

    def test_nothing_is_written_before_the_refusal(self, service, ctx, campaign):
        """Un refus qui laisse un périmètre déclaré serait pire que le crash."""
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        frozen = self._frozen(ctx, campaign)

        with pytest.raises(ConflictError):
            service.declare_scope(frozen, journal, [SOL])

        assert ctx.journals.sealed_keys(campaign.id) == set()
        assert ctx.erp_journals.get_by_number(campaign.id, "NPEM-1").scope == []

    def test_a_precount_sealed_before_the_freeze_still_reseals(
        self, service, ctx, campaign
    ):
        """Le gel ferme la déclaration, pas le rafraîchissement.

        Un journal déjà scellé se recharge le jour J comme les autres, et sa
        référence se recalcule : fermer cela couperait le réimport de tous les
        journaux, puisque l'import rescelle au passage.
        """
        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])
        frozen = self._frozen(ctx, campaign)

        assert service.reseal_after_import(frozen) == 1


class TestTheLabelDecisions:
    """Où est la pièce, et ce que la réponse change aux quantités.

    Une étiquette d'un emplacement scellé qui reparaît ailleurs pose la seule
    question du dispositif qu'aucun calcul ne tranche. Trois réponses, et
    chacune doit **agir** : une décision qui ne changerait rien serait une
    opinion consignée, pas une décision.
    """

    def _two_places(self, ctx, campaign) -> str:
        """SOL scellé porte l'étiquette ; QUAI EXP la recompte le jour J."""
        _priced(ctx, campaign)
        sealed = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-1", kind=JournalKind.INVE,
            erp_posted=True, counted_on=dt.date(2026, 6, 11),
        )
        ctx.erp_journals.replace_lines(campaign.id, sealed, [
            _erp_line(campaign.id, sealed, erp_line_number=1,
                      label_id="001609233", qty_on_hand=8, qty_counted=8),
            _erp_line(campaign.id, sealed, erp_line_number=2,
                      label_id="001609234", qty_on_hand=8, qty_counted=8),
        ])
        other = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-2", kind=JournalKind.INVE,
        )
        ctx.erp_journals.replace_lines(campaign.id, other, [
            _erp_line(campaign.id, other, erp_line_number=1,
                      warehouse_id="ATP", location_id="QUAI EXP",
                      label_id="001609233", qty_on_hand=0, qty_counted=8),
        ])
        ctx.journals.ensure_journals(campaign.id, [SOL])
        return sealed

    def _same_place_twice(self, ctx, campaign) -> str:
        """SOL scellé par NPEM-1 ; NPEM-2 repasse dessus, au même endroit.

        Le cas réel : deux journaux de comptage avancé à deux jours d'écart sur
        le même emplacement. Ce n'est pas un déplacement — l'étiquette est là où
        elle doit être — et les quantités peuvent différer.
        """
        _priced(ctx, campaign)
        sealed = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-1", kind=JournalKind.INVE,
            erp_posted=True, counted_on=dt.date(2026, 6, 11),
        )
        ctx.erp_journals.replace_lines(campaign.id, sealed, [
            _erp_line(campaign.id, sealed, erp_line_number=1,
                      label_id="000235471", qty_on_hand=104, qty_counted=104),
        ])
        other = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-2", kind=JournalKind.INVE,
        )
        ctx.erp_journals.replace_lines(campaign.id, other, [
            _erp_line(campaign.id, other, erp_line_number=1,
                      label_id="000235471", qty_on_hand=93, qty_counted=93),
        ])
        ctx.journals.ensure_journals(campaign.id, [SOL])
        return sealed

    def test_the_same_place_is_not_somewhere_else(self, service, ctx, campaign):
        """« Comptée ailleurs » exigeait un autre *journal*, pas un autre endroit.

        L'écran affichait donc des lignes dont les deux colonnes d'emplacement
        portaient la même valeur — « ATP / SF1 comptée aussi en ATP / SF1 » — et
        proposait de la mettre au nouvel emplacement. Il n'y en a pas.
        """
        journal = self._same_place_twice(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        assert service.label_alerts(campaign.id) == []

    def test_it_is_said_somewhere_else_instead(self, service, ctx, campaign):
        """Les retirer sans le dire cacherait deux comptages du même endroit."""
        journal = self._same_place_twice(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        [row] = service.labels_recounted_in_place(campaign.id)
        assert row["sealedLocationId"] == "SOL"
        assert row["ownerJournalNumber"] == "NPEM-1", "celui qui est retenu"
        assert row["otherJournalNumber"] == "NPEM-2", "celui qui ne l'est pas"
        assert row["labelCount"] == 1

    def test_a_real_move_is_not_swept_up_with_them(self, service, ctx, campaign):
        """La correction ne doit pas emporter ce que le contrôle sert à voir."""
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        assert len(service.label_alerts(campaign.id)) == 1
        assert service.labels_recounted_in_place(campaign.id) == []

    def test_the_pair_is_listed_once_per_owner_not_per_line(
        self, service, ctx, campaign
    ):
        """Le départ était n'importe quelle ligne posée sur l'emplacement scellé.

        Celle d'un journal de passage en faisait donc partie, et la même paire
        ressortait autant de fois que de journaux ayant touché l'emplacement.
        Un emplacement scellé appartient à un seul journal : c'est le sien qui
        porte la preuve, et les autres ne sont qu'une trace.
        """
        journal = self._two_places(ctx, campaign)
        # Un troisième journal repasse sur l'emplacement scellé — la ligne de
        # passage, celle qui ne compte pas. Elle servait pourtant de point de
        # départ, et redoublait l'alerte de son propriétaire.
        passing = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-3", kind=JournalKind.INVE,
        )
        ctx.erp_journals.replace_lines(campaign.id, passing, [
            _erp_line(campaign.id, passing, erp_line_number=1,
                      label_id="001609233", qty_on_hand=8, qty_counted=8),
        ])
        service.declare_scope(campaign, journal, [SOL])

        alerts = service.label_alerts(campaign.id)
        assert [(a["sealedLocationId"], a["otherLocationId"]) for a in alerts] == [
            ("SOL", "QUAI EXP")
        ]

    def test_a_bulk_journal_carries_no_label_at_all(self, service, ctx, campaign):
        """« VRAC » n'est pas une étiquette, c'est un remplissage de colonne.

        Toutes les lignes d'un journal ``INVV`` portent la même : un emplacement
        vrac se compte en quantité, pas en lots identifiés. Le contrôle la
        lisait comme une identité de palette, et deux emplacements vrac
        quelconques devenaient « la même étiquette comptée aux deux endroits » —
        quatre cents lignes de faux doublons devant lesquelles il n'y a rien à
        faire, et qui noient les vrais déplacements.
        """
        _priced(ctx, campaign)
        sealed = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-VRAC-1", kind=JournalKind.INVV,
            erp_posted=True, counted_on=dt.date(2026, 6, 11),
        )
        ctx.erp_journals.replace_lines(campaign.id, sealed, [
            _erp_line(campaign.id, sealed, erp_line_number=1,
                      label_id="VRAC", qty_on_hand=8, qty_counted=8),
        ])
        other = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-VRAC-2", kind=JournalKind.INVV,
        )
        ctx.erp_journals.replace_lines(campaign.id, other, [
            _erp_line(campaign.id, other, erp_line_number=1,
                      warehouse_id="ATP", location_id="QUAI EXP",
                      label_id="VRAC", qty_on_hand=0, qty_counted=3),
        ])
        ctx.journals.ensure_journals(campaign.id, [SOL])
        service.declare_scope(campaign, sealed, [SOL])

        assert service.label_alerts(campaign.id) == []
        assert service.labels_recounted_in_place(campaign.id) == []

    def test_a_scanned_journal_is_still_controlled(self, service, ctx, campaign):
        """L'exclusion porte sur le type de journal, et s'arrête là."""
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        assert len(service.label_alerts(campaign.id)) == 1

    def test_the_alert_names_both_places(self, service, ctx, campaign):
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        alerts = service.label_alerts(campaign.id)
        assert len(alerts) == 1
        assert alerts[0]["labelId"] == "001609233"
        assert alerts[0]["sealedLocationId"] == "SOL"
        assert alerts[0]["otherLocationId"] == "QUAI EXP"
        assert alerts[0]["decision"] is None

    def test_keeping_the_new_place_empties_the_sealed_reference(
        self, service, ctx, campaign
    ):
        """La pièce est ailleurs : l'emplacement scellé perd sa quantité."""
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])
        assert ctx.book_stock.list(campaign.id)[0].qty == Decimal(16)

        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.KEEP_NEW,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
        )

        assert ctx.book_stock.list(campaign.id)[0].qty == Decimal(8), (
            "l'étiquette sort de l'emplacement scellé"
        )

    def test_and_the_counted_quantity_follows(self, service, ctx, campaign):
        """Sinon la décision creuse l'écart qu'elle est censée trancher.

        L'étiquette sortait de la référence et restait dans le comptage : un
        emplacement scellé à 8 en stock ERP et 16 comptés, c'est-à-dire un écart
        de 8 créé par la décision elle-même. Référence et comptage se lisent
        dans les mêmes lignes ; ils sortent de la même agrégation.
        """
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])
        counting = {j.key: j for j in ctx.journals.list(campaign.id)}[SOL]
        assert ctx.journals.lines_by_journal(campaign.id)[counting.id][
            0
        ].qty_imported == Decimal(16)

        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.KEEP_NEW,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
        )

        assert ctx.journals.lines_by_journal(campaign.id)[counting.id][
            0
        ].qty_imported == Decimal(8)

    def test_keeping_the_sealed_place_leaves_the_reference_alone(
        self, service, ctx, campaign
    ):
        """Elle n'a pas bougé : c'est l'autre ligne qui est l'erreur."""
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.KEEP_SEALED,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
        )

        assert ctx.book_stock.list(campaign.id)[0].qty == Decimal(16)

    def test_the_decision_shows_on_the_alert(self, service, ctx, campaign):
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])
        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.RECOUNT,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
            comment="palette introuvable",
        )

        alert = service.label_alerts(campaign.id)[0]
        assert alert["decision"] == "RECOUNT"
        assert alert["comment"] == "palette introuvable"

    def test_signalling_lists_the_place_to_rescan(self, service, ctx, campaign):
        """C'est l'ancien emplacement qu'il faut desceller, pas le nouveau."""
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])
        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.RECOUNT,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
        )

        places = service.locations_to_rescan(campaign.id)
        assert len(places) == 1
        assert (places[0]["warehouseId"], places[0]["locationId"]) == ("ATP", "SOL")
        assert places[0]["journalNumber"] == "NPEM-1"
        assert places[0]["isSealed"] is True
        assert [lab["labelId"] for lab in places[0]["labels"]] == ["001609233"]

    def test_the_other_two_outcomes_ask_for_no_rescan(self, service, ctx, campaign):
        """On a tranché : il n'y a plus rien à aller voir."""
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])
        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.KEEP_NEW,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
        )
        assert service.locations_to_rescan(campaign.id) == []

    def test_a_decision_survives_the_next_import(self, service, ctx, campaign):
        """Le notebook est rejoué toutes les quelques minutes le jour J.

        Repartir de zéro effacerait des décisions prises entre deux imports —
        un exploitant tranche à neuf heures et retrouve la question vierge à
        neuf heures cinq, sans que rien ne le lui dise.
        """
        journal = self._two_places(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])
        service.decide_label(
            campaign, label_id="001609233", item_number="MASS-1",
            decision=LabelResolution.KEEP_NEW,
            sealed=SOL, other=LocationKey(warehouse_id="ATP", location_id="QUAI EXP"),
        )

        service.reseal_after_import(campaign)

        assert ctx.book_stock.list(campaign.id)[0].qty == Decimal(8)
        assert service.label_alerts(campaign.id)[0]["decision"] == "KEEP_NEW"


class TestPassThroughLinesDoNotCount:
    """Une ligne de passage n'est pas un comptage.

    Un journal ERP porte des lignes sur des emplacements qu'il ne couvre pas :
    elles matérialisent un déplacement — 1 932 sur 58 345 dans l'export du
    13 juin. L'import créait un journal de comptage pour chacune, **avec ses
    quantités**, sur des emplacements que personne n'avait sélectionnés. Ils
    apparaissaient ensuite dans la vue des journaux de comptage, entraient dans
    le dénominateur d'avancement, et produisaient un écart au jour J.
    """

    def _with_a_pass_through(self, ctx, campaign) -> str:
        _priced(ctx, campaign)
        journal = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number="NPEM-1", kind=JournalKind.INVE,
            erp_posted=True, counted_on=dt.date(2026, 6, 11),
        )
        ctx.erp_journals.replace_lines(campaign.id, journal, [
            _erp_line(campaign.id, journal, erp_line_number=1,
                      qty_on_hand=10, qty_counted=10),
            # La ligne de passage : un autre emplacement, que ce journal ne
            # couvre pas.
            _erp_line(campaign.id, journal, erp_line_number=2,
                      warehouse_id="ATP", location_id="STK P FI",
                      qty_on_hand=4, qty_counted=4),
        ])
        # Ce que l'import avait fait : un journal de comptage par emplacement
        # touché, quantités comprises.
        ctx.journals.ensure_journals(campaign.id, [SOL, STK])
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}
        ctx.journals.replace_imported_lines(
            campaign.id, [journals[SOL].id, journals[STK].id],
            [
                CountJournalLine(
                    id=new_id(), journal_id=journals[SOL].id,
                    campaign_id=campaign.id, item_number="MASS-1",
                    qty_imported=Decimal(10), erp_journal_number="NPEM-1",
                ),
                CountJournalLine(
                    id=new_id(), journal_id=journals[STK].id,
                    campaign_id=campaign.id, item_number="MASS-1",
                    qty_imported=Decimal(4), erp_journal_number="NPEM-1",
                ),
            ],
        )
        return journal

    def test_declaring_removes_the_journal_of_a_pass_through(
        self, service, ctx, campaign
    ):
        journal = self._with_a_pass_through(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        keys = {j.key for j in ctx.journals.list(campaign.id)}
        assert SOL in keys
        assert STK not in keys, (
            "personne n'a sélectionné cet emplacement : son journal n'a pas lieu "
            "d'être"
        )

    def test_the_raw_erp_line_survives(self, service, ctx, campaign):
        """C'est la trace, et c'est ce que le contrôle par étiquette relit."""
        journal = self._with_a_pass_through(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        lines = ctx.erp_journals.lines(campaign.id, journal)
        assert {line.location_id for line in lines} == {"SOL", "STK P FI"}

    def test_a_manual_quantity_protects_the_journal(self, service, ctx, campaign):
        """La règle ne doit jamais emporter du travail humain."""
        journal = self._with_a_pass_through(ctx, campaign)
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}
        line = ctx.journals.lines_by_journal(campaign.id)[journals[STK].id][0]
        ctx.journals.upsert_line(
            line.model_copy(update={"qty_manual": Decimal(7)}), actor="alice"
        )

        service.declare_scope(campaign, journal, [SOL])

        keys = {j.key for j in ctx.journals.list(campaign.id)}
        assert STK in keys, "quelqu'un y a compté à la main : on n'y touche pas"

    def test_a_reimport_does_not_bring_it_back(self, service, ctx, campaign):
        """Sinon le nettoyage ne tiendrait que jusqu'au prochain notebook."""
        from inventory.services.import_service import ImportService

        journal = self._with_a_pass_through(ctx, campaign)
        service.declare_scope(campaign, journal, [SOL])

        imports = ImportService(ctx)
        imports.batches.archive = lambda *a, **k: None
        rows = [
            {
                "journal_number": "NPEM-1", "erp_line_number": 1,
                "warehouse_id": "ATP", "location_id": "SOL",
                "item_number": "MASS-1", "counted_quantity": 10,
                "qty_on_hand": 10, "journal_name_id": "INVE",
                "is_posted": True, "unit": "PCE",
            },
            {
                "journal_number": "NPEM-1", "erp_line_number": 2,
                "warehouse_id": "ATP", "location_id": "STK P FI",
                "item_number": "MASS-1", "counted_quantity": 4,
                "qty_on_hand": 4, "journal_name_id": "INVE",
                "is_posted": True, "unit": "PCE",
            },
        ]
        from inventory.ingest import ParseResult

        imports.parser.parse = lambda contract, **kw: (  # type: ignore[method-assign]
            None,
            ParseResult(contract_key=contract, rows=rows, rows_received=len(rows)),
        )
        imports.import_journal_lines(campaign, mode="file", payload=b"x",
                                     filename="j.csv")

        keys = {j.key for j in ctx.journals.list(campaign.id)}
        assert STK not in keys


class TestAnLocationBelongsToOneJournal:
    """Deux journaux sur le même emplacement : lequel le compte ?

    Le cas arrive : deux comptages avancés à deux jours d'écart passent par le
    même emplacement, ou l'un ne fait qu'y déplacer une palette. La base tient
    déjà l'unicité du périmètre (``erp_journal_scope_location_uq``), mais elle
    la tenait *seule* — et un index unique ne sait pas nommer le propriétaire.
    Ce qui remontait était une ``UniqueViolation`` brute, donc un 500 devant
    lequel il n'y a rien à faire.

    L'autre moitié du cas est plus silencieuse et pire : un journal **non
    déclaré** dont les lignes touchent un emplacement scellé posait sa quantité
    par-dessus celle du propriétaire, pendant que la référence restait celle du
    propriétaire. Deux journaux dans un même écart, et rien pour le dire.
    """

    def _counted(self, ctx, campaign, key=SOL) -> list[Decimal]:
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}
        by_journal = ctx.journals.lines_by_journal(campaign.id)
        return [line.qty_imported for line in by_journal.get(journals[key].id, [])]

    def _erp_journal(self, ctx, campaign, number, *, qty) -> str:
        journal_id = ctx.erp_journals.upsert_journal(
            campaign.id, journal_number=number, kind=JournalKind.INVE,
            erp_posted=True, counted_on=dt.date(2026, 6, 11),
        )
        ctx.erp_journals.replace_lines(campaign.id, journal_id, [
            _erp_line(campaign.id, journal_id, erp_line_number=1,
                      label_id=f"ET-{number}", qty_on_hand=qty, qty_counted=qty),
        ])
        return journal_id

    def test_the_refusal_names_the_journal_that_owns_it(
        self, service, ctx, campaign
    ):
        """Sans ce refus, l'écran renvoie un 500 sur un geste ordinaire."""
        _priced(ctx, campaign)
        first = self._erp_journal(ctx, campaign, "NPEM-A", qty=10)
        second = self._erp_journal(ctx, campaign, "NPEM-B", qty=99)
        service.declare_scope(campaign, first, [SOL])

        with pytest.raises(ConflictError) as caught:
            service.declare_scope(campaign, second, [SOL])

        assert "NPEM-A" in str(caught.value), "l'exploitant doit savoir qui desceller"
        assert "ATP / SOL" in str(caught.value)

    def test_the_proposal_does_not_offer_it_either(self, service, ctx, campaign):
        """Le refus est le filet ; la liste proposée est la première défense."""
        _priced(ctx, campaign)
        first = self._erp_journal(ctx, campaign, "NPEM-A", qty=10)
        second = self._erp_journal(ctx, campaign, "NPEM-B", qty=99)
        service.declare_scope(campaign, first, [SOL])

        assert service.propose_scope(campaign, second) == []

    def test_a_second_journal_does_not_count_a_sealed_location(
        self, service, ctx, campaign
    ):
        """Le cas silencieux : B n'est pas déclaré, ses lignes passent sur SOL.

        Sa quantité remplaçait celle de A tandis que la référence restait celle
        de A. L'emplacement affichait alors l'écart entre le stock d'un journal
        et le comptage d'un autre.
        """
        from inventory.ingest import ParseResult
        from inventory.services.import_service import ImportService

        _priced(ctx, campaign)
        first = self._erp_journal(ctx, campaign, "NPEM-A", qty=10)
        ctx.journals.ensure_journals(campaign.id, [SOL])
        service.declare_scope(campaign, first, [SOL])

        imports = ImportService(ctx)
        imports.batches.archive = lambda *a, **k: None
        imports.parser.parse = lambda contract, **kw: (None, ParseResult(
            contract_key=contract,
            rows=[{
                "journal_number": "NPEM-B", "erp_line_number": 1,
                "warehouse_id": "ATP", "location_id": "SOL",
                "item_number": "MASS-1", "counted_quantity": 99,
                "qty_on_hand": 99, "journal_name_id": "INVE",
                "is_posted": True, "unit": "PCE",
            }],
            rows_received=1,
        ))
        imports.import_journal_lines(campaign, mode="file", payload=b"x",
                                     filename="b.csv")

        assert self._counted(ctx, campaign) == [Decimal(10)], (
            "seul le journal propriétaire compte son emplacement"
        )
        assert [line.qty for line in ctx.book_stock.list(campaign.id)] == [
            Decimal(10)
        ]

    def test_declaring_after_the_import_rewrites_the_count(
        self, service, ctx, campaign
    ):
        """L'ordre des gestes ne doit rien changer.

        Quand les deux journaux entrent avant qu'aucun ne soit déclaré, l'import
        ne sait pas encore trier : les deux comptent, et l'emplacement porte leur
        somme. Déclarer est le moment où l'on sait — et le comptage se recalcule
        alors sur le seul propriétaire, comme la référence.
        """
        _priced(ctx, campaign)
        first = self._erp_journal(ctx, campaign, "NPEM-A", qty=10)
        self._erp_journal(ctx, campaign, "NPEM-B", qty=99)
        ctx.journals.ensure_journals(campaign.id, [SOL])
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}
        ctx.journals.replace_imported_lines(campaign.id, [journals[SOL].id], [
            CountJournalLine(
                id=new_id(), journal_id=journals[SOL].id, campaign_id=campaign.id,
                item_number="MASS-1", qty_imported=Decimal(109),
                erp_journal_number="NPEM-B",
            ),
        ])

        service.declare_scope(campaign, first, [SOL])

        assert self._counted(ctx, campaign) == [Decimal(10)]

    def test_unsealing_hands_the_location_over(self, service, ctx, campaign):
        """C'est le geste qui transfère : desceller A, déclarer B.

        Référence *et* comptage doivent suivre ensemble. Recalculer la seule
        référence laissait le comptage de A sous le stock de B.
        """
        _priced(ctx, campaign)
        first = self._erp_journal(ctx, campaign, "NPEM-A", qty=10)
        second = self._erp_journal(ctx, campaign, "NPEM-B", qty=99)
        service.declare_scope(campaign, first, [SOL])
        service.unseal(campaign, first, reason="mauvais journal")
        service.declare_scope(campaign, second, [SOL])

        assert self._counted(ctx, campaign) == [Decimal(99)]
        reference = ctx.book_stock.list(campaign.id)
        assert [line.qty for line in reference] == [Decimal(99)]
        assert reference[0].erp_journal_id == second


class TestTheOverviewReportsWhatIsSealed:
    """C'est ce compteur qui ouvre l'analyse avant le gel général.

    Le gel du stock ERP est global et arrive au jour J ; le scellement d'un
    précomptage est un gel par emplacement. L'écran a besoin de savoir qu'il
    existe une référence figée, même partielle, sinon il cache un écart déjà
    définitif pendant les jours où l'on peut encore aller voir sur le terrain.
    """

    def test_it_counts_the_sealed_locations(self, service, ctx, campaign):
        from inventory.services.campaign_service import CampaignService

        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        before = CampaignService(ctx).overview(campaign.id)["counts"]
        assert before["sealedLocations"] == 0

        service.declare_scope(campaign, journal, [SOL])

        after = CampaignService(ctx).overview(campaign.id)["counts"]
        assert after["sealedLocations"] == 1

    def test_unsealing_brings_it_back_to_zero(self, service, ctx, campaign):
        from inventory.services.campaign_service import CampaignService

        _priced(ctx, campaign)
        journal = _journal(ctx, campaign, scope=())
        service.declare_scope(campaign, journal, [SOL])
        service.unseal(campaign, journal, reason="recomptage demandé")

        counts = CampaignService(ctx).overview(campaign.id)["counts"]
        assert counts["sealedLocations"] == 0
