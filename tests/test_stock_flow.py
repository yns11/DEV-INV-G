"""Réconciliation de deux campagnes à travers les flux de la période.

Deux inventaires encadrent une période. Entre les deux, le stock d'un article a
reçu, produit, expédié, consommé et rebuté des quantités qu'on sait chiffrer. La
question est donc fermée : en partant du stock *compté* du premier inventaire et
en appliquant ces flux, retombe-t-on sur le stock *compté* du second ?

Trois règles décident de tout, et elles sont ici parce qu'aucune ne se voit à
l'écran quand elle est fausse :

* **le sens est porté par l'étape, pas par le signe** — une expédition saisie en
  négatif serait ajoutée au lieu d'être retranchée, et le rapport resterait
  parfaitement lisible ;
* **la production ne se somme pas telle quelle** — la table de faits la répète
  sur chaque ligne composant du parent, et la sommer brute la multiplie par la
  taille de la nomenclature ;
* **un article compté d'un seul côté n'est pas un zéro** — c'est un trou dans la
  comparaison, et le lire comme un zéro fabrique un écart de la taille du stock.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import FlowKind
from inventory.domain.models import StockFlowInput, StockFlowLine
from inventory.errors import ValidationError
from inventory.ingest.mappers import map_stock_flow_inputs
from inventory.services.stock_flow_service import (
    StockFlowService,
    _chain,
    _counted_by_item,
    _kpis,
)


def line(
    *,
    opening: float = 0,
    received: float = 0,
    produced: float = 0,
    shipped: float = 0,
    consumed: float = 0,
    scrapped: float = 0,
    closing: float = 0,
    cost: float = 10,
    counted_opening: bool = True,
    counted_closing: bool = True,
    item: str = "ART-1",
) -> StockFlowLine:
    return StockFlowLine(
        item_number=item,
        unit_cost=Decimal(str(cost)),
        opening_qty=Decimal(str(opening)),
        received_qty=Decimal(str(received)),
        produced_qty=Decimal(str(produced)),
        shipped_qty=Decimal(str(shipped)),
        consumed_qty=Decimal(str(consumed)),
        scrapped_qty=Decimal(str(scrapped)),
        closing_qty=Decimal(str(closing)),
        counted_opening=counted_opening,
        counted_closing=counted_closing,
    )


class TestTheChain:
    """Les six termes, dans l'ordre où ils se produisent."""

    def test_the_whole_walk(self):
        # 1000 + 400 + 0 − 0 − 500 − 12 = 888
        assert line(
            opening=1000, received=400, consumed=500, scrapped=12, closing=880
        ).expected_qty == 888

    def test_what_none_of_the_flows_explains(self):
        assert line(
            opening=1000, received=400, consumed=500, scrapped=12, closing=880
        ).variance_qty == -8

    def test_production_adds_and_consumption_subtracts(self):
        """Un sous-ensemble est légitimement les deux à la fois."""
        assert line(opening=50, produced=14, consumed=40).expected_qty == 24

    def test_the_variance_is_valued_at_the_standard_price(self):
        assert line(opening=100, closing=90, cost=45).variance_value == -450

    def test_a_period_with_no_flow_expects_the_opening_stock(self):
        assert line(opening=120, closing=120).expected_qty == 120
        assert line(opening=120, closing=120).variance_qty == 0


class TestTheRelativeVariance:
    def test_it_is_relative_to_what_was_expected(self):
        assert line(opening=100, closing=90).variance_ratio == Decimal("-0.1")

    def test_an_expectation_of_zero_has_no_ratio_rather_than_a_nil_one(self):
        """Attendu à zéro et trouvé à douze : le ratio est indéfini, pas nul.

        Afficher « 0 % » à côté d'un vrai écart est pire que de n'afficher rien.
        """
        assert line(opening=0, closing=12).variance_ratio is None


class TestAnArticleCountedOnOneSideOnly:
    def test_it_is_not_complete(self):
        assert line(opening=100, counted_closing=False).is_complete is False

    def test_it_is_excluded_from_the_totals(self):
        """Une seule référence de ce genre suffit à dominer le total."""
        kpis = _kpis([
            line(opening=100, closing=90),
            line(opening=5000, counted_closing=False, item="ART-2"),
        ])
        assert kpis["completeCount"] == 1
        assert kpis["incompleteCount"] == 1
        assert kpis["netVarianceValue"] == -100

    def test_but_it_is_still_reported(self):
        kpis = _kpis([line(opening=100, counted_opening=False)])
        assert kpis["lineCount"] == 1
        assert kpis["incompleteCount"] == 1

    def test_the_chain_ignores_it_too(self):
        """Sinon le stock attendu du graphique contredirait celui des KPI."""
        chain = _chain([
            line(opening=100, closing=100),
            line(opening=9999, counted_closing=False, item="ART-2"),
        ])
        opening = next(step for step in chain if step["key"] == "opening")
        assert opening["qty"] == 100


class TestTheKpis:
    def test_the_gross_reading_does_not_let_errors_cancel(self):
        kpis = _kpis([
            line(opening=100, closing=110),
            line(opening=100, closing=90, item="ART-2"),
        ])
        assert kpis["netVarianceValue"] == 0
        assert kpis["grossVarianceValue"] == 200

    def test_reliability_is_undefined_rather_than_perfect_on_an_empty_base(self):
        kpis = _kpis([])
        assert kpis["grossReliability"] is None
        assert kpis["netReliability"] is None

    def test_a_perfect_period_scores_one(self):
        kpis = _kpis([line(opening=100, closing=100)])
        assert kpis["grossReliability"] == 1
        assert kpis["matchedCount"] == 1


class TestTheChainTerminals:
    def test_the_two_ends_are_totals_not_movements(self):
        """Ils repartent de la ligne de base ; les six autres s'empilent."""
        chain = _chain([line(opening=1000, received=400, consumed=500, closing=880)])
        terminals = [step for step in chain if step["terminal"]]
        assert [step["key"] for step in terminals] == ["expected", "closing"]
        assert [step["qty"] for step in terminals] == [900, 880]

    def test_the_subtracted_terms_are_carried_negative(self):
        """Le graphique lit une chaîne : les signes y sont déjà appliqués."""
        chain = _chain([line(opening=100, shipped=30, consumed=20, scrapped=5)])
        by_key = {step["key"]: step for step in chain}
        assert by_key["shipped"]["qty"] == -30
        assert by_key["consumed"]["qty"] == -20
        assert by_key["scrapped"]["qty"] == -5
        assert by_key["received"]["qty"] == 0

    def test_the_chain_sums_to_the_expected_stock(self):
        chain = _chain([line(opening=1000, received=400, consumed=500, scrapped=12)])
        movements = sum(
            step["qty"] for step in chain if not step["terminal"]
        )
        expected = next(step for step in chain if step["key"] == "expected")
        assert movements == expected["qty"]


class TestTheDirectionComesFromTheStepNotTheSign:
    def test_a_shipment_typed_negative_is_stored_positive(self):
        """Sinon elle serait ajoutée au stock au lieu d'en être retirée."""
        lines, _ = map_stock_flow_inputs(
            "run-1", [{"item_number": "ART-1", "qty": -70}],
            kind=FlowKind.SHIPMENT,
        )
        assert lines[0].qty == 70

    def test_the_model_refuses_a_negative_quantity_anywhere(self):
        entry = StockFlowInput(
            run_id="run-1", item_number="ART-1", kind=FlowKind.RECEIPT, qty=-5
        )
        assert entry.qty == 5

    def test_duplicates_are_summed(self):
        """Un an de réceptions exporté mois par mois liste douze fois la même
        référence : c'est la forme normale du fichier, pas une anomalie."""
        lines, _ = map_stock_flow_inputs(
            "run-1",
            [{"item_number": "ART-1", "qty": 100}, {"item_number": "ART-1", "qty": 40}],
            kind=FlowKind.RECEIPT,
        )
        assert len(lines) == 1
        assert lines[0].qty == 140

    def test_an_article_outside_the_campaign_is_left_out(self):
        from inventory.domain.models import Item

        lines, _ = map_stock_flow_inputs(
            "run-1",
            [{"item_number": "ART-1", "qty": 5}, {"item_number": "HORS", "qty": 5}],
            kind=FlowKind.RECEIPT,
            items={"ART-1": Item(campaign_id="c", item_number="ART-1")},
        )
        assert [entry.item_number for entry in lines] == ["ART-1"]


class TestTheOpeningStockIsCollapsedToTheArticle:
    """Entre deux inventaires une palette bouge, et un déplacement n'est pas un
    écart : comparer casier par casier en signalerait un à chaque mouvement."""

    def test_two_bins_of_one_article_are_added(self):
        ctx = cast(Any, SimpleNamespace(
            journals=SimpleNamespace(counted_quantities=lambda cid: [
                {"item_number": "ART-1", "warehouse_id": "B06",
                 "location_id": "A", "qty": Decimal("60")},
                {"item_number": "ART-1", "warehouse_id": "B06",
                 "location_id": "B", "qty": Decimal("40")},
            ])
        ))
        assert _counted_by_item(ctx, "camp-1") == {"ART-1": Decimal("100")}

    def test_a_float_quantity_is_read_without_binary_drift(self):
        ctx = cast(Any, SimpleNamespace(
            journals=SimpleNamespace(counted_quantities=lambda cid: [
                {"item_number": "ART-1", "warehouse_id": "B06",
                 "location_id": "A", "qty": 0.1},
                {"item_number": "ART-1", "warehouse_id": "B06",
                 "location_id": "B", "qty": 0.2},
            ])
        ))
        assert _counted_by_item(ctx, "camp-1")["ART-1"] == Decimal("0.3")


class TestTheEarlierCampaignIsTheEarlierOneByCountDate:
    """Jamais par date de création. Des campagnes créées dans un ordre et
    comptées dans l'autre existent, et c'est le comptage qui borne la période."""

    def campaign(self, code: str, count_date: dt.date, created: int) -> Any:
        return cast(Any, SimpleNamespace(
            id=f"id-{code}", code=code, label=code, count_date=count_date,
            status="ANALYSIS", created_at=created,
        ))

    def service(self, *campaigns: Any) -> StockFlowService:
        ctx = SimpleNamespace(
            actor="testeur",
            campaigns=SimpleNamespace(
                list=lambda limit=100: list(campaigns),
                get=lambda cid: next(c for c in campaigns if c.id == cid),
            ),
        )
        return StockFlowService(cast(Any, ctx))

    def test_a_later_campaign_is_not_offered(self):
        now = self.campaign("MAI", dt.date(2026, 6, 29), created=1)
        after = self.campaign("SEPT", dt.date(2026, 9, 28), created=0)
        before = self.campaign("MARS", dt.date(2026, 3, 30), created=2)
        offered = self.service(now, after, before).comparable_campaigns(now)
        assert [c["code"] for c in offered] == ["MARS"]

    def test_they_are_ordered_most_recent_first(self):
        now = self.campaign("JUIN", dt.date(2026, 6, 29), created=0)
        mars = self.campaign("MARS", dt.date(2026, 3, 30), created=1)
        janv = self.campaign("JANV", dt.date(2026, 1, 5), created=2)
        offered = self.service(now, mars, janv).comparable_campaigns(now)
        assert [c["code"] for c in offered] == ["MARS", "JANV"]

    def test_the_week_span_is_reported(self):
        now = self.campaign("JUIN", dt.date(2026, 6, 29), created=0)
        mars = self.campaign("MARS", dt.date(2026, 3, 30), created=1)
        assert self.service(now, mars).comparable_campaigns(now)[0]["weeks"] == 13

    def test_choosing_a_later_campaign_as_baseline_is_refused(self):
        now = self.campaign("MARS", dt.date(2026, 3, 30), created=0)
        after = self.campaign("JUIN", dt.date(2026, 6, 29), created=1)
        service = self.service(now, after)
        with pytest.raises(ValidationError, match="date d'inventaire"):
            service._baseline(now, after.id)

    def test_and_so_is_a_campaign_counted_the_same_day(self):
        now = self.campaign("A", dt.date(2026, 6, 29), created=0)
        twin = self.campaign("B", dt.date(2026, 6, 29), created=1)
        with pytest.raises(ValidationError):
            self.service(now, twin)._baseline(now, twin.id)
