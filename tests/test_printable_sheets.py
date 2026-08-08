"""The printed counting sheet — the only artefact the shop floor actually holds.

Assertions read the generated PDF back rather than inspecting the builder's
internals: what matters is what lands on the paper.
"""

from __future__ import annotations

import datetime as dt

import pytest

from inventory.domain.enums import CampaignStatus
from inventory.domain.printing import (
    BLANK_ROWS_PER_SECTION,
    PrintMode,
    available_print_modes,
    print_refusal,
)
from inventory.reporting.exports import build_counting_sheet_pdf


def line(number: str, section: str = "LINE_SIDE", **kwargs) -> dict:
    return {
        "item_number": number,
        "name": kwargs.get("name", "VIS TETE HEXAGONALE M6"),
        "section": section,
        "unit": kwargs.get("unit", "PCE"),
        "qty": kwargs.get("qty"),
        "source": kwargs.get("source", "MANUAL"),
        "comment": kwargs.get("comment", ""),
    }


def render(lines, **kwargs) -> list[str]:
    """Build the sheet and return the text of each page."""
    import pypdfium2

    payload = build_counting_sheet_pdf(
        campaign_label="INV-2026-06 — Inventaire",
        campaign_code="INV-2026-06",
        count_date=dt.date(2026, 6, 30),
        zone_code="FI ASSY",
        zone_label="Assemblage final",
        pass_no=1,
        lines=lines,
        sheet_id="abcdef12-3456",
        **kwargs,
    )
    assert payload[:4] == b"%PDF"
    document = pypdfium2.PdfDocument(payload)
    try:
        return [page.get_textpage().get_text_bounded() for page in document]
    finally:
        document.close()


class TestSheetWithoutQuantities:
    """``mode=list`` — the article list, handed to a counter."""

    def test_the_quantity_column_is_left_empty(self):
        pages = render([line("P-001", qty=42)])
        assert "P-001" in pages[0]
        assert "42" not in pages[0]

    def test_every_section_gets_room_for_an_unlisted_article(self):
        """A part found in a corner has to have somewhere to be written down."""
        pages = render([line("P-001")])
        text = "\n".join(pages)
        # All three tables print, even though only the line side has content.
        assert "Composants en bord de ligne" in text
        assert "en-cours non déclaré" in text
        assert "ensembles déclarés" in text

    def test_a_line_that_was_never_counted_is_still_printed(self):
        pages = render([line("P-001", qty=None), line("P-002", qty=7)])
        assert "P-001" in pages[0] and "P-002" in pages[0]

    def test_the_free_row_allowance_is_five_three_two(self):
        """Sized from where surprises actually turn up, not evenly."""
        assert BLANK_ROWS_PER_SECTION == {"LINE_SIDE": 5, "WIP": 3, "WIP_OK": 2}


class TestFilledSheet:
    def test_quantities_are_printed(self):
        pages = render([line("P-001", qty=42)], mode=PrintMode.FILLED)
        assert "42" in pages[0]

    def test_a_listed_article_nobody_counted_says_so(self):
        """Dropping the row would hide the one fact the record exists to carry."""
        pages = render(
            [line("P-001", qty=42), line("P-002", qty=None)], mode=PrintMode.FILLED
        )
        assert "P-001" in pages[0] and "P-002" in pages[0]
        assert "non compté" in pages[0]

    def test_no_blank_rows_are_appended(self):
        """Inviting somebody to write on a record would make it not a record."""
        blank = render([line("P-001", qty=1)])
        filled = render([line("P-001", qty=1)], mode=PrintMode.FILLED)
        assert len("\n".join(filled)) < len("\n".join(blank))

    def test_sources_and_comments_are_optional_columns(self):
        without = render([line("P-001", qty=1, comment="raturé")], mode=PrintMode.FILLED)
        with_them = render(
            [line("P-001", qty=1, source="SCAN_AI", comment="raturé")],
            mode=PrintMode.FILLED, with_sources=True,
        )
        assert "Commentaire" not in without[0]
        assert "Commentaire" in with_them[0]
        assert "raturé" in with_them[0]
        assert "IA" in with_them[0]


class TestFreeEntrySheet:
    """``mode=blank`` — nothing is known, the counter writes both columns."""

    def test_blank_lines_are_printed_without_any_article(self):
        pages = render([], mode=PrintMode.BLANK, blank_lines=40)
        text = "\n".join(pages)
        assert "Composants en bord de ligne" in text
        # 40 line-side rows do not fit on one page.
        assert len(pages) > 1

    def test_the_article_list_is_ignored_rather_than_printed(self):
        """The whole point of this mode is a grid with nothing pre-filled."""
        pages = render([line("P-001")], mode=PrintMode.BLANK, blank_lines=10)
        assert "P-001" not in "\n".join(pages)

    def test_only_the_asked_for_rows_are_printed(self):
        """Somebody who asks for ten lines gets ten, not ten plus an allowance."""
        pages = render([], mode=PrintMode.BLANK, blank_lines=10)
        text = "\n".join(pages)
        assert "en-cours non déclaré" not in text
        assert "ensembles déclarés" not in text

    @pytest.mark.parametrize("count", [10, 60, 180])
    def test_the_accepted_range_renders(self, count):
        assert render([], mode=PrintMode.BLANK, blank_lines=count)


class TestWhichModesAreOffered:
    """The matrix: what a zone can be printed as, and when.

    Two facts decide it — whether the zone has a pre-printed article list, and
    whether anything has been counted yet. Everything else follows.
    """

    @pytest.mark.parametrize(
        "status", [CampaignStatus.COUNTING, CampaignStatus.ANALYSIS, CampaignStatus.CLOSED]
    )
    def test_a_listed_zone_prints_its_list_then_its_record(self, status):
        assert available_print_modes(free_entry=False, status=status) == (
            PrintMode.LIST, PrintMode.FILLED,
        )

    @pytest.mark.parametrize(
        "status", [CampaignStatus.COUNTING, CampaignStatus.ANALYSIS, CampaignStatus.CLOSED]
    )
    def test_a_free_entry_zone_prints_a_grid_then_its_record(self, status):
        assert available_print_modes(free_entry=True, status=status) == (
            PrintMode.BLANK, PrintMode.FILLED,
        )

    def test_the_record_does_not_exist_before_the_count(self):
        for free_entry in (True, False):
            assert PrintMode.FILLED not in available_print_modes(
                free_entry=free_entry, status=CampaignStatus.PREPARATION
            )

    def test_the_sheet_to_hand_out_exists_from_the_first_phase(self):
        """Paper is prepared *before* inventory day — that is the whole job."""
        assert available_print_modes(
            free_entry=False, status=CampaignStatus.PREPARATION
        ) == (PrintMode.LIST,)
        assert available_print_modes(
            free_entry=True, status=CampaignStatus.PREPARATION
        ) == (PrintMode.BLANK,)

    def test_a_listed_zone_is_never_offered_a_blank_grid(self):
        """It would ask the counter to rewrite a list the application holds."""
        assert print_refusal(
            PrintMode.BLANK, free_entry=False, status=CampaignStatus.COUNTING
        )

    def test_a_free_entry_zone_has_no_list_to_print(self):
        assert print_refusal(
            PrintMode.LIST, free_entry=True, status=CampaignStatus.COUNTING
        )

    def test_an_allowed_mode_is_not_refused(self):
        assert print_refusal(
            PrintMode.LIST, free_entry=False, status=CampaignStatus.PREPARATION
        ) is None


class TestPagination:
    def test_the_section_title_repeats_on_every_page_of_a_table(self):
        """A page separated from its stack must still say what it is counting.

        Otherwise a WIP assembly on page 3 gets counted as a line-side part —
        which is the confusion the sections exist to prevent.
        """
        pages = render([line(f"P-{i:04d}") for i in range(60)])
        assert len(pages) > 1
        assert all("Composants en bord de ligne" in page for page in pages[:2])

    def test_the_sheet_identity_repeats_in_every_footer(self):
        pages = render([line(f"P-{i:04d}") for i in range(60)])
        assert all("abcdef12" in page for page in pages)


class TestDesignation:
    def test_a_long_designation_is_truncated_not_wrapped(self):
        long_name = "STATOR ASSEMBLE M3 GEN2 AVEC CONNECTIQUE ET CAPOT ARRIERE"
        pages = render([line("P-001", name=long_name)])
        assert long_name not in pages[0]
        assert "STATOR ASSEMBLE M3 GEN2 AVEC CO" in pages[0]

    def test_the_budget_shrinks_when_the_provenance_columns_appear(self):
        long_name = "STATOR ASSEMBLE M3 GEN2 AVEC CONNECTIQUE"
        wide = render([line("P-001", name=long_name, qty=1)], mode=PrintMode.FILLED)
        narrow = render(
            [line("P-001", name=long_name, qty=1)], mode=PrintMode.FILLED, with_sources=True
        )
        assert "STATOR ASSEMBLE M3 GEN2 AVEC CO" in wide[0]
        assert "STATOR ASSEMBLE M3 " in narrow[0]
        assert "STATOR ASSEMBLE M3 GEN2" not in narrow[0]
