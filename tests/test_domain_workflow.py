"""Campaign and zone state machines, and the freeze matrix."""

from __future__ import annotations

import datetime as dt

import pytest

from inventory.domain.enums import (
    CampaignStatus,
    JournalStatus,
    ZoneStatus,
)
from inventory.domain.models import ArbitrationLine
from inventory.domain.workflow import (
    arbitration_required,
    assert_campaign_transition,
    campaign_transition_blockers,
    derive_zone_status,
    mutability_of,
    zone_closure_blockers,
)
from inventory.errors import WorkflowError


class TestCampaignTransitions:
    def test_forward_path_is_allowed(self):
        assert_campaign_transition(CampaignStatus.PREPARATION, CampaignStatus.COUNTING)
        assert_campaign_transition(CampaignStatus.COUNTING, CampaignStatus.ANALYSIS)
        assert_campaign_transition(CampaignStatus.ANALYSIS, CampaignStatus.CLOSED)

    def test_skipping_a_phase_is_refused(self):
        with pytest.raises(WorkflowError):
            assert_campaign_transition(
                CampaignStatus.PREPARATION, CampaignStatus.ANALYSIS
            )

    def test_going_backwards_is_refused(self):
        with pytest.raises(WorkflowError):
            assert_campaign_transition(
                CampaignStatus.ANALYSIS, CampaignStatus.COUNTING
            )

    def test_a_closed_campaign_cannot_be_reopened(self):
        with pytest.raises(WorkflowError):
            assert_campaign_transition(CampaignStatus.CLOSED, CampaignStatus.ANALYSIS)


class TestFreezeMatrix:
    def test_preparation_allows_referentials_but_not_counting(self):
        editable = mutability_of(CampaignStatus.PREPARATION)
        assert editable.items and editable.boms and editable.thresholds
        assert not editable.count_journals and not editable.adjustments

    def test_counting_freezes_referentials_but_keeps_zones_open(self):
        """The spec explicitly keeps GENERIQUE sheets creatable during counting."""
        editable = mutability_of(CampaignStatus.COUNTING)
        assert not editable.items and not editable.boms and not editable.thresholds
        assert editable.zones and editable.count_sheets and editable.count_journals
        assert editable.book_stock

    def test_analysis_freezes_counting_and_opens_adjustments(self):
        editable = mutability_of(CampaignStatus.ANALYSIS)
        assert not editable.count_journals and not editable.count_sheets
        assert not editable.book_stock and not editable.locations
        assert editable.adjustments and editable.analysis

    def test_closed_freezes_everything_that_feeds_the_campaign_s_figures(self):
        """Une seule exception, et elle est nommée ici pour ne pas s'étendre.

        La réconciliation entre deux campagnes n'écrit que dans ses propres
        tables : elle ne change ni un écart, ni un IRA, ni un total que
        quelqu'un a validé. Et c'est une fois les deux inventaires terminés
        qu'on la fait — la figer à la clôture interdisait l'usage principal de
        la fonction. Tout le reste, y compris l'écart backflush qui entre dans
        l'écart d'inventaire, est bel et bien gelé.
        """
        editable = mutability_of(CampaignStatus.CLOSED)
        open_aspects = {
            name for name, value in editable.as_dict().items() if value
        }
        assert open_aspects == {"stockFlow"}


class TestTransitionBlockers:
    def test_counting_has_no_structural_prerequisite(self):
        assert campaign_transition_blockers(
            CampaignStatus.PREPARATION, CampaignStatus.COUNTING
        ) == []

    def test_analysis_requires_a_frozen_book_stock(self):
        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING, CampaignStatus.ANALYSIS, book_stock_frozen=False
        )
        assert any(b.code == "BOOK_STOCK_NOT_FROZEN" for b in blockers)

    def test_analysis_requires_every_journal_to_be_complete(self):
        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING,
            CampaignStatus.ANALYSIS,
            book_stock_frozen=True,
            journal_statuses=[JournalStatus.POSTED, JournalStatus.PENDING],
        )
        assert any(b.code == "JOURNALS_NOT_POSTED" for b in blockers)

    def test_book_enforced_counts_as_complete(self):
        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING,
            CampaignStatus.ANALYSIS,
            book_stock_frozen=True,
            journal_statuses=[JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED],
        )
        assert blockers == []

    def test_analysis_requires_every_zone_to_be_done(self):
        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING,
            CampaignStatus.ANALYSIS,
            book_stock_frozen=True,
            zone_statuses=[ZoneStatus.DONE, ZoneStatus.IN_PROGRESS],
        )
        assert any(b.code == "ZONES_NOT_DONE" for b in blockers)


class TestZoneStatus:
    """Trois états, dont deux se lisent dans les quantités.

    Une feuille n'a plus d'état propre. Elle en a eu quatre, qu'il fallait faire
    avancer à la main deux fois par zone alors qu'aucune écriture n'en dépendait :
    le papier partait au comptage que le bouton ait été cliqué ou non, et les
    quantités s'enregistraient dans tous les cas.
    """

    def test_pending_when_nothing_has_been_counted(self):
        assert derive_zone_status(counted_lines=0, closed=False) is ZoneStatus.PENDING

    def test_in_progress_as_soon_as_one_quantity_exists(self):
        """Rien à cliquer : saisir la première quantité *est* le démarrage."""
        assert (
            derive_zone_status(counted_lines=1, closed=False)
            is ZoneStatus.IN_PROGRESS
        )

    def test_done_is_the_human_decision(self):
        assert derive_zone_status(counted_lines=42, closed=True) is ZoneStatus.DONE

    def test_a_zone_closed_without_a_single_count_is_still_done(self):
        """Une zone vide déclarée finie l'est : c'est le cas de la salle où il
        n'y avait rien à compter, et la déduire du contraire bloquerait la
        campagne sur une zone qui n'a rien à dire."""
        assert derive_zone_status(counted_lines=0, closed=True) is ZoneStatus.DONE


class TestClosingAZone:
    def test_an_open_discrepancy_refuses_the_closure(self):
        """Fermer sur un écart non tranché promettrait à la consolidation une
        quantité qui n'existe pas encore."""
        message = zone_closure_blockers(pending_arbitrations=3)
        assert "3 écart" in message
        assert "Arbitrez" in message

    def test_nothing_pending_lets_it_close(self):
        assert zone_closure_blockers(pending_arbitrations=0) == ""


class TestArbitrationRequired:
    def _line(self, q1, q2, arbitrated=None, decided=False) -> ArbitrationLine:
        return ArbitrationLine(
            id="a", campaign_id="c", zone_id="z", item_number="P-1",
            section="LINE_SIDE", qty_pass_1=q1, qty_pass_2=q2,
            qty_arbitrated=arbitrated,
            decided_at=dt.datetime(2026, 6, 30, tzinfo=dt.UTC) if decided else None,
        )

    def test_agreement_needs_nothing(self):
        assert arbitration_required([self._line(10, 10)]) == []

    def test_any_difference_requires_a_decision_by_default(self):
        assert len(arbitration_required([self._line(10, 11)])) == 1

    def test_a_decided_line_is_not_pending(self):
        assert arbitration_required(
            [self._line(10, 11, arbitrated=11, decided=True)]
        ) == []

    def test_a_pre_filled_line_is_still_pending(self):
        """A quantity sitting in a field is not a decision somebody made."""
        assert len(arbitration_required([self._line(10, 11, arbitrated=11)])) == 1

    def test_tolerance_absorbs_a_small_relative_gap(self):
        from decimal import Decimal

        assert arbitration_required(
            [self._line(100, 99)], tolerance=Decimal("0.02")
        ) == []
        assert len(
            arbitration_required([self._line(100, 90)], tolerance=Decimal("0.02"))
        ) == 1
