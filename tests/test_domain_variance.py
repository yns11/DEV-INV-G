"""Variance reconciliation, materiality and inventory KPIs."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from inventory.domain.enums import ItemType, LocationStatus
from inventory.domain.models import (
    AdjustmentLine,
    BookStockLine,
    Campaign,
    Item,
    Location,
    LocationKey,
    Thresholds,
    VarianceLine,
)
from inventory.domain.variance import (
    CountedQty,
    aggregate_by,
    build_variances,
    compute_kpis,
    is_material,
    pareto,
)


@pytest.fixture
def campaign() -> Campaign:
    return Campaign(
        id="c",
        code="INV-TEST",
        label="Test",
        count_date=dt.date(2026, 6, 13),
        created_by="tester",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        thresholds=[
            Thresholds(
                item_type=ItemType.COMPONENT,
                value_abs_eur="1000",
                qty_relative="0.02",
            )
        ],
    )


ITEMS = {
    "A": Item(campaign_id="c", item_number="A", item_type=ItemType.COMPONENT,
              std_price="10"),
    "B": Item(campaign_id="c", item_number="B", item_type=ItemType.COMPONENT,
              std_price="100"),
}


def book(item: str, wh: str, loc: str, qty, cost="10") -> BookStockLine:
    return BookStockLine(
        campaign_id="c", item_number=item, warehouse_id=wh, location_id=loc,
        qty=qty, unit_cost=cost,
    )


def counted(item: str, wh: str, loc: str, qty) -> CountedQty:
    return CountedQty(item_number=item, warehouse_id=wh, location_id=loc,
                      qty=Decimal(str(qty)))


class TestReconciliation:
    def test_matching_stock_produces_a_zero_variance(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[counted("A", "B06", "L1", 100)],
            items=ITEMS,
        )
        assert len(lines) == 1
        assert lines[0].variance_qty == 0
        assert lines[0].variance_value == 0

    def test_shortage_is_negative(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[counted("A", "B06", "L1", 80)],
            items=ITEMS,
        )
        assert lines[0].variance_qty == Decimal("-20.000000")
        assert lines[0].variance_value == Decimal("-200.00")

    def test_book_stock_never_counted_is_surfaced(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[],
            items=ITEMS,
        )
        assert lines[0].book_only is True
        assert lines[0].variance_qty == Decimal("-100.000000")

    def test_counted_without_book_stock_is_surfaced(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[],
            counted=[counted("A", "B06", "L1", 30)],
            items=ITEMS,
        )
        assert lines[0].counted_only is True
        assert lines[0].variance_qty == Decimal("30.000000")

    def test_item_granularity_collapses_a_transfer_between_bins(self, campaign):
        """Moving stock between two bins is not a variance."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100), book("A", "B06", "L2", 0)],
            counted=[counted("A", "B06", "L1", 0), counted("A", "B06", "L2", 100)],
            items=ITEMS,
            granularity="item",
        )
        assert len(lines) == 1
        assert lines[0].variance_qty == 0

    def test_location_granularity_keeps_the_transfer_visible(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100), book("A", "B06", "L2", 0)],
            counted=[counted("A", "B06", "L1", 0), counted("A", "B06", "L2", 100)],
            items=ITEMS,
            granularity="item_location",
        )
        assert len(lines) == 2
        assert {l.variance_qty for l in lines} == {
            Decimal("-100.000000"), Decimal("100.000000")
        }

    def test_disabled_locations_leave_the_perimeter_entirely(self, campaign):
        locations = {
            LocationKey(warehouse_id="B06", location_id="L1"): Location(
                campaign_id="c", warehouse_id="B06", location_id="L1"
            ),
            LocationKey(warehouse_id="B06", location_id="OFF"): Location(
                campaign_id="c", warehouse_id="B06", location_id="OFF",
                status=LocationStatus.DISABLED,
            ),
        }
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100), book("A", "B06", "OFF", 999)],
            counted=[counted("A", "B06", "L1", 100)],
            items=ITEMS,
            locations=locations,
            granularity="item_location",
        )
        assert len(lines) == 1
        assert lines[0].location_id == "L1"

    def test_an_adjustment_moves_the_physical_stock_and_the_variance_with_it(
        self, campaign
    ):
        """Un ajustement est un *mouvement de stock*, pas une correction d'écart.

        Posté après le comptage, il change ce qu'il y a sur l'étagère : le
        comptage seul cesse d'être l'image courante, et c'est le stock physique
        — compté plus mouvements — que l'écart mesure désormais.
        """
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[counted("A", "B06", "L1", 80)],
            items=ITEMS,
            adjustments=[
                AdjustmentLine(id="x", campaign_id="c", item_number="A",
                               warehouse_id="B06", location_id="L1", qty="-20")
            ],
            granularity="item_location",
        )
        assert lines[0].physical_qty == Decimal("60.000000")
        assert lines[0].variance_qty == Decimal("-40.000000")
        # Ce que le comptage seul montrait reste lisible à côté : la différence
        # entre les deux est exactement ce que l'ajustement a fait.
        assert lines[0].counted_variance_qty == Decimal("-20.000000")

    def test_without_an_adjustment_the_two_readings_coincide(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[counted("A", "B06", "L1", 80)],
            items=ITEMS,
            granularity="item_location",
        )
        assert lines[0].physical_qty == lines[0].counted_qty
        assert lines[0].variance_qty == lines[0].counted_variance_qty

    def test_snapshot_cost_wins_over_the_referential_price(self, campaign):
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("B", "B06", "L1", 10, cost="250")],
            counted=[counted("B", "B06", "L1", 9)],
            items=ITEMS,
        )
        assert lines[0].unit_cost == Decimal("250.00")
        assert lines[0].variance_value == Decimal("-250.00")


class TestMateriality:
    def _line(self, book_qty, counted_qty, cost="10") -> VarianceLine:
        return VarianceLine(
            campaign_id="c", item_number="A", item_type=ItemType.COMPONENT,
            unit_cost=cost, book_qty=book_qty, counted_qty=counted_qty,
        )

    def test_zero_variance_is_never_material(self, campaign):
        t = campaign.threshold_for(ItemType.COMPONENT)
        assert is_material(self._line(100, 100), t) is False

    def test_small_value_is_not_material(self, campaign):
        t = campaign.threshold_for(ItemType.COMPONENT)
        # 10 units × 10 € = 100 € < 1 000 € gate
        assert is_material(self._line(1000, 990), t) is False

    def test_large_value_but_small_ratio_is_not_material(self, campaign):
        t = campaign.threshold_for(ItemType.COMPONENT)
        # 500 € breach in value but only 0.5 % of the book: below the ratio gate
        assert is_material(self._line(100_000, 99_800), t) is False

    def test_breaching_every_gate_is_material(self, campaign):
        t = campaign.threshold_for(ItemType.COMPONENT)
        # 200 units × 10 € = 2 000 € and 20 % of a 1 000-unit book
        assert is_material(self._line(1000, 800), t) is True

    def test_stock_the_erp_does_not_know_about_is_always_material(self, campaign):
        t = campaign.threshold_for(ItemType.COMPONENT)
        assert is_material(self._line(0, 1), t) is True


class TestKpis:
    def test_net_and_gross_differ_when_variances_offset(self, campaign):
        lines = [
            VarianceLine(campaign_id="c", item_number="A", unit_cost="10",
                         book_qty=100, counted_qty=110),
            VarianceLine(campaign_id="c", item_number="B", unit_cost="10",
                         book_qty=100, counted_qty=90),
        ]
        kpi = compute_kpis(lines, campaign=campaign)
        assert kpi.net_variance_value == 0           # +100 € and −100 € cancel
        assert kpi.gross_variance_value == Decimal("200.00")  # two errors, not zero
        assert kpi.net_reliability_value == Decimal("1")
        assert kpi.gross_reliability_value == Decimal("0.9")

    def test_ira_counts_the_records_that_matched_exactly(self, campaign):
        lines = [
            VarianceLine(campaign_id="c", item_number="A",
                         item_type=ItemType.COMPONENT, unit_cost="10",
                         book_qty=1000, counted_qty=1000),
            VarianceLine(campaign_id="c", item_number="B",
                         item_type=ItemType.COMPONENT, unit_cost="10",
                         book_qty=1000, counted_qty=900),
        ]
        kpi = compute_kpis(lines, campaign=campaign)
        assert kpi.accurate_line_count == 1
        assert kpi.ira == Decimal("0.5")

    def test_a_record_off_by_one_is_not_accurate(self, campaign):
        """There is no tolerance dial any more, and that is the point.

        A configurable tolerance made the indicator agree with whoever set it
        rather than with the shelf: raise it a little and accuracy improves
        without a single part moving.
        """
        lines = [
            VarianceLine(campaign_id="c", item_number="A",
                         item_type=ItemType.COMPONENT, unit_cost="10",
                         book_qty=1000, counted_qty=999),
        ]
        assert compute_kpis(lines, campaign=campaign).accurate_line_count == 0

    def test_empty_input_yields_none_rather_than_zero(self):
        kpi = compute_kpis([])
        assert kpi.gross_reliability_value is None
        assert kpi.ira is None

    def test_zero_book_stock_yields_no_ratio(self):
        kpi = compute_kpis(
            [VarianceLine(campaign_id="c", item_number="A", unit_cost="10",
                          book_qty=0, counted_qty=5)]
        )
        assert kpi.gross_reliability_value is None
        assert kpi.counted_only_count == 0  # flag set by build_variances, not here


class TestAggregation:
    def test_groups_and_sorts_by_absolute_impact(self, campaign):
        lines = [
            VarianceLine(campaign_id="c", item_number="A", category="STATOR",
                         unit_cost="10", book_qty=100, counted_qty=90),
            VarianceLine(campaign_id="c", item_number="B", category="ROTOR",
                         unit_cost="10", book_qty=100, counted_qty=50),
        ]
        groups = aggregate_by(lines, "category", campaign=campaign)
        assert [g.key for g in groups] == ["ROTOR", "STATOR"]
        assert groups[0].abs_variance_value == Decimal("500.00")

    def test_unknown_dimension_is_rejected(self):
        with pytest.raises(ValueError, match="unknown aggregation dimension"):
            aggregate_by([], "couleur")

    def test_pareto_returns_the_shortest_covering_set(self, campaign):
        lines = [
            VarianceLine(campaign_id="c", item_number=f"I{i}", unit_cost="1",
                         book_qty=0, counted_qty=qty)
            for i, qty in enumerate([80, 10, 5, 3, 2])
        ]
        groups = aggregate_by(lines, "item", campaign=campaign)
        head = pareto(groups, coverage=Decimal("0.8"))
        assert [g.key for g in head] == ["I0"]
