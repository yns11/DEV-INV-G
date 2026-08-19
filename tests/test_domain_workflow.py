"""Campaign, sheet and zone state machines, and the freeze matrix."""

from __future__ import annotations

import datetime as dt

import pytest

from inventory.domain.enums import (
    CampaignStatus,
    JournalStatus,
    SheetPass,
    SheetStatus,
    ZoneStatus,
)
from inventory.domain.models import ArbitrationLine, CountSheet
from inventory.domain.workflow import (
    arbitration_required,
    assert_campaign_transition,
    assert_sheet_transition,
    campaign_transition_blockers,
    derive_zone_status,
    mutability_of,
)
from inventory.errors import WorkflowError


def sheet(pass_no: SheetPass, status: SheetStatus) -> CountSheet:
    return CountSheet(
        id=f"s-{pass_no}", campaign_id="c", zone_id="z", pass_no=pass_no, status=status
    )


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
            zone_statuses=[ZoneStatus.DONE, ZoneStatus.ARBITRATION],
        )
        assert any(b.code == "ZONES_NOT_DONE" for b in blockers)


class TestSheetTransitions:
    def test_forward_and_one_step_back_are_allowed(self):
        assert_sheet_transition(
            sheet(SheetPass.PASS_1, SheetStatus.PENDING), SheetStatus.COUNTING
        )
        assert_sheet_transition(
            sheet(SheetPass.PASS_1, SheetStatus.DONE), SheetStatus.ENCODING
        )

    def test_skipping_a_step_is_refused(self):
        with pytest.raises(WorkflowError):
            assert_sheet_transition(
                sheet(SheetPass.PASS_1, SheetStatus.PENDING), SheetStatus.DONE
            )

    def test_pass_two_cannot_start_before_pass_one_is_returned(self):
        """Two simultaneous counts are one count done twice, not two counts."""
        with pytest.raises(WorkflowError, match="comptage n°1"):
            assert_sheet_transition(
                sheet(SheetPass.PASS_2, SheetStatus.PENDING),
                SheetStatus.COUNTING,
                pass_1_status=SheetStatus.COUNTING,
            )

    def test_pass_two_may_start_once_pass_one_is_being_encoded(self):
        assert_sheet_transition(
            sheet(SheetPass.PASS_2, SheetStatus.PENDING),
            SheetStatus.COUNTING,
            pass_1_status=SheetStatus.ENCODING,
        )


class TestZoneStatus:
    def test_pending_when_nothing_started(self):
        sheets = [
            sheet(SheetPass.PASS_1, SheetStatus.PENDING),
            sheet(SheetPass.PASS_2, SheetStatus.PENDING),
        ]
        assert derive_zone_status(sheets) is ZoneStatus.PENDING

    def test_pass_one_running(self):
        sheets = [
            sheet(SheetPass.PASS_1, SheetStatus.COUNTING),
            sheet(SheetPass.PASS_2, SheetStatus.PENDING),
        ]
        assert derive_zone_status(sheets) is ZoneStatus.PASS_1_RUNNING

    def test_pass_two_running(self):
        sheets = [
            sheet(SheetPass.PASS_1, SheetStatus.DONE),
            sheet(SheetPass.PASS_2, SheetStatus.ENCODING),
        ]
        assert derive_zone_status(sheets) is ZoneStatus.PASS_2_RUNNING

    def test_arbitration_when_both_done_but_gaps_remain(self):
        sheets = [
            sheet(SheetPass.PASS_1, SheetStatus.DONE),
            sheet(SheetPass.PASS_2, SheetStatus.DONE),
        ]
        assert (
            derive_zone_status(sheets, pending_arbitrations=3) is ZoneStatus.ARBITRATION
        )

    def test_done_when_both_passes_agree(self):
        sheets = [
            sheet(SheetPass.PASS_1, SheetStatus.DONE),
            sheet(SheetPass.PASS_2, SheetStatus.DONE),
        ]
        assert derive_zone_status(sheets) is ZoneStatus.DONE

    def test_single_pass_campaign_skips_pass_two(self):
        sheets = [sheet(SheetPass.PASS_1, SheetStatus.DONE)]
        assert derive_zone_status(sheets, passes_required=1) is ZoneStatus.DONE


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
