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
    at_standard_price,
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


def precount(item: str, wh: str, loc: str, qty, cost="10") -> BookStockLine:
    """Une ligne de référence posée par le scellement d'un précomptage.

    Ce qui la distingue : `erp_journal_id`. Sa valorisation est le prix standard
    du référentiel, pas le coût que l'ERP portait au gel.
    """
    return BookStockLine(
        campaign_id="c", item_number=item, warehouse_id=wh, location_id=loc,
        qty=qty, unit_cost=cost, erp_journal_id="j-1",
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

    def test_the_standard_price_values_both_sides(self, campaign):
        """`prix standard × quantité`, pour le stock ERP comme pour le comptage.

        Le coût porté par la ligne de stock — celui que l'ERP tenait au gel —
        ne valorise plus rien : les deux côtés de l'écart doivent se mesurer à
        la même base, sans quoi l'écart en euros mélangerait une différence de
        quantité et une différence de méthode.
        """
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("B", "B06", "L1", 10, cost="250")],
            counted=[counted("B", "B06", "L1", 9)],
            items=ITEMS,
        )
        assert lines[0].unit_cost == Decimal("100.00"), "std_price de B"
        assert lines[0].book_value == Decimal("1000.00")
        assert lines[0].physical_value == Decimal("900.00")
        assert lines[0].variance_value == Decimal("-100.00")

    def test_the_line_cost_is_the_fallback_for_an_unknown_article(self, campaign):
        """Mieux vaut la valeur que l'ERP portait que zéro."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("INCONNU", "B06", "L1", 10, cost="250")],
            counted=[],
            items=ITEMS,
        )
        assert lines[0].unit_cost == Decimal("250.00")


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


class TestAnExcludedArticleProducesNoVariance:
    """L'exclusion vaut partout, pas seulement à l'entrée du stock ERP.

    Le cas rencontré en production, capture à l'appui : quatre références
    `SECONDARY SHIM BEARING C2`, exclues du périmètre, affichées dans la liste
    des écarts avec un stock ERP à zéro, un comptage à trente-quatre mille et un
    écart de trente-quatre mille — signalé « au-delà des seuils » et « hors
    ERP », en tête du classement par montant.

    Les deux moitiés de l'exclusion n'avaient pas été appliquées ensemble. La
    ligne de **stock ERP** n'était plus chargée, ce qui met le stock à zéro ; le
    **comptage**, lui, arrivait toujours par les feuilles et les journaux. Le
    résultat était le pire des deux mondes : une exclusion fabriquait
    exactement l'écart qu'elle existe pour éviter, et systématiquement matériel,
    puisqu'un écart sans stock ERP en face l'est toujours.

    `in_perimeter` énonce déjà la règle pour les lectures ERP — « un article
    délibérément laissé hors du périmètre ne doit pas revenir par les quantités
    relevées dessus ». Ces contrôles la tiennent là où elle manquait.
    """

    def excluded(self, *numbers: str) -> dict[str, Item]:
        from inventory.domain.enums import ExclusionScope

        return {
            **ITEMS,
            **{
                n: Item(
                    campaign_id="c", item_number=n, item_type=ItemType.COMPONENT,
                    std_price="10", exclusions={ExclusionScope.ALL},
                )
                for n in numbers
            },
        }

    def test_compte_sans_stock_erp_il_ne_produit_plus_d_ecart(self, campaign):
        """Le cas de la capture, à l'identique."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[],
            counted=[counted("X", "B06", "GENERIQUE", 33980)],
            items=self.excluded("X"),
        )

        assert lines == []

    def test_il_n_apparait_pas_davantage_avec_du_stock_erp(self, campaign):
        """Un article exclu dont le stock aurait été chargé avant l'exclusion."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("X", "B06", "L1", 100)],
            counted=[counted("X", "B06", "L1", 40)],
            items=self.excluded("X"),
        )

        assert lines == []

    def test_un_ajustement_ne_le_ramene_pas_non_plus(self, campaign):
        """Les trois sources d'une ligne d'écart, et non deux sur trois."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[],
            counted=[],
            adjustments=[
                AdjustmentLine(
                    id="adj-1", campaign_id="c", item_number="X",
                    warehouse_id="B06", location_id="L1", qty=Decimal("7"),
                )
            ],
            items=self.excluded("X"),
        )

        assert lines == []

    def test_les_articles_du_perimetre_restent_intacts(self, campaign):
        """La garde ne doit pas emporter ce qu'on inventorie."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[counted("A", "B06", "L1", 90), counted("X", "B06", "L1", 500)],
            items=self.excluded("X"),
        )

        assert [line.item_number for line in lines] == ["A"]
        assert lines[0].variance_qty == -10

    def test_une_exclusion_generique_seule_ne_retire_rien(self, campaign):
        """Elle ne porte que sur la consolidation des zones.

        L'article reste inventorié ailleurs — dans un journal d'emplacement —
        et son écart y est parfaitement légitime. Confondre les deux portées
        ferait disparaître des écarts que personne n'a demandé d'exclure.
        """
        from inventory.domain.enums import ExclusionScope

        items = {
            **ITEMS,
            "G": Item(
                campaign_id="c", item_number="G", item_type=ItemType.COMPONENT,
                std_price="10", exclusions={ExclusionScope.GENERIC},
            ),
        }

        lines = build_variances(
            campaign=campaign,
            book_stock=[book("G", "B06", "L1", 100)],
            counted=[counted("G", "B06", "L1", 90)],
            items=items,
        )

        assert [line.item_number for line in lines] == ["G"]

    def test_un_article_inconnu_du_referentiel_reste_signale(self, campaign):
        """Il n'est pas exclu : il manque. C'est un constat, pas une décision,
        et le faire disparaître avec les exclusions le rendrait invisible."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[],
            counted=[counted("INCONNU", "B06", "L1", 12)],
            items=ITEMS,
        )

        assert [line.item_number for line in lines] == ["INCONNU"]
        assert lines[0].counted_only is True

    def test_le_total_ne_porte_plus_leur_montant(self, campaign):
        """Le chiffre du bas de l'écran, celui qu'on lit en réunion."""
        lines = build_variances(
            campaign=campaign,
            book_stock=[book("A", "B06", "L1", 100)],
            counted=[counted("A", "B06", "L1", 100), counted("X", "B06", "L1", 33980)],
            items=self.excluded("X"),
        )

        assert sum(line.variance_value for line in lines) == 0


class TestTheStockHasTwoOrigins:
    """Le snapshot général et la référence d'un emplacement précompté.

    Depuis les comptages avancés, `book_stock` porte les deux dans la même
    table, et elles ne portent pas le même coût : le snapshot porte celui que
    l'ERP tenait au gel, le précomptage porte le prix standard.

    Aucune des deux ne valorise la campagne. La base est **le prix standard du
    référentiel, partout et des deux côtés** — c'est ce qui rend le stock ERP et
    le stock compté comparables, et ce qui met le total à l'abri de l'ordre des
    lignes.
    """

    def _lines(self, camp, book_stock):
        return build_variances(
            campaign=camp, book_stock=book_stock,
            counted=[], items=ITEMS, granularity="item",
        )

    def test_both_origins_are_valued_at_the_standard_price(self, campaign):
        [line] = self._lines(campaign, [
            precount("A", "ATP", "SOL", Decimal(10), cost="4"),
            book("A", "B06", "AUTRE", Decimal(100), cost="9"),
        ])
        assert line.book_qty == Decimal(110), "les quantités s'additionnent"
        assert line.unit_cost == Decimal(10), "std_price de A, et rien d'autre"
        assert line.book_value == Decimal(1100)

    def test_the_total_does_not_depend_on_the_order_of_the_lines(self, campaign):
        """Sans cela, un VACUUM suffisait à changer un chiffre signé."""
        [first] = self._lines(campaign, [
            precount("A", "ATP", "SOL", Decimal(10), cost="4"),
            book("A", "B06", "AUTRE", Decimal(100), cost="9"),
        ])
        [second] = self._lines(campaign, [
            book("A", "B06", "AUTRE", Decimal(100), cost="9"),
            precount("A", "ATP", "SOL", Decimal(10), cost="4"),
        ])
        assert first.book_value == second.book_value == Decimal(1100)

    def test_loading_the_general_stock_does_not_move_the_valuation(self, campaign):
        """Avant le chargement général, un précomptage vaut déjà son prix.

        Il valait le prix standard puis, au chargement, celui du snapshot : le
        total bougeait sans qu'aucune quantité n'ait changé.
        """
        [alone] = self._lines(
            campaign, [precount("A", "ATP", "SOL", Decimal(10), cost="4")]
        )
        assert alone.unit_cost == Decimal(10)
        assert alone.book_value == Decimal(100)


class TestValuingBookStockLines:
    """La même règle pour les écrans qui affichent les lignes de stock.

    La grille Stock ERP, son total, l'export Excel et la liste des articles non
    comptés lisaient le coût porté par la ligne. Sur les mêmes lignes, ils
    valorisaient donc autrement que les écarts et les KPI — et le total de la
    grille ne tombait pas sur celui du carrousel.
    """

    def test_the_referential_price_replaces_the_line_cost(self):
        [line] = at_standard_price([book("A", "B06", "L1", 10, cost="250")], ITEMS)
        assert line.unit_cost == Decimal(10)
        assert line.value == Decimal(100)

    def test_a_precount_line_is_valued_the_same_way(self):
        [line] = at_standard_price(
            [precount("A", "ATP", "SOL", Decimal(10), cost="4")], ITEMS
        )
        assert line.unit_cost == Decimal(10)

    def test_an_unknown_article_keeps_what_the_erp_carried(self):
        [line] = at_standard_price(
            [book("INCONNU", "B06", "L1", 10, cost="250")], ITEMS
        )
        assert line.unit_cost == Decimal(250)

    def test_and_so_does_an_article_priced_at_zero(self):
        """Un prix standard manquant ne doit pas effacer une valeur connue."""
        items = {**ITEMS, "Z": Item(campaign_id="c", item_number="Z", std_price="0")}
        [line] = at_standard_price([book("Z", "B06", "L1", 10, cost="250")], items)
        assert line.unit_cost == Decimal(250)

    def test_the_quantities_are_left_alone(self):
        """Cette fonction valorise ; elle ne touche à aucune quantité."""
        lines = at_standard_price([book("A", "B06", "L1", 10, cost="250")], ITEMS)
        assert lines[0].qty == Decimal(10)
