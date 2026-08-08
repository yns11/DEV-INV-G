"""Reading a free-entry sheet — one printed empty and filled in by hand.

There is no pre-printed article list to read against, which removes the guard
that makes a normal sheet safe: on a listed sheet, a reference the model
"reads" that is not on the paper is provably invented. Here the same guard has
to be applied one step later, against the campaign's article referential. These
tests pin that: what the referential knows becomes a counted line, what it does
not is reported and never created.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from inventory.ai.client import LlmResponse
from inventory.ai.sheet_extraction import SheetExtractor
from inventory.domain.enums import CountSection, DataSource
from inventory.domain.models import Item
from inventory.errors import ValidationError

CAMPAIGN = "camp-1"
SHEET = "sheet-1"

ITEMS = {
    "P-00001": Item(
        campaign_id=CAMPAIGN, item_number="P-00001", name="VIS M6", unit="PCE"
    ),
    "P-00002": Item(
        campaign_id=CAMPAIGN, item_number="P-00002", name="ECROU M8", unit="BOX"
    ),
}


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete_json(self, *, system: str, user: str, **kwargs: Any):
        self.prompts.append(user)
        return self.payload, LlmResponse(text="", prompt_tokens=1, completion_tokens=1)


def read(payload: dict[str, Any], items=None, pages: int = 1):
    client = _FakeClient(payload)
    counter = iter(f"line-{i}" for i in range(100))
    result = SheetExtractor(client=client).extract_free_entry(
        campaign_id=CAMPAIGN,
        sheet_id=SHEET,
        zone_label="LABO CHIMIE",
        pass_no=1,
        known_items=ITEMS if items is None else items,
        images=[b"page"] * pages,
        id_factory=lambda: next(counter),
    )
    return result, client


class TestWhatIsAccepted:
    def test_a_known_reference_becomes_a_counted_line(self):
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 12, "confidence": 0.95},
        ]})
        assert [(l.item_number, l.qty) for l in result.lines] == [
            ("P-00001", Decimal("12")),
        ]
        assert result.lines[0].source is DataSource.SCAN_AI

    def test_the_reference_is_normalised_before_being_looked_up(self):
        """Handwriting arrives with stray spaces and lower case."""
        result, _ = read({"lines": [{"item_number": " p-00001 ", "qty": 3}]})
        assert [l.item_number for l in result.lines] == ["P-00001"]

    def test_a_blank_quantity_stays_uncounted(self):
        """« non compté » and « compté à zéro » are different facts."""
        result, _ = read({"lines": [{"item_number": "P-00001", "qty": None}]})
        assert result.lines[0].is_counted is False
        assert result.lines[0].qty_imported is None

    def test_the_section_written_on_the_sheet_is_kept(self):
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 1, "section": "MOM waiting"},
        ]})
        assert result.lines[0].section is CountSection.WIP

    def test_an_unreadable_section_falls_back_to_the_line_side(self):
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 1, "section": "???"},
        ]})
        assert result.lines[0].section is CountSection.LINE_SIDE

    def test_the_unit_falls_back_to_the_referential(self):
        result, _ = read({"lines": [{"item_number": "P-00002", "qty": 1}]})
        assert result.lines[0].unit == "BOX"

    def test_a_reference_read_twice_is_counted_once(self):
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 5},
            {"item_number": "P-00001", "qty": 9},
        ]})
        assert len(result.lines) == 1
        assert result.lines[0].qty == Decimal("5")


class TestWhatIsRefused:
    def test_an_unknown_reference_is_reported_never_created(self):
        """An article invented by a misreading becomes an unexplainable variance."""
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 4},
            {"item_number": "P-99999", "qty": 7},
        ]})
        assert [l.item_number for l in result.lines] == ["P-00001"]
        assert result.unexpected[0]["text"] == "P-99999"
        assert "référentiel" in result.unexpected[0]["note"].lower()

    def test_an_empty_reference_is_dropped_silently(self):
        """A blank printed row the counter never used is not a finding."""
        result, _ = read({"lines": [{"item_number": "  ", "qty": None}]})
        assert result.lines == []
        assert result.unexpected == []

    def test_no_page_is_refused(self):
        with pytest.raises(ValidationError):
            read({"lines": []}, pages=0)


class TestConfidence:
    def test_a_doubtful_reading_is_surfaced_for_checking(self):
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 12, "confidence": 0.4},
        ]})
        assert result.low_confidence_items == ["P-00001"]

    def test_an_uncounted_line_does_not_drag_the_average_down(self):
        """Confidence measures what was read, not what was left blank."""
        result, _ = read({"lines": [
            {"item_number": "P-00001", "qty": 12, "confidence": 0.9},
            {"item_number": "P-00002", "qty": None, "confidence": 0.1},
        ]})
        assert result.mean_confidence == 0.9


class TestThePrompt:
    def test_the_model_is_told_the_sheet_carried_no_list(self):
        _, client = read({"lines": []})
        assert "saisie libre" in client.prompts[0].lower()

    def test_no_expected_list_is_shipped(self):
        """Sending one would invite the model to match against nothing."""
        _, client = read({"lines": []})
        assert "P-00001" not in client.prompts[0]
