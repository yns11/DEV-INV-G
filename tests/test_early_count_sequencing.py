"""Le comptage avancé passe avant le chargement du stock ERP, garde comprise.

Le défaut que ces contrôles fixent était entier : l'écran « Comptages avancés »
était fermé, et son API refusait, tant que le stock ERP général n'était pas
chargé. Or ce chargement a lieu **le jour J**, et un lot avancé se compte des
jours avant — l'écran n'ouvrait donc qu'après le moment où il sert.

La cause tient en une ligne : tout le chantier s'était branché sur l'aspect
``count_journals``, dont le prérequis est le stock ERP. C'était le mauvais
aspect. Un journal de comptage général se mesure contre le stock chargé ; un lot
avancé porte sa propre référence dans la colonne « Stock ERP » de son journal.
D'où ``early_counts``, qui n'attend que le référentiel articles.

Rien de tout cela n'était visible depuis les contrôles existants des comptages
avancés : ils neutralisent ``guard`` pour tester autre chose, ce qui est
légitime et ce qui a laissé passer la faute. Ceux-ci gardent donc **la vraie
garde**, et c'est tout leur objet.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign, Item, LocationKey
from inventory.errors import FrozenError

pytestmark = pytest.mark.postgres

SOL = LocationKey(warehouse_id="ATP", location_id="SOL")

#: L'acteur est celui qui crée la campagne dans la base jetable : la garde
#: interroge le rôle avant la phase, et un autre nom échouerait pour une raison
#: qui n'est pas celle qu'on mesure ici.
ACTOR = "test"


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_sequencement_avance") as database:
        yield database


def _campaign(db, status: CampaignStatus) -> Campaign:
    campaign_id = make_campaign(db, f"SEQ-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = %s WHERE id = %s",
            (str(status), campaign_id),
        )
    return Campaign(
        id=campaign_id,
        code=f"SEQ-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 6, 13),
        status=status,
        created_by=ACTOR,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def campaign(db):
    return _campaign(db, CampaignStatus.COUNTING)


@pytest.fixture
def ctx(db):
    """La vraie garde. C'est le sujet du fichier, pas un détail du montage."""
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext

    return ServiceContext(actor=ACTOR, db=db, settings=get_settings())


def _load_items(ctx, campaign: Campaign) -> None:
    ctx.referentials.upsert_items(
        [
            Item(
                campaign_id=campaign.id,
                item_number="MASS-1",
                name="X",
                std_price=Decimal("4.00"),
            )
        ],
        actor=ACTOR,
    )
    # Les compteurs de la garde sont mis en cache par requête : sans cet oubli,
    # le référentiel qu'on vient de charger ne serait pas vu.
    ctx.forget_progress(campaign.id)


def _import_service(ctx, monkeypatch, rows: list[dict[str, Any]]):
    from inventory.ingest import ParseResult
    from inventory.services.import_service import ImportService

    service = ImportService(ctx)
    service.batches.archive = lambda *a, **k: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        service.parser,
        "parse",
        lambda contract, **kw: (
            None,
            ParseResult(contract_key=contract, rows=rows, rows_received=len(rows)),
        ),
    )
    return service


def _row(**kwargs) -> dict[str, Any]:
    base = {
        "journal_number": "NPEM-1",
        "erp_line_number": 1,
        "warehouse_id": SOL.warehouse_id,
        "location_id": SOL.location_id,
        "item_number": "MASS-1",
        "counted_quantity": 12,
        "qty_on_hand": 10,
        "journal_name_id": "INVE",
        "is_posted": True,
        "unit": "PCE",
    }
    return {**base, **kwargs}


def _stock_lines(ctx, campaign: Campaign) -> int:
    return ctx.book_stock.count(campaign.id)


class TestTheAdvanceCountDoesNotWaitForTheGeneralLoad:
    """Le cas qui ne passait pas, et qui est tout l'intérêt de la fonction."""

    def test_the_journal_of_an_advance_batch_imports_without_any_erp_stock(
        self, ctx, campaign, monkeypatch
    ):
        """C'est l'étape 4 du process : elle précède le chargement de l'étape 10.

        Le fichier apporte le comptage **et**, dans sa colonne « Stock ERP », ce
        contre quoi il se compare. Rien n'attend un snapshot.
        """
        _load_items(ctx, campaign)
        assert _stock_lines(ctx, campaign) == 0, "le décor : aucun stock chargé"
        service = _import_service(ctx, monkeypatch, [_row()])

        outcome = service.import_journal_lines(
            campaign, payload=b"x", filename="j.csv"
        )

        assert outcome.rows_accepted == 1
        assert ctx.erp_journals.get_by_number(campaign.id, "NPEM-1") is not None

    def test_and_its_scope_can_be_declared_in_the_same_state(
        self, ctx, campaign, monkeypatch
    ):
        """Importer sans pouvoir déclarer le périmètre ne servirait à rien.

        Le lot s'ouvre sur le périmètre ; sans lui, la suite entière — créer,
        clore, sceller — est refusée pour une autre raison.
        """
        from inventory.services.early_count_service import EarlyCountService

        _load_items(ctx, campaign)
        _import_service(ctx, monkeypatch, [_row()]).import_journal_lines(
            campaign, payload=b"x", filename="j.csv"
        )
        journal = ctx.erp_journals.get_by_number(campaign.id, "NPEM-1")
        ctx.journals.ensure_journals(campaign.id, [SOL])
        ctx.forget_progress(campaign.id)

        declared = EarlyCountService(ctx).declare_scope(campaign, journal.id, [SOL])

        assert declared == 1
        # Les seules lignes de stock sont celles que le journal vient de poser :
        # aucun chargement général n'a eu lieu, et il n'en fallait aucun.
        reference = ctx.book_stock.list(campaign.id)
        assert [line.erp_journal_id for line in reference] == [journal.id]


class TestWhatTheRelaxationDoesNotTouch:
    """Une garde qu'on déplace doit laisser en place celles qui restent utiles."""

    def test_the_general_journals_still_wait_for_the_stock(self, ctx, campaign):
        """Le prérequis d'origine tient : il visait le comptage général.

        Corriger une ligne à la main, changer un statut, forcer au stock ERP —
        tout ce qui s'écrit *dans l'application* se mesure contre la référence
        chargée, et continue de l'exiger.
        """
        _load_items(ctx, campaign)

        with pytest.raises(FrozenError) as caught:
            ctx.guard(campaign, "count_journals")

        assert "stock ERP" in str(caught.value)

    def test_the_advance_count_still_waits_for_the_article_referential(self, ctx, campaign):
        """Ses lignes se rattachent à des articles, et sceller les valorise."""
        with pytest.raises(FrozenError) as caught:
            ctx.guard(campaign, "early_counts")

        assert "articles" in str(caught.value)

    @pytest.mark.parametrize(
        "status", [CampaignStatus.PREPARATION, CampaignStatus.ANALYSIS]
    )
    def test_the_phase_still_decides(self, db, ctx, status):
        """Le comptage avancé reste une sous-phase de `COUNTING`, pas un passe-droit.

        Déplacer un prérequis d'ordre ne doit pas ouvrir une fenêtre : hors
        comptage, l'aspect est gelé comme les autres.
        """
        campaign = _campaign(db, status)
        _load_items(ctx, campaign)

        with pytest.raises(FrozenError) as caught:
            ctx.guard(campaign, "early_counts")

        assert "gelé" in str(caught.value)


class TestWhatTheScreenIsTold:
    def test_the_sidebar_gets_the_same_answer_as_the_guard(self, ctx, campaign):
        """L'écran grise une étape avec la fonction qui la refuse, ou il ment.

        Ici c'est l'inverse qui s'était produit : la barre latérale lisait
        `count_journals`, l'entrée restait verrouillée, et le motif affiché
        parlait d'un stock que la fonction n'a jamais eu à attendre.
        """
        from inventory.domain.sequence import unlocked_aspects

        _load_items(ctx, campaign)
        unlocked = unlocked_aspects(ctx.progress(campaign))

        assert unlocked["early_counts"] is True
        assert unlocked["count_journals"] is False
