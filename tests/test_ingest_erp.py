"""Translating the ERP's vocabulary into the campaign's.

The silver tables speak Dynamics: a functional group code for the article type,
a price that may be quoted per *n* units, ``Commun`` for a shared article. Every
one of those is a decision, and getting one wrong is invisible until the
variance stage — a price divided by the wrong divisor values a whole campaign at
a hundred times its worth and nothing upstream complains.
"""

from __future__ import annotations

from typing import Any

import pytest

from inventory.errors import UpstreamError, ValidationError
from inventory.ingest.erp import ErpReader

#: Column order of the items query, so a row here reads like the table does.
ITEM_COLUMNS = (
    "item_id", "item_name", "item_description", "search_name", "name_alias",
    "categorie", "programme", "item_group_id", "item_group_label",
    "std_cost_price", "std_price_unit", "std_unit",
)


def item_row(**overrides: Any) -> list[Any]:
    base = {
        "item_id": "P-00003436", "item_name": "PRINCIPAL HOUSING",
        "item_description": None, "search_name": "Carter Princ Usiné",
        "name_alias": "Carter Principal", "categorie": "CARTER",
        "programme": "M2BEV", "item_group_id": "COMPO",
        "item_group_label": "Composant", "std_cost_price": "6063.30",
        "std_price_unit": "1.0", "std_unit": "PCE",
    }
    base.update(overrides)
    return [base[c] for c in ITEM_COLUMNS]


def bom_row(**overrides: Any) -> list[Any]:
    base = {
        "parent_itemid": "mass-00048312", "parent_name": "MEL M4",
        "child_itemid": "P-00003818", "child_qty": "8.0", "child_unitid": "PCE",
    }
    base.update(overrides)
    return [base[c] for c in
            ("parent_itemid", "parent_name", "child_itemid", "child_qty", "child_unitid")]


class _FakeClient:
    """Answers one canned result set and records the statements it was given."""

    def __init__(self, rows: list[list[Any]], *, state: str = "SUCCEEDED",
                 error: str = "", chunks: list[list[list[Any]]] | None = None) -> None:
        self.rows = rows
        self.state = state
        self.error = error
        self.chunks = chunks or []
        self.statements: list[str] = []
        self.statement_execution = self

    def execute_statement(self, *, statement: str, **kwargs: Any) -> Any:
        self.statements.append(statement)
        return _Response(
            self.rows, self.state, self.error,
            next_chunk=0 if self.chunks else None,
        )

    def get_statement_result_chunk_n(self, *, statement_id: str, chunk_index: int) -> Any:
        rows = self.chunks[chunk_index]
        has_more = chunk_index + 1 < len(self.chunks)
        return _Chunk(rows, chunk_index + 1 if has_more else None)


class _Response:
    def __init__(self, rows, state, error, next_chunk=None):
        self.statement_id = "stmt-1"
        self.status = type("S", (), {
            "state": type("E", (), {"__str__": lambda s: state})(),
            "error": type("Err", (), {"message": error})() if error else None,
        })()
        self.result = _Chunk(rows, next_chunk)


class _Chunk:
    def __init__(self, rows, next_chunk_index=None):
        self.data_array = rows
        self.next_chunk_index = next_chunk_index


def read_items(rows, **kwargs):
    client = _FakeClient(rows, **kwargs)
    return ErpReader(client=client, warehouse_id="wh-1").fetch_items(limit=1000), client


def read_boms(rows, **kwargs):
    approved_only = kwargs.pop("approved_only", False)
    client = _FakeClient(rows, **kwargs)
    reader = ErpReader(client=client, warehouse_id="wh-1")
    return reader.fetch_bom_links(limit=1000, approved_only=approved_only), client


class TestArticleTranslation:
    def test_the_business_key_and_designation_come_across(self):
        rows, _ = read_items([item_row()])
        assert rows[0]["item_number"] == "P-00003436"
        assert rows[0]["name"] == "PRINCIPAL HOUSING"

    def test_the_designation_falls_back_when_the_erp_left_it_blank(self):
        """A blank designation on a printed counting sheet helps nobody."""
        rows, _ = read_items([item_row(item_name=None)])
        assert rows[0]["name"] == "Carter Principal"
        rows, _ = read_items([item_row(item_name=None, name_alias=None,
                                       item_description="Carter, version usinée")])
        assert rows[0]["name"] == "Carter, version usinée"

    @pytest.mark.parametrize("group,expected", [
        ("COMPO", "COMPONENT"),
        ("PFINI", "FINISHED"),
        ("PSMFI", "SEMI_FINISHED"),
        ("APVPR", "COMPONENT"),
    ])
    def test_the_functional_group_decides_the_article_type(self, group, expected):
        rows, _ = read_items([item_row(item_group_id=group)])
        assert rows[0]["item_type"] == expected

    @pytest.mark.parametrize("group", ["SSTRA", "PRESTA"])
    def test_a_non_stock_group_is_left_unknown_rather_than_guessed(self, group):
        """A subcontracted operation valued as a component distorts the variance."""
        rows, _ = read_items([item_row(item_group_id=group)])
        assert rows[0]["item_type"] == "UNKNOWN"

    def test_an_unmapped_group_is_left_unknown(self):
        rows, _ = read_items([item_row(item_group_id="NOUVEAU")])
        assert rows[0]["item_type"] == "UNKNOWN"

    def test_the_category_and_programme_come_across(self):
        rows, _ = read_items([item_row()])
        assert rows[0]["category"] == "CARTER"
        assert rows[0]["program"] == "M2BEV"

    def test_commun_is_a_shared_article_not_a_programme_named_commun(self):
        rows, _ = read_items([item_row(programme="Commun")])
        assert rows[0]["commonality"] == "COMMON"

    def test_a_named_programme_makes_the_article_specific(self):
        rows, _ = read_items([item_row(programme="M3GEN2")])
        assert rows[0]["commonality"] == "SPECIFIC"

    def test_no_programme_leaves_the_specificity_unknown(self):
        rows, _ = read_items([item_row(programme=None)])
        assert rows[0]["commonality"] == "UNKNOWN"

    def test_the_exclusion_scope_is_never_inferred_from_the_erp(self):
        """It is a campaign decision, made in the app and editable there."""
        rows, _ = read_items([item_row()])
        assert rows[0]["exclusions"] == ""


class TestStandardCost:
    def test_a_price_quoted_per_unit_comes_across_as_is(self):
        rows, _ = read_items([item_row(std_cost_price="6063.30", std_price_unit="1.0")])
        assert rows[0]["std_price"] == pytest.approx(6063.30)

    def test_a_price_quoted_per_hundred_is_brought_back_to_one(self):
        """Ignoring the divisor values the campaign at a hundred times its worth."""
        rows, _ = read_items([item_row(std_cost_price="250.0", std_price_unit="100")])
        assert rows[0]["std_price"] == pytest.approx(2.5)

    def test_a_missing_divisor_is_treated_as_one_not_as_a_division_by_zero(self):
        for divisor in (None, "", "0"):
            rows, _ = read_items([item_row(std_cost_price="12.5", std_price_unit=divisor)])
            assert rows[0]["std_price"] == pytest.approx(12.5)

    def test_an_article_without_a_standard_cost_is_worth_zero_not_broken(self):
        rows, _ = read_items([item_row(std_cost_price=None)])
        assert rows[0]["std_price"] == 0.0

    def test_the_unit_falls_back_to_pieces(self):
        rows, _ = read_items([item_row(std_unit=None)])
        assert rows[0]["unit"] == "PCE"


class TestBomTranslation:
    def test_a_link_comes_across_with_its_quantity(self):
        rows, _ = read_boms([bom_row()])
        assert rows[0] == {
            "parent_item": "mass-00048312",
            "parent_name": "MEL M4",
            "child_item": "P-00003818",
            "qty_per": 8.0,
            "unit": "PCE",
        }

    def test_the_parent_designation_is_joined_in(self):
        _, client = read_boms([bom_row()])
        assert "LEFT JOIN" in client.statements[0]

    def test_an_unapproved_recipe_is_kept_by_default(self):
        """Dropping every row whose flag is null looks like an empty BOM."""
        _, client = read_boms([bom_row()])
        assert "approved" not in client.statements[0]

    def test_it_can_be_restricted_to_approved_recipes(self):
        _, client = read_boms([bom_row()], approved_only=True)
        assert "b.approved = 1" in client.statements[0]


class TestReadingTheWholeTable:
    def test_every_chunk_is_read_not_just_the_first(self):
        """A truncated referential is the exact failure this app exists to remove."""
        client = _FakeClient(
            [item_row(item_id="P-1")],
            chunks=[[item_row(item_id="P-2")], [item_row(item_id="P-3")]],
        )
        rows = ErpReader(client=client, warehouse_id="wh-1").fetch_items(limit=1000)
        assert [r["item_number"] for r in rows] == ["P-1", "P-2", "P-3"]

    def test_the_read_is_ordered_so_a_truncation_is_a_prefix(self):
        _, client = read_items([item_row()])
        assert "ORDER BY item_id" in client.statements[0]
        assert "LIMIT 1000" in client.statements[0]


class TestFailures:
    def test_a_missing_table_says_so_instead_of_returning_nothing(self):
        with pytest.raises(ValidationError, match="introuvable"):
            read_items([], state="FAILED", error="TABLE_OR_VIEW_NOT_FOUND: silvr_bom")

    def test_any_other_refusal_surfaces_the_provider_s_own_words(self):
        with pytest.raises(UpstreamError, match="PERMISSION_DENIED"):
            read_items([], state="FAILED", error="PERMISSION_DENIED on schema")

    def test_a_short_row_does_not_raise(self):
        """A column dropped from the silver table must not take the app down."""
        rows, _ = read_items([["P-1", "CARTER"]])
        assert rows[0]["item_number"] == "P-1"
        assert rows[0]["std_price"] == 0.0
