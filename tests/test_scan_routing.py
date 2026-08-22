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
from inventory.ai.sheet_extraction import (
    ExtractionResult,
    SheetCandidate,
    SheetExtractor,
)
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


def route(
    payload: dict[str, Any],
    pages: int = 2,
    candidates=CANDIDATES,
    batch_size: int = 12,
):
    client = _FakeClient(payload)
    extractor = SheetExtractor(client=client)
    routing = extractor.route_pages(
        footers=[b"bande"] * pages,
        candidates=candidates,
        batch_size=batch_size,
        max_workers=1,
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
        """Le modèle a répondu dans le désordre ; les pages, non.

        Une feuille sur deux pages dont la liste d'articles continue au verso,
        envoyée à l'extraction verso d'abord, se lit à l'envers. Rien ne le
        signale : le modèle rend des lignes plausibles, dans le mauvais ordre.
        """
        routing, _ = route({"pages": [
            {"page": 2, "sheet": "aaaaaaaa"},
            {"page": 1, "sheet": "aaaaaaaa"},
        ]})
        assert routing.pages_by_sheet["aaaaaaaa-1111-2222"] == [0, 1]

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


class TestReportShape:
    """The per-sheet report and the routing are merged into one row on screen.

    They must therefore not claim the same key with two different shapes. The
    screen renders the page *list*; a per-sheet report claiming ``pages`` as a
    *count* silently won the merge and crashed the whole result panel on
    ``pages.join is not a function``.
    """

    def test_the_extraction_report_does_not_claim_the_page_list_key(self):
        assert "pages" not in ExtractionResult(pages=3).as_report()

    def test_it_still_says_how_many_pages_it_read(self):
        assert ExtractionResult(pages=3).as_report()["pagesRead"] == 3

    def test_merging_a_report_with_a_routing_keeps_the_page_list(self):
        row = {**ExtractionResult(pages=2).as_report(), "pages": [4, 5]}
        assert row["pages"] == [4, 5]


class TestPreconditions:
    def test_no_pages_is_refused(self):
        extractor = SheetExtractor(client=_FakeClient({}))
        with pytest.raises(ValidationError):
            extractor.route_pages(footers=[], candidates=CANDIDATES)

    def test_no_candidate_sheet_is_refused(self):
        """With nothing to route to, the model would have to invent a target."""
        extractor = SheetExtractor(client=_FakeClient({}))
        with pytest.raises(ValidationError, match="Aucune feuille"):
            extractor.route_pages(footers=[b"bande"], candidates=[])


class _BatchClient:
    """Répond par lot, et note ce que chaque appel portait.

    Le routage d'une pile de cent pages est découpé : cette doublure sert à
    vérifier que le découpage rend les mêmes numéros de page qu'un appel
    unique — c'est là que le décalage d'un lot se voit, ou pas.
    """

    def __init__(self, per_batch: list[dict[str, Any]] | None = None) -> None:
        self.per_batch = per_batch or []
        self.calls: list[int] = []

    def complete_json(self, *, system: str, user: str, images=(), **kwargs: Any):
        index = len(self.calls)
        self.calls.append(len(images))
        payload = (
            self.per_batch[index] if index < len(self.per_batch)
            else {"pages": [
                {"page": n + 1, "sheet": "aaaaaaaa"} for n in range(len(images))
            ]}
        )
        return payload, LlmResponse(text="", prompt_tokens=1, completion_tokens=1)


class _FailingBatchClient(_BatchClient):
    """Le deuxième lot échoue ; les autres aboutissent."""

    def complete_json(self, **kwargs: Any):
        index = len(self.calls)
        if index == 1:
            self.calls.append(len(kwargs.get("images") or ()))
            raise RuntimeError("endpoint saturé")
        return super().complete_json(**kwargs)


class TestBatching:
    """Une pile de cent pages ne tient pas dans un appel.

    Un appel unique portant toutes les pages est une charge utile que l'endpoint
    refuse bien avant que le modèle ait un problème de lecture — et une réponse
    tronquée y perdait le routage de la pile entière.
    """

    def test_the_pages_are_split_into_batches(self):
        client = _BatchClient()
        SheetExtractor(client=client).route_pages(
            footers=[b"b"] * 25, candidates=CANDIDATES,
            batch_size=10, max_workers=1,
        )
        assert client.calls == [10, 10, 5]

    def test_a_batch_numbers_its_pages_from_the_stack_not_from_itself(self):
        """Le décalage du lot : sans lui, les pages 11 à 20 s'écrivent 1 à 10."""
        client = _BatchClient()
        routing = SheetExtractor(client=client).route_pages(
            footers=[b"b"] * 25, candidates=CANDIDATES,
            batch_size=10, max_workers=1,
        )
        assert routing.pages_by_sheet["aaaaaaaa-1111-2222"] == list(range(25))
        assert routing.unrouted == []

    def test_a_failed_batch_loses_only_its_own_pages(self):
        client = _FailingBatchClient()
        routing = SheetExtractor(client=client).route_pages(
            footers=[b"b"] * 25, candidates=CANDIDATES,
            batch_size=10, max_workers=1,
        )
        # Pages 1-10 et 21-25 routées ; 11-20 rendues à l'humain, avec la raison.
        assert routing.pages_by_sheet["aaaaaaaa-1111-2222"] == (
            list(range(10)) + list(range(20, 25))
        )
        assert [u["page"] for u in routing.unrouted] == list(range(11, 21))
        assert "saturé" in routing.unrouted[0]["note"]

    def test_the_tokens_of_every_batch_are_counted(self):
        client = _BatchClient()
        routing = SheetExtractor(client=client).route_pages(
            footers=[b"b"] * 25, candidates=CANDIDATES,
            batch_size=10, max_workers=1,
        )
        assert routing.tokens_used == 6  # trois lots × (1 + 1)
