"""L'écart backflush, et ce qu'il explique d'un écart d'inventaire.

La production ne saisit pas ses sorties de composants ligne à ligne : elle les
déduit de sa déclaration, selon la nomenclature. L'écart backflush mesure
exactement l'hypothèse que fait cette déduction — théorique moins réel — et son
*signe* dit dans quel sens le stock système a dérivé.

Les deux conventions sont des miroirs l'une de l'autre, et c'est là que tout se
joue. L'inventaire lit « compté − ERP » ; le backflush lit « théorique − réel ».
Un écart backflush positif — le backflush a déduit moins que le théorique — veut
donc dire que le stock système est surévalué, et qu'on comptera *moins* que ce
que l'ERP annonce. D'où le changement de signe, fait à un seul endroit.

Ces tests épinglent les trois cas chiffrés du guide, parce que ce sont eux qui
disent si la soustraction est dans le bon sens — et parce qu'une erreur de signe
ici est invisible : elle produit des nombres parfaitement plausibles.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import ItemType
from inventory.domain.models import BackflushLine, BookStockLine, Item, VarianceLine
from inventory.domain.variance import CountedQty, build_variances, compute_kpis
from inventory.errors import ValidationError
from inventory.ingest.erp import validate_period
from inventory.ingest.mappers import map_backflush
from inventory.services.import_service import monday_of, suggested_period

CAMPAIGN = cast(Any, SimpleNamespace(id="camp-1"))
START, END = dt.date(2026, 3, 30), dt.date(2026, 6, 29)


def variance(book: float, counted: float, backflush: float | None) -> VarianceLine:
    return VarianceLine(
        campaign_id="camp-1",
        item_number="ART-1",
        unit_cost=Decimal("10"),
        book_qty=Decimal(str(book)),
        counted_qty=Decimal(str(counted)),
        backflush_qty=Decimal(str(backflush or 0)),
        backflush_measured=backflush is not None,
    )


class TestTheThreeWorkedExamplesOfTheGuide:
    """Les chiffres du guide, repris tels quels. Ce sont eux qui font foi."""

    @pytest.mark.parametrize(
        "label,book,counted,backflush,unexplained,rate",
        [
            # L'inventaire trouve 50 de moins ; le backflush en explique 42.
            ("expliqué", 1200, 1150, 42, -8, Decimal("0.84")),
            # Le backflush annonce 26 de non-consommation, l'inventaire n'en
            # trouve que 10 : il sur-explique, et le taux passe sous zéro.
            ("sur-expliqué", 800, 790, 26, 16, Decimal("-0.6")),
            # La surconsommation aurait dû faire trouver *plus* de stock ; on en
            # trouve moins. Les deux anomalies s'additionnent.
            ("aggravé", 1200, 1150, -30, -80, Decimal("-0.6")),
        ],
    )
    def test_the_figures_reproduce(
        self, label, book, counted, backflush, unexplained, rate
    ):
        line = variance(book, counted, backflush)
        assert line.unexplained_qty == unexplained
        assert line.explanation_rate == pytest.approx(rate, abs=Decimal("0.005"))

    def test_a_reference_the_backflush_never_measured_has_no_rate(self):
        """« Non mesuré » n'est pas « expliqué à 0 % » : c'est une absence."""
        line = variance(640, 618, None)
        assert line.backflush_measured is False
        assert line.backflush_share_qty == 0
        # L'inexpliqué vaut alors l'écart entier, ce qui est la vérité : rien ne
        # l'explique.
        assert line.unexplained_qty == line.variance_qty


class TestTheSignChangesExactlyOnce:
    def test_a_positive_backflush_predicts_a_negative_inventory_variance(self):
        """Le backflush a déduit moins que le théorique : il manquera du stock."""
        line = variance(1000, 1000, 42)
        assert line.backflush_share_qty == -42

    def test_and_a_negative_one_the_opposite(self):
        line = variance(1000, 1000, -42)
        assert line.backflush_share_qty == 42

    def test_the_share_is_valued_at_the_line_cost(self):
        line = variance(1000, 1000, 42)
        assert line.backflush_share_value == -420

    def test_an_exactly_explained_variance_leaves_nothing(self):
        line = variance(1000, 958, 42)
        assert line.unexplained_qty == 0
        assert line.explanation_rate == 1


class TestTheRateIsAReductionOfTheGap:
    """Et non une part, qui dépasserait 100 % dès la première sur-explication."""

    def test_a_nil_variance_has_no_rate_rather_than_a_full_one(self):
        """Une part de rien n'est pas totale, elle est indéfinie."""
        assert variance(500, 500, 0).explanation_rate is None

    def test_it_is_not_floored_at_zero(self):
        """Un taux négatif est un constat, pas un défaut de la formule."""
        assert variance(1200, 1150, -30).explanation_rate < 0

    def test_a_backflush_that_brings_nothing_scores_zero(self):
        assert variance(1000, 950, 0).explanation_rate == 0


class TestItIsReadAgainstTheAdjustedVariance:
    """L'inexpliqué porte sur l'écart où l'on se trouve, pas sur un état révolu.

    Un ajustement est un mouvement de stock : il déplace le physique, donc
    l'écart, donc ce qui reste à expliquer. Le mesurer sur le comptage seul
    répondrait à propos d'un stock que plus personne n'a devant lui.
    """

    def test_an_adjustment_moves_the_unexplained_with_the_variance(self):
        line = variance(1000, 950, 42)
        line.adjusted_qty = Decimal("-50")
        assert line.variance_qty == -100
        # L'écart s'est creusé de 50 ; le backflush en explique toujours 42.
        assert line.unexplained_qty == -58

    def test_the_backflush_does_not_move_the_stock_it_explains(self):
        """Il explique un écart, il ne le corrige pas : rien ne bouge sur l'étagère."""
        line = variance(1000, 950, 42)
        assert line.physical_qty == line.counted_qty
        assert line.variance_qty == -50


class TestCarryingItOntoTheVarianceLines:
    def items() -> dict[str, Item]:  # type: ignore[misc]
        return {
            "ART-1": Item(
                campaign_id="camp-1", item_number="ART-1",
                item_type=ItemType.COMPONENT, std_price=Decimal("10"),
            )
        }

    def build(self, granularity: str, backflush: dict[str, BackflushLine] | None):
        return build_variances(
            campaign=CAMPAIGN,
            book_stock=[
                BookStockLine(
                    campaign_id="camp-1", item_number="ART-1", warehouse_id="B06",
                    location_id="A", qty=Decimal("60"), unit_cost=Decimal("10"),
                ),
                BookStockLine(
                    campaign_id="camp-1", item_number="ART-1", warehouse_id="B06",
                    location_id="B", qty=Decimal("40"), unit_cost=Decimal("10"),
                ),
            ],
            counted=[
                CountedQty("ART-1", "B06", "A", Decimal("50")),
                CountedQty("ART-1", "B06", "B", Decimal("40")),
            ],
            items=TestCarryingItOntoTheVarianceLines.items(),
            backflush=backflush,
            granularity=granularity,
        )

    def line(self) -> BackflushLine:
        return BackflushLine(
            campaign_id="camp-1", item_number="ART-1",
            period_start=START, period_end=END, net_qty=Decimal("10"),
        )

    def test_the_article_view_carries_it(self):
        lines = self.build("item", {"ART-1": self.line()})
        assert lines[0].backflush_qty == 10
        assert lines[0].backflush_measured is True

    def test_the_per_location_view_does_not(self):
        """La production consomme depuis une ligne, pas depuis un casier.

        L'étaler sur les emplacements inventerait une répartition que la source
        n'a jamais eue — et la somme des parts vaudrait le double de l'écart.
        """
        lines = self.build("item_location", {"ART-1": self.line()})
        assert [float(l.backflush_qty) for l in lines] == [0.0, 0.0]
        assert not any(l.backflush_measured for l in lines)

    def test_an_article_the_backflush_ignores_is_marked_unmeasured(self):
        lines = self.build("item", {})
        assert lines[0].backflush_measured is False


class TestTheKpis:
    def kpis(self, *lines: VarianceLine):
        return compute_kpis(list(lines))

    def test_the_rate_is_computed_over_the_measured_articles_only(self):
        """Y mêler ceux que la production n'a jamais touchés diluerait le taux.

        Une bonne explication passerait pour une mauvaise, et le chiffre
        tomberait mécaniquement à mesure que le référentiel grossit.
        """
        kpis = self.kpis(
            variance(1000, 958, 42),   # expliqué à 100 %
            variance(500, 400, None),  # jamais mesuré : hors du ratio
        )
        assert kpis.backflush_line_count == 1
        assert kpis.backflush_explanation_rate == 1

    def test_it_stays_negative_when_the_backflush_widens_the_gap(self):
        kpis = self.kpis(variance(1200, 1150, -30))
        assert kpis.backflush_explanation_rate < 0

    def test_no_measured_article_gives_no_rate(self):
        assert self.kpis(variance(500, 400, None)).backflush_explanation_rate is None

    def test_the_shares_and_the_unexplained_are_valued(self):
        kpis = self.kpis(variance(1000, 958, 42))
        assert kpis.backflush_share_value == -420
        assert kpis.unexplained_value == 0

    def test_the_three_figures_share_one_population(self):
        """Écart − part = inexpliqué, sur le même ensemble d'articles.

        Le KPI d'écart porté à côté de la part doit être celui des articles
        *mesurés*, pas celui de toute la campagne : sinon l'écran affiche un
        total sur un ensemble à côté de deux totaux sur un autre, et la
        soustraction qu'il donne à lire ne tombe pas.
        """
        kpis = self.kpis(
            variance(1000, 958, 42),    # mesuré
            variance(2000, 1000, None), # jamais mesuré : hors des trois cartes
        )
        assert kpis.backflush_variance_value == -420
        assert (
            kpis.backflush_variance_value - kpis.backflush_share_value
            == kpis.unexplained_value
        )
        # Et l'écart global, lui, reste celui de toute la campagne.
        assert kpis.net_variance_value == -10420



class TestThePeriodIsAPairOfIsoMondays:
    def test_a_midweek_bound_is_refused_rather_than_snapped(self):
        """Une période élargie de quatre jours en silence produirait un chiffre
        dont l'en-tête dit une chose et la valeur une autre."""
        with pytest.raises(ValidationError):
            validate_period(dt.date(2026, 4, 1), END)

    def test_the_end_must_follow_the_start(self):
        with pytest.raises(ValidationError):
            validate_period(END, START)

    def test_one_week_is_two_consecutive_mondays(self):
        validate_period(dt.date(2026, 3, 30), dt.date(2026, 4, 6))

    def test_the_same_monday_twice_is_an_empty_period(self):
        with pytest.raises(ValidationError):
            validate_period(START, START)


class TestTheSuggestedPeriod:
    def test_the_end_excludes_the_counting_week(self):
        """La semaine du comptage est coupée en deux par le comptage lui-même.

        Lui imputer une production entière surévaluerait l'écart sur tout ce qui
        a été fabriqué cette semaine-là.
        """
        _, end = suggested_period(dt.date(2026, 7, 1))  # un mercredi
        assert end == dt.date(2026, 6, 29)
        assert end.weekday() == 0

    def test_it_starts_at_the_previous_campaign_when_there_is_one(self):
        start, _ = suggested_period(
            dt.date(2026, 6, 29), previous=dt.date(2026, 3, 31)
        )
        assert start == dt.date(2026, 3, 30)

    def test_and_at_a_quarter_otherwise(self):
        start, end = suggested_period(dt.date(2026, 6, 29))
        assert (end - start).days == 13 * 7

    def test_a_previous_campaign_in_the_same_week_falls_back(self):
        """Sinon la période serait vide, et l'écran proposerait l'impossible."""
        start, end = suggested_period(
            dt.date(2026, 6, 29), previous=dt.date(2026, 7, 1)
        )
        assert start < end

    def test_monday_of_is_idempotent(self):
        assert monday_of(monday_of(dt.date(2026, 7, 1))) == dt.date(2026, 6, 29)


class TestMappingTheLoadedRows:
    def rows(self, *rows):
        return map_backflush(
            "camp-1", list(rows), period_start=START, period_end=END,
        )

    def test_a_wholly_empty_row_is_dropped(self):
        """L'absence de donnée vaut écart nul : la stocker n'ajoute qu'une ligne."""
        lines, errors = self.rows({"item_number": "ART-1", "net_qty": 0})
        assert lines == []
        assert errors == []

    def test_a_measured_zero_is_kept(self):
        """« Mesuré, et ça tombe à zéro » n'est pas « pas de donnée »."""
        lines, _ = self.rows(
            {"item_number": "ART-1", "net_qty": 0, "week_count": 13}
        )
        assert len(lines) == 1

    def test_duplicates_are_summed_not_overwritten(self):
        """Un export découpé par parent liste deux fois le même composant."""
        lines, _ = self.rows(
            {"item_number": "ART-1", "net_qty": 30, "theoretical_qty": 100},
            {"item_number": "ART-1", "net_qty": 12, "theoretical_qty": 50},
        )
        assert len(lines) == 1
        assert lines[0].net_qty == 42
        assert lines[0].theoretical_qty == 150

    def test_the_week_count_is_taken_at_its_maximum_not_added(self):
        """Deux fois treize semaines font treize semaines, pas vingt-six."""
        lines, _ = self.rows(
            {"item_number": "ART-1", "net_qty": 1, "week_count": 13},
            {"item_number": "ART-1", "net_qty": 1, "week_count": 13},
        )
        assert lines[0].week_count == 13

    def test_the_bounds_travel_with_every_line(self):
        lines, _ = self.rows({"item_number": "ART-1", "net_qty": 5})
        assert (lines[0].period_start, lines[0].period_end) == (START, END)

    def test_an_article_outside_the_campaign_is_left_out(self):
        lines, _ = map_backflush(
            "camp-1",
            [{"item_number": "ART-9", "net_qty": 999}],
            period_start=START, period_end=END,
            items=TestCarryingItOntoTheVarianceLines.items(),
        )
        assert lines == []

    def test_the_article_number_is_normalised(self):
        lines, _ = self.rows({"item_number": " art-1 ", "net_qty": 5})
        assert lines[0].item_number == "ART-1"


class TestWhatClosureFreezes:
    """La clôture ne fige pas les deux chantiers pour la même raison.

    L'écart backflush entre dans l'écart d'inventaire de la campagne : un
    contrôleur qui a signé un chiffre doit être certain qu'il ne bougera plus,
    et le guide le dit explicitement.

    La réconciliation entre deux campagnes, non. Elle n'écrit que dans ses
    propres tables et lit le stock compté de deux campagnes sans toucher ni à
    l'une ni à l'autre. Et le moment utile est précisément celui-là : on compare
    deux inventaires une fois qu'ils sont terminés. La figer à la clôture
    interdisait l'usage principal de la fonction.
    """

    def editable(self, status):
        from inventory.domain.workflow import mutability_of

        return mutability_of(status)

    def test_the_backflush_is_frozen_once_the_campaign_is_closed(self):
        from inventory.domain.enums import CampaignStatus

        assert self.editable(CampaignStatus.CLOSED).backflush is False

    def test_the_comparison_stays_open_after_closure(self):
        from inventory.domain.enums import CampaignStatus

        assert self.editable(CampaignStatus.CLOSED).stock_flow is True

    def test_both_are_open_while_the_campaign_is(self):
        from inventory.domain.enums import CampaignStatus

        for status in (
            CampaignStatus.PREPARATION,
            CampaignStatus.COUNTING,
            CampaignStatus.ANALYSIS,
        ):
            editable = self.editable(status)
            assert editable.backflush is True, status
            assert editable.stock_flow is True, status

    def test_the_interface_is_told_about_both(self):
        """La barre latérale et l'API lisent la même charge utile."""
        from inventory.domain.enums import CampaignStatus

        payload = self.editable(CampaignStatus.CLOSED).as_dict()
        assert payload["backflush"] is False
        assert payload["stockFlow"] is True
