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


class TestNoBlankPages:
    """A printed stack must not contain a page with nothing on it.

    The gap between two section tables used to be appended *after* each table,
    including the last. A trailing spacer is still a flowable: when a table ended
    exactly at the bottom of a page it flowed onto the next one, and the sheet
    came out with a page carrying only its header and footer. It turned up once
    in sixty-two pages of a real workbook — rare enough to ship, frequent enough
    to confuse a counter every campaign.

    The cases below are the exact fits found by sweeping the old builder; the
    surrounding range guards the neighbourhood, because the boundary moves with
    any change to row height or margins.
    """

    def _assert_every_page_carries_a_line(self, line_side, wip=0, wip_ok=0):
        rows = (
            [line(f"P-{i:04d}", qty=1) for i in range(line_side)]
            + [line(f"W-{i:04d}", section="WIP", qty=1) for i in range(wip)]
            + [line(f"K-{i:04d}", section="WIP_OK", qty=1) for i in range(wip_ok)]
        )
        pages = render(rows, mode=PrintMode.FILLED)
        for number, page in enumerate(pages, start=1):
            assert any(k in page for k in ("P-", "W-", "K-")), (
                f"page {number} sur {len(pages)} est vide "
                f"({line_side}/{wip}/{wip_ok} lignes)"
            )

    @pytest.mark.parametrize("line_side,wip,wip_ok", [
        (23, 0, 0),   # une seule section, qui finit pile en bas de page
        (15, 3, 2),   # trois sections, la dernière finit pile
        (44, 2, 0),
        (44, 0, 2),
    ])
    def test_the_known_exact_fits_leave_no_empty_page(self, line_side, wip, wip_ok):
        self._assert_every_page_carries_a_line(line_side, wip, wip_ok)

    @pytest.mark.parametrize("count", range(20, 27))
    def test_nor_does_the_neighbourhood_of_a_page_boundary(self, count):
        self._assert_every_page_carries_a_line(count)


class TestNumbersOnPaper:
    """Every character of a quantity has to be drawable by the PDF's font.

    The sheet is drawn in Helvetica, whose Latin-1 encoding has no narrow
    no-break space. Using one as the thousands separator printed « 2■724 »: a
    black box exactly where the counter reads a digit. It only showed above a
    thousand, so the demo campaign never caught it.

    Reading the page back is what makes these tests worth having: a character
    the font cannot draw comes back as U+25A0 — the box itself — so the failure
    appears here in the same shape it has on paper. Asserting on the formatter's
    output instead would have passed throughout the bug.
    """

    @pytest.mark.parametrize("quantity,expected", [
        (2724, "2 724"),
        (15600, "15 600"),
        (1_234_567, "1 234 567"),
        (999, "999"),
    ])
    def test_a_thousands_separator_survives_the_page(self, quantity, expected):
        """The separator drawn is a no-break space; extraction normalises it."""
        pages = render([line("P-001", qty=quantity)], mode=PrintMode.FILLED)
        assert expected in pages[0]

    def test_no_undrawable_character_reaches_the_paper(self):
        pages = render(
            [line(f"P-{i:03d}", qty=1000 * (i + 1)) for i in range(5)],
            mode=PrintMode.FILLED,
        )
        text = "\n".join(pages)
        assert "■" not in text, "un caractère non dessinable a atteint le papier"
