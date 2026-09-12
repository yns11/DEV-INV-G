"""L'ordre des étapes, et ce qu'il empêche.

Trois choses ont été faites en production dans un ordre qui n'a pas de sens, et
aucune n'a rien déclenché : des quantités saisies sur les feuilles GENERIQUE
pendant la préparation, le journal consolidé généré à partir de ces quantités,
et tous les journaux postés avant même que le stock ERP soit chargé. Chacune
produit un travail qui *a l'air* fait.

Ces tests décrivent la règle par ce qu'elle interdit, parce que c'est ce qu'on
attend d'elle.
"""

from __future__ import annotations

import pytest

from inventory.domain.sequence import (
    PREREQUISITES,
    Progress,
    blocking_reason,
    unlocked_aspects,
)

EMPTY = Progress()
ITEMS_ONLY = Progress(items=1200)
READY_TO_COUNT = Progress(items=1200, zones=8)
STOCK_LOADED = Progress(items=1200, zones=8, book_stock_lines=4500)
STOCK_FROZEN = Progress(
    items=1200, zones=8, book_stock_lines=4500, book_stock_frozen=True
)


class TestPreparation:
    """Articles → nomenclatures et feuilles → pilotage."""

    @pytest.mark.parametrize("aspect", ["boms", "zones", "count_sheets", "thresholds"])
    def test_nothing_starts_before_the_article_referential(self, aspect):
        """Tout s'y rattache : une nomenclature, une feuille, un seuil par type."""
        assert "articles" in (blocking_reason(aspect, EMPTY) or "")

    def test_the_referential_itself_is_never_gated(self):
        """Sinon la campagne n'aurait aucune première étape."""
        assert blocking_reason("items", EMPTY) is None

    @pytest.mark.parametrize("aspect", ["boms", "zones", "count_sheets"])
    def test_articles_alone_unlock_the_rest_of_the_referential(self, aspect):
        assert blocking_reason(aspect, ITEMS_ONLY) is None

    def test_pilotage_waits_for_the_sheets_it_assigns(self):
        """Affecter un gestionnaire à des zones qui n'existent pas ne veut rien dire."""
        assert "feuilles" in (blocking_reason("thresholds", ITEMS_ONLY) or "")
        assert blocking_reason("thresholds", READY_TO_COUNT) is None


class TestCounting:
    """Stock ERP → journaux et GENERIQUE."""

    @pytest.mark.parametrize("aspect", ["count_journals", "count_entries"])
    def test_counting_waits_for_the_erp_stock(self, aspect):
        """« Un comptage sans référence ne mesure rien » — c'est littéralement le cas.

        Sans stock ERP, une quantité comptée n'a pas d'écart : elle a une valeur
        et rien à quoi la comparer.
        """
        assert "stock ERP" in (blocking_reason(aspect, READY_TO_COUNT) or "")

    @pytest.mark.parametrize("aspect", ["count_journals", "count_entries"])
    def test_loading_it_is_enough_to_start_counting(self, aspect):
        """Compter n'exige pas le gel : on gèle avant de poster, pas avant de compter."""
        assert blocking_reason(aspect, STOCK_LOADED) is None

    def test_posting_waits_for_the_snapshot_to_stop_moving(self):
        assert "Gelez" in (blocking_reason("post_journal", STOCK_LOADED) or "")

    def test_and_goes_through_once_it_has(self):
        assert blocking_reason("post_journal", STOCK_FROZEN) is None

    def test_the_early_count_does_not_wait_for_a_stock_it_precedes(self):
        """C'est la seule étape du comptage qui passe avant le chargement.

        Un lot avancé se compte des jours avant le jour J et porte sa propre
        référence : la colonne « Stock ERP » des lignes de son journal. Lui
        donner le prérequis des journaux généraux fermait l'écran jusqu'au
        chargement général — donc jusqu'après le moment où il sert.
        """
        assert blocking_reason("early_counts", READY_TO_COUNT) is None
        assert blocking_reason("early_counts", ITEMS_ONLY) is None

    def test_but_it_waits_for_the_articles_it_values_against(self):
        """Ses lignes se rattachent à des articles, et sceller les valorise."""
        assert "articles" in (blocking_reason("early_counts", EMPTY) or "")

    def test_posting_without_any_stock_names_the_first_gap_not_the_last(self):
        """Deux prérequis manquent ; celui à combler est le premier."""
        assert "Chargez" in (blocking_reason("post_journal", READY_TO_COUNT) or "")


class TestWhatTheScreenIsTold:
    def test_every_gated_aspect_is_reported(self):
        """L'écran grise une étape et l'explique — avec la même fonction que le garde."""
        unlocked = unlocked_aspects(EMPTY)
        assert set(unlocked) == set(PREREQUISITES)
        assert not any(unlocked.values())

    def test_a_ready_campaign_has_everything_open(self):
        assert all(unlocked_aspects(STOCK_FROZEN).values())

    def test_an_ungated_aspect_is_simply_absent(self):
        """Ne pas inventer un ordre là où il n'y en a pas."""
        assert "items" not in PREREQUISITES
        assert "analysis" not in PREREQUISITES
