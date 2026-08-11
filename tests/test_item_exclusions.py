"""Exclusions posées sur un lot, et filtre « articles stockés / comptés ».

Les deux tiennent dans des fonctions de routage qui ne dépendent que du
contexte : un contexte factice suffit, comme partout ailleurs dans cette suite.
Ce qui est vérifié ici n'est pas le câblage HTTP mais les décisions — ce qui est
écrit, ce qui ne l'est pas, et ce qui est refusé.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError as PydanticValidationError

from inventory.api.routers import data
from inventory.api.schemas import ItemExclusionsRequest
from inventory.domain.enums import ExclusionScope
from inventory.domain.models import BomLink, Item
from inventory.errors import ValidationError

CAMPAIGN = cast(Any, SimpleNamespace(id="camp-1"))


def item(number: str, *, exclusions: object = ()) -> Item:
    return Item(campaign_id="camp-1", item_number=number, exclusions=exclusions)


class Referentials:
    def __init__(self, items: list[Item], links: list[BomLink] | None = None) -> None:
        self._items = items
        self._links = links or []
        self.written: list[Item] = []

    def items_by_number(self, campaign_id: str) -> dict[str, Item]:
        return {i.item_number: i for i in self._items}

    def list_items(self, campaign_id: str) -> list[Item]:
        return list(self._items)

    def list_bom_links(self, campaign_id: str) -> list[BomLink]:
        return list(self._links)

    def upsert_items(self, items: Any, *, actor: str) -> int:
        self.written = list(items)
        return len(self.written)


def context(
    items: list[Item] | None = None,
    links: list[BomLink] | None = None,
    *,
    on_sheets: set[str] = frozenset(),
    on_journals: set[str] = frozenset(),
    frozen: bool = False,
) -> Any:
    referentials = Referentials(items or [], links)
    events: list[dict[str, Any]] = []

    def guard(campaign: Any, aspect: str) -> None:
        if frozen:
            raise ValidationError(f"« {aspect} » est gelé.")

    return SimpleNamespace(
        actor="testeur",
        guard=guard,
        referentials=referentials,
        sheets=SimpleNamespace(listed_item_numbers=lambda cid: set(on_sheets)),
        journals=SimpleNamespace(listed_item_numbers=lambda cid: set(on_journals)),
        record=lambda **kw: events.append(kw) or "evt",
        events=events,
    )


def request(numbers: list[str], scopes: list[str]) -> ItemExclusionsRequest:
    return ItemExclusionsRequest.model_validate(
        {"itemNumbers": numbers, "exclusions": scopes}
    )


# --------------------------------------------------------------------------- #


class TestTheExclusionSetIsNormalised:
    """Une même intention ne doit pas pouvoir s'écrire de trois façons."""

    def test_none_is_the_absence_of_an_exclusion_not_a_fourth_one(self):
        assert ExclusionScope.normalise(["NONE"]) == set()

    def test_all_replaces_the_facets_it_already_covers(self):
        assert ExclusionScope.normalise(["ALL", "GENERIC", "BOM"]) == {
            ExclusionScope.ALL
        }

    def test_the_two_facets_are_independent_and_combine(self):
        assert ExclusionScope.normalise(["GENERIC", "BOM"]) == {
            ExclusionScope.GENERIC,
            ExclusionScope.BOM,
        }

    def test_the_article_itself_normalises_on_the_way_in(self):
        """Sinon une exclusion importée court-circuiterait la règle."""
        assert item("P-1", exclusions=["ALL", "BOM"]).exclusions == {ExclusionScope.ALL}

    def test_an_unknown_scope_is_refused_at_the_door(self):
        """Stockée, elle ne casserait qu'à la relecture — et tout à la fois."""
        with pytest.raises(PydanticValidationError):
            request(["P-1"], ["INVENTÉ"])


class TestSettingTheExclusionOfABatch:
    def test_every_selected_article_is_written(self):
        ctx = context([item("P-1"), item("P-2")])
        result = data.set_item_exclusions(CAMPAIGN, request(["P-1", "P-2"], ["ALL"]), ctx)

        assert result["updated"] == 2
        assert {i.item_number for i in ctx.referentials.written} == {"P-1", "P-2"}
        assert all(
            i.exclusions == {ExclusionScope.ALL} for i in ctx.referentials.written
        )

    def test_an_article_already_in_that_state_is_not_rewritten(self):
        """Réécrire pour rien ferait avancer row_version et polluerait l'audit."""
        ctx = context([item("P-1", exclusions=["ALL"]), item("P-2")])
        result = data.set_item_exclusions(CAMPAIGN, request(["P-1", "P-2"], ["ALL"]), ctx)

        assert (result["updated"], result["unchanged"]) == (1, 1)
        assert [i.item_number for i in ctx.referentials.written] == ["P-2"]

    def test_an_empty_selection_of_scopes_puts_the_batch_back_in_scope(self):
        ctx = context([item("P-1", exclusions=["GENERIC"])])
        result = data.set_item_exclusions(CAMPAIGN, request(["P-1"], []), ctx)

        assert result["updated"] == 1
        assert ctx.referentials.written[0].exclusions == set()

    def test_the_batch_is_normalised_like_a_single_edit(self):
        ctx = context([item("P-1")])
        data.set_item_exclusions(CAMPAIGN, request(["P-1"], ["ALL", "GENERIC"]), ctx)
        assert ctx.referentials.written[0].exclusions == {ExclusionScope.ALL}

    def test_references_are_matched_regardless_of_case_and_spacing(self):
        ctx = context([item("P-1")])
        result = data.set_item_exclusions(CAMPAIGN, request([" p-1 "], ["BOM"]), ctx)
        assert result["updated"] == 1

    def test_the_same_reference_twice_counts_once(self):
        ctx = context([item("P-1")])
        result = data.set_item_exclusions(
            CAMPAIGN, request(["P-1", "P-1"], ["BOM"]), ctx
        )
        assert (result["updated"], result["unchanged"]) == (1, 0)

    def test_an_unknown_reference_stops_the_whole_batch(self):
        """La sélection a été faite sur un écran : un inconnu = un désaccord."""
        ctx = context([item("P-1")])
        with pytest.raises(ValidationError) as caught:
            data.set_item_exclusions(CAMPAIGN, request(["P-1", "P-9"], ["ALL"]), ctx)

        assert "P-9" in str(caught.value)
        assert ctx.referentials.written == []

    def test_a_frozen_referential_refuses_the_batch(self):
        ctx = context([item("P-1")], frozen=True)
        with pytest.raises(ValidationError):
            data.set_item_exclusions(CAMPAIGN, request(["P-1"], ["ALL"]), ctx)
        assert ctx.referentials.written == []

    def test_the_batch_lands_in_the_audit_trail_with_its_references(self):
        ctx = context([item("P-1"), item("P-2")])
        data.set_item_exclusions(CAMPAIGN, request(["P-1", "P-2"], ["GENERIC"]), ctx)

        (event,) = ctx.events
        assert "2 article(s)" in event["summary"]
        assert "hors GENERIQUE" in event["summary"]
        assert "P-1" in event["after"]["itemNumbers"]

    def test_a_batch_that_changes_nothing_writes_nothing_at_all(self):
        ctx = context([item("P-1", exclusions=["BOM"])])
        result = data.set_item_exclusions(CAMPAIGN, request(["P-1"], ["BOM"]), ctx)

        assert (result["updated"], ctx.events) == (0, [])


class TestTheStockedOrCountedFilter:
    """« Articles stockés / comptés » : les feuilles B06VRAC ∪ les journaux."""

    def test_without_the_filter_the_whole_referential_is_returned(self):
        ctx = context([item("P-1"), item("P-2")], on_sheets={"P-1"})
        assert data.list_items(CAMPAIGN, ctx)["total"] == 2

    def test_a_reference_on_a_generique_sheet_is_kept(self):
        ctx = context([item("P-1"), item("P-2")], on_sheets={"P-1"})
        page = data.list_items(CAMPAIGN, ctx, counted=True)
        assert [r["item_number"] for r in page["rows"]] == ["P-1"]

    def test_a_reference_on_a_counting_journal_is_kept_too(self):
        """Le stock n'est pas tout en B06VRAC : les deux sources s'unissent."""
        ctx = context([item("P-1"), item("P-2")], on_journals={"P-2"})
        page = data.list_items(CAMPAIGN, ctx, counted=True)
        assert [r["item_number"] for r in page["rows"]] == ["P-2"]

    def test_the_total_reflects_the_filter_so_paging_stays_honest(self):
        ctx = context([item(f"P-{n}") for n in range(10)], on_sheets={"P-3"})
        assert data.list_items(CAMPAIGN, ctx, counted=True)["total"] == 1

    def test_the_filter_and_the_search_box_compose(self):
        ctx = context(
            [item("P-1"), item("P-2"), item("Q-1")], on_sheets={"P-1", "Q-1"}
        )
        page = data.list_items(CAMPAIGN, ctx, search="P-", counted=True)
        assert [r["item_number"] for r in page["rows"]] == ["P-1"]


class TestTheStockedFilterOnBills:
    def link(self, parent: str, child: str) -> BomLink:
        return BomLink(
            campaign_id="camp-1", parent_item=parent, child_item=child, qty_per=1
        )

    def test_an_edge_whose_assembly_is_counted_is_kept(self):
        """Il sera éclaté : sa structure est ce qui produit la quantité."""
        ctx = context(links=[self.link("A", "C")], on_sheets={"A"})
        assert len(data.list_boms(CAMPAIGN, ctx, counted=True)) == 1

    def test_an_edge_whose_component_is_counted_is_kept(self):
        """Un qty_per faux au-dessus d'un composant compté est invisible sinon."""
        ctx = context(links=[self.link("A", "C")], on_journals={"C"})
        assert len(data.list_boms(CAMPAIGN, ctx, counted=True)) == 1

    def test_an_edge_touching_nothing_stocked_falls_out(self):
        ctx = context(links=[self.link("A", "C")], on_sheets={"Z"})
        assert data.list_boms(CAMPAIGN, ctx, counted=True) == []

    def test_the_filter_composes_with_the_parent_filter(self):
        ctx = context(
            links=[self.link("A", "C"), self.link("B", "C")], on_sheets={"C"}
        )
        kept = data.list_boms(CAMPAIGN, ctx, parent="A", counted=True)
        assert [l["parent_item"] for l in kept] == ["A"]
