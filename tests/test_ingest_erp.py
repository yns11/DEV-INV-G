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
ITEM_COLUMNS_FIXTURE = (
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
    return [base[c] for c in ITEM_COLUMNS_FIXTURE]


def bom_row(**overrides: Any) -> list[Any]:
    base = {
        "parent_itemid": "mass-00048312", "parent_name": "MEL M4",
        "child_itemid": "P-00003818", "child_qty": "8.0", "child_unitid": "PCE",
        "statut": "Actif",
    }
    base.update(overrides)
    return [base[c] for c in ("parent_itemid", "parent_name", "child_itemid",
                              "child_qty", "child_unitid", "statut")]


class _FakeClient:
    """Answers one canned result set and records the statements it was given."""

    def __init__(self, rows: list[list[Any]], *, state: str = "SUCCEEDED",
                 error: str = "", chunks: list[list[list[Any]]] | None = None) -> None:
        self.rows = rows
        self.state = state
        self.error = error
        self.chunks = chunks or []
        self.statements: list[str] = []
        self.kwargs: list[dict[str, Any]] = []
        self.statement_execution = self

    def execute_statement(self, *, statement: str, **kwargs: Any) -> Any:
        # Mimic what the SDK actually does with these arguments, so a value of
        # the wrong *type* fails here rather than in production. Swallowing
        # **kwargs is what let ``on_wait_timeout="CANCEL"`` — a string where the
        # SDK reads ``.value`` — reach a real warehouse.
        self.kwargs.append(kwargs)
        assert isinstance(kwargs["wait_timeout"], str)
        assert kwargs["on_wait_timeout"].value in ("CANCEL", "CONTINUE")
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
    client = _FakeClient(rows, **kwargs)
    reader = ErpReader(client=client, warehouse_id="wh-1")
    return reader.fetch_bom_links(limit=1000), client


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
            "statut": "Actif",
        }

    def test_the_parent_designation_is_joined_in(self):
        _, client = read_boms([bom_row()])
        assert "LEFT JOIN" in client.statements[0]

    def test_every_version_is_read_in_force_or_not(self):
        """Filtering here would hide the difference that matters.

        An assembly whose only recipe is retired *has* a structure; one the ERP
        has no recipe for at all does not. Dropping the retired rows at read
        time makes the two indistinguishable, and it was reporting the first as
        the second that produced a page of alerts nobody could act on.
        """
        _, client = read_boms([bom_row()])
        assert "WHERE" not in client.statements[0]

    def test_the_status_comes_across_verbatim(self):
        """The mapper decides what counts as in force — once, for every mode."""
        rows, _ = read_boms([bom_row(statut="Inactif")])
        assert rows[0]["statut"] == "Inactif"

    def test_a_row_without_a_status_is_taken_as_in_force(self):
        """A source that predates the column is a source of live recipes."""
        rows, _ = read_boms([bom_row(statut=None)])
        assert rows[0]["statut"] == "Actif"


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
            read_items([], state="FAILED", error="TABLE_OR_VIEW_NOT_FOUND: silver_bom")

    def test_any_other_refusal_surfaces_the_provider_s_own_words(self):
        with pytest.raises(UpstreamError, match="ANALYSIS_EXCEPTION"):
            read_items([], state="FAILED",
                       error="[ANALYSIS_EXCEPTION] cannot resolve column")

    def test_a_short_row_does_not_raise(self):
        """A column dropped from the silver table must not take the app down."""
        rows, _ = read_items([["P-1", "CARTER"]])
        assert rows[0]["item_number"] == "P-1"
        assert rows[0]["std_price"] == 0.0


class TestWhichTablesAreRead:
    """The fully-qualified names, pinned.

    They were mistyped once (``silvr_`` for ``silver_``), and a wrong table name
    fails at read time with an error that looks like a permissions problem. A
    test costs nothing and makes the next rename deliberate.
    """

    def test_the_default_tables_are_the_ones_the_dictionary_documents(self):
        from inventory.config import get_settings

        settings = get_settings()
        assert settings.erp_items_fqn == (
            "emotors_data_champions.silver_erp_ye.silver_base_article"
        )
        assert settings.erp_bom_fqn == (
            "emotors_data_champions.silver_erp_ye.silver_bom"
        )

    def test_each_read_targets_its_own_table(self):
        _, items = read_items([item_row()])
        _, boms = read_boms([bom_row()])
        assert "silver_base_article" in items.statements[0]
        assert "silver_bom" in boms.statements[0]


class TestTheSdkContract:
    """What the read hands to ``execute_statement``.

    The SDK builds its request body with ``on_wait_timeout.value``, so a plain
    string raises ``'str' object has no attribute 'value'`` — an error that only
    appears against a real warehouse, and says nothing about its cause. These
    tests pin the shapes the SDK will accept.
    """

    def test_the_wait_timeout_is_a_duration_string(self):
        _, client = read_items([item_row()])
        assert client.kwargs[0]["wait_timeout"].endswith("s")

    def test_the_timeout_policy_carries_a_value_the_sdk_can_read(self):
        _, client = read_items([item_row()])
        assert client.kwargs[0]["on_wait_timeout"].value == "CANCEL"

    def test_a_read_that_times_out_says_so_rather_than_looking_like_a_refusal(self):
        """CANCELED is the wait expiring, not the warehouse saying no."""
        with pytest.raises(ValidationError, match="dépassé"):
            read_items([], state="CANCELED")


class TestAMissingGrant:
    """The refusal an administrator actually receives.

    Unity Catalog answers a missing privilege with a SQLSTATE and a sentence
    about a « User » — which, in an App, is the *application's* service
    principal and not the person reading the screen. Passing that through
    verbatim sends everybody to check their own access, where they find they can
    query the table perfectly well. The message has to name the grant to run.
    """

    #: The exact text the warehouse returned in production.
    REAL = (
        "[INSUFFICIENT_PERMISSIONS] Insufficient privileges:\n"
        "User does not have USE CATALOG on Catalog 'emotors_data_champions'. "
        "SQLSTATE: 42501"
    )

    def refusal(self, error: str) -> str:
        with pytest.raises(ValidationError) as raised:
            read_items([], state="FAILED", error=error)
        return str(raised.value)

    def test_it_is_a_problem_to_fix_not_an_upstream_outage(self):
        """UpstreamError reads as « retry later »; this never fixes itself."""
        with pytest.raises(ValidationError):
            read_items([], state="FAILED", error=self.REAL)

    def test_the_grant_to_run_is_spelled_out(self):
        assert "GRANT USE CATALOG ON CATALOG emotors_data_champions" in (
            self.refusal(self.REAL)
        )

    def test_it_says_whose_rights_are_missing(self):
        """The single fact that stops the wrong person debugging their own access."""
        message = self.refusal(self.REAL)
        assert "service principal" in message

    def test_the_named_principal_is_the_app_s_own(self, monkeypatch):
        from inventory.config import get_settings

        monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-1234")
        get_settings.cache_clear()
        try:
            assert "`sp-1234`" in self.refusal(self.REAL)
        finally:
            monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
            get_settings.cache_clear()

    @pytest.mark.parametrize("privilege,kind,name", [
        ("USE SCHEMA", "Schema", "emotors_data_champions.silver_erp_ye"),
        ("SELECT", "Table", "emotors_data_champions.silver_erp_ye.silver_bom"),
    ])
    def test_each_missing_privilege_is_echoed_back_as_its_own_grant(
        self, privilege, kind, name
    ):
        """Three grants are needed and they fail one at a time."""
        message = self.refusal(
            f"[INSUFFICIENT_PERMISSIONS] User does not have {privilege} on "
            f"{kind} '{name}'. SQLSTATE: 42501"
        )
        assert f"GRANT {privilege} ON {kind.upper()} {name}" in message

    def test_an_unparsed_refusal_still_points_at_the_catalog(self):
        """A reworded platform message must not degrade into « refusé »."""
        message = self.refusal("PERMISSION_DENIED: not authorized")
        assert "GRANT USE CATALOG ON CATALOG emotors_data_champions" in message

    def test_the_fallback_is_named_so_the_load_is_not_blocked(self):
        assert "fichier" in self.refusal(self.REAL)


class TestTheLocalMirror:
    """Reading the copy instead of the catalogue.

    The mirror exists for one situation: the application's service principal
    cannot be granted USE CATALOG on the ERP's catalogue, and no catalogue owner
    is reachable. The copy is fed by a job running under an identity that does
    have the access.

    What must not change is everything downstream. The mirror holds the ERP's
    own column names, so the same translation runs on the same tuple order — a
    mirror that translated on its own would be a second vocabulary to keep in
    step, and it would drift.
    """

    def rows_from_mirror(self, monkeypatch, fetched, kind="items"):
        """Run a fetch with the mirror as the source and a stubbed database."""
        from inventory.config import get_settings
        from inventory.ingest import erp

        monkeypatch.setenv("INV_ERP_SOURCE", "mirror")
        get_settings.cache_clear()
        seen: dict = {}

        def fake_rows(source, columns, *, order_by, limit, where=""):
            seen.update(source=source, columns=list(columns),
                        order_by=order_by, limit=limit, where=where)
            return fetched

        monkeypatch.setattr(erp, "_mirror_rows", fake_rows)
        try:
            reader = erp.ErpReader()
            rows = (reader.fetch_items(limit=1000) if kind == "items"
                    else reader.fetch_bom_links(limit=1000))
        finally:
            monkeypatch.delenv("INV_ERP_SOURCE", raising=False)
            get_settings.cache_clear()
        return rows, seen

    def test_an_article_is_translated_exactly_as_it_would_be_from_the_catalogue(
        self, monkeypatch
    ):
        """The one property that makes the mirror a source and not a fork."""
        direct, _ = read_items([item_row(item_group_id="PFINI",
                                         std_cost_price="250.0",
                                         std_price_unit="100",
                                         programme="Commun")])
        mirrored, _ = self.rows_from_mirror(
            monkeypatch,
            [item_row(item_group_id="PFINI", std_cost_price="250.0",
                      std_price_unit="100", programme="Commun")],
        )
        assert mirrored == direct
        assert mirrored[0]["item_type"] == "FINISHED"
        assert mirrored[0]["std_price"] == pytest.approx(2.5)
        assert mirrored[0]["commonality"] == "COMMON"

    def test_a_bom_link_is_translated_the_same_way_too(self, monkeypatch):
        direct, _ = read_boms([bom_row()])
        mirrored, _ = self.rows_from_mirror(monkeypatch, [bom_row()], kind="boms")
        assert mirrored == direct

    def test_the_mirror_tables_are_read_not_the_catalogue_ones(self, monkeypatch):
        _, seen = self.rows_from_mirror(monkeypatch, [item_row()])
        assert seen["source"] == "erp_base_article"
        assert "emotors_data_champions" not in seen["source"]

    def test_the_parent_designation_is_still_joined_in(self, monkeypatch):
        """Same query shape, so the grid shows the same thing either way."""
        _, seen = self.rows_from_mirror(monkeypatch, [bom_row()], kind="boms")
        assert "LEFT JOIN erp_base_article" in seen["source"]

    def test_the_read_stays_ordered_and_bounded(self, monkeypatch):
        _, seen = self.rows_from_mirror(monkeypatch, [item_row()])
        assert seen["order_by"] == "item_id"
        assert seen["limit"] == 1000

    def test_no_warehouse_is_needed(self, monkeypatch):
        """That is the whole point: no SQL warehouse, no Unity Catalog grant."""
        from inventory.config import get_settings
        from inventory.ingest.erp import erp_available

        monkeypatch.setenv("INV_ERP_SOURCE", "mirror")
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
        monkeypatch.setenv("PGHOST", "db.example")
        monkeypatch.setenv("PGDATABASE", "inv")
        monkeypatch.setenv("PGUSER", "app")
        get_settings.cache_clear()
        try:
            assert erp_available() is True
        finally:
            for name in ("INV_ERP_SOURCE", "PGHOST", "PGDATABASE", "PGUSER"):
                monkeypatch.delenv(name, raising=False)
            get_settings.cache_clear()

    def test_a_client_passed_in_still_reads_the_catalogue(self, monkeypatch):
        """The tests above, and any explicit caller, must not be hijacked."""
        from inventory.config import get_settings

        monkeypatch.setenv("INV_ERP_SOURCE", "mirror")
        get_settings.cache_clear()
        try:
            rows, client = read_items([item_row()])
            assert "silver_base_article" in client.statements[0]
            assert rows[0]["item_number"] == "P-00003436"
        finally:
            monkeypatch.delenv("INV_ERP_SOURCE", raising=False)
            get_settings.cache_clear()


class TestTheColumnContract:
    """One declaration of the column order, read by both transports.

    ``_item_row`` unpacks a tuple positionally. A column added to the catalogue
    query but not to the mirror's — or the reverse — would shift every field by
    one and load prices into unit codes, with nothing raising.
    """

    def test_the_catalogue_query_selects_the_declared_columns(self):
        from inventory.ingest.erp import ITEM_COLUMNS

        _, client = read_items([item_row()])
        for column in ITEM_COLUMNS:
            assert column in client.statements[0]

    def test_the_declared_order_is_the_one_the_translation_unpacks(self):
        """ITEM_COLUMNS is the contract; this is what makes it one."""
        from inventory.ingest.erp import ITEM_COLUMNS, _item_row

        row = _item_row([f"<{c}>" for c in ITEM_COLUMNS])
        assert row["item_number"] == "<item_id>"
        assert row["name"] == "<item_name>"
        assert row["category"] == "<categorie>"
        assert row["unit"] == "<std_unit>"

    def test_the_test_fixture_declares_the_same_columns(self):
        """Otherwise every row built here would be shifted, and pass anyway."""
        from inventory.ingest.erp import ITEM_COLUMNS

        assert ITEM_COLUMNS == ITEM_COLUMNS_FIXTURE
