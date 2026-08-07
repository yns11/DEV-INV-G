"""Routing the pages of a multi-sheet scan back to their sheets.

The application printed these pages, so each one carries its sheet's identifier
in the footer. Everything here exists to guarantee one property: a page is
either routed to the sheet it names, or reported — never guessed. A page filed
under the wrong zone posts a count against stock that was never there, and no
downstream control can undo that.
"""

from __future__ import annotations

from typing import Any

import pytest

from inventory.ai.client import LlmResponse
from inventory.ai.sheet_extraction import SheetCandidate, SheetExtractor
from inventory.errors import ValidationError

CANDIDATES = [
    SheetCandidate(sheet_id="aaaaaaaa-1111-2222", zone_code="FI ASSY", pass_no=1),
    SheetCandidate(sheet_id="bbbbbbbb-3333-4444", zone_code="PICKING", pass_no=1),
]


class _FakeClient:
    """Answers with a canned routing payload, and records what it was asked."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete_json(self, *, system: str, user: str, **kwargs: Any):
        self.prompts.append(user)
        return self.payload, LlmResponse(text="", prompt_tokens=1, completion_tokens=1)


def route(payload: dict[str, Any], pages: int = 2, candidates=CANDIDATES):
    client = _FakeClient(payload)
    extractor = SheetExtractor(client=client)
    routing = extractor.route_pages(
        images=[b"page"] * pages, candidates=candidates
    )
    return routing, client


class TestRouting:
    def test_pages_land_on_the_sheet_their_footer_names(self):
        routing, _ = route({"pages": [
            {"page": 1, "sheet": "aaaaaaaa", "confidence": 0.99},
            {"page": 2, "sheet": "bbbbbbbb", "confidence": 0.98},
        ]})
        assert routing.pages_by_sheet == {
            "aaaaaaaa-1111-2222": [0],
            "bbbbbbbb-3333-4444": [1],
        }
        assert routing.unrouted == []

    def test_several_pages_of_one_sheet_stay_in_reading_order(self):
        routing, _ = route({"pages": [
            {"page": 2, "sheet": "aaaaaaaa"},
            {"page": 1, "sheet": "aaaaaaaa"},
        ]})
        # The model answered out of order; the pages are still fed to the
        # extractor in the order they were scanned.
        assert routing.pages_by_sheet["aaaaaaaa-1111-2222"] == [1, 0]

    def test_the_candidate_list_is_what_the_model_is_shown(self):
        _, client = route({"pages": []})
        assert "aaaaaaaa" in client.prompts[0]
        assert "FI ASSY" in client.prompts[0]


class TestRefusalToGuess:
    def test_an_unknown_identifier_is_reported_not_matched(self):
        """A near-miss is still a miss: attributing it would post to a wrong zone."""
        routing, _ = route({"pages": [
            {"page": 1, "sheet": "aaaaaaab", "note": "pied de page abîmé"},
            {"page": 2, "sheet": "bbbbbbbb"},
        ]})
        assert routing.pages_by_sheet == {"bbbbbbbb-3333-4444": [1]}
        assert routing.unrouted[0]["page"] == 1
        assert routing.unrouted[0]["read"] == "aaaaaaab"

    def test_a_null_identifier_is_reported(self):
        routing, _ = route({"pages": [
            {"page": 1, "sheet": None, "note": "illisible"},
            {"page": 2, "sheet": "bbbbbbbb"},
        ]})
        assert [u["page"] for u in routing.unrouted] == [1]

    def test_a_page_the_model_forgot_is_reported(self):
        """Silence is not a routing decision."""
        routing, _ = route({"pages": [{"page": 1, "sheet": "aaaaaaaa"}]}, pages=3)
        assert [u["page"] for u in routing.unrouted] == [2, 3]

    def test_a_page_answered_twice_is_counted_once(self):
        routing, _ = route({"pages": [
            {"page": 1, "sheet": "aaaaaaaa"},
            {"page": 1, "sheet": "bbbbbbbb"},
            {"page": 2, "sheet": "bbbbbbbb"},
        ]})
        assert routing.pages_by_sheet == {
            "aaaaaaaa-1111-2222": [0],
            "bbbbbbbb-3333-4444": [1],
        }

    def test_a_page_number_out_of_range_is_ignored(self):
        routing, _ = route({"pages": [
            {"page": 9, "sheet": "aaaaaaaa"},
            {"page": 1, "sheet": "aaaaaaaa"},
            {"page": 2, "sheet": "bbbbbbbb"},
        ]})
        assert routing.pages_by_sheet["aaaaaaaa-1111-2222"] == [0]


class TestPreconditions:
    def test_no_pages_is_refused(self):
        extractor = SheetExtractor(client=_FakeClient({}))
        with pytest.raises(ValidationError):
            extractor.route_pages(images=[], candidates=CANDIDATES)

    def test_no_candidate_sheet_is_refused(self):
        """With nothing to route to, the model would have to invent a target."""
        extractor = SheetExtractor(client=_FakeClient({}))
        with pytest.raises(ValidationError, match="Aucune feuille"):
            extractor.route_pages(images=[b"page"], candidates=[])
