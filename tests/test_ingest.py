"""Column contracts, parsing and mapping."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from inventory.domain.enums import (
    AdjustmentKind,
    CountSection,
    DataSource,
    ExclusionScope,
    ItemType,
)
from inventory.domain.quantities import to_decimal
from inventory.ingest import (
    get_contract,
    map_adjustments,
    map_book_stock,
    map_items,
    map_journal_lines,
    map_sheet_lines,
    normalise_header,
    parse_clipboard,
    parse_rows,
)

next_id = lambda: "id"


class TestHeaderMatching:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Numéro d'article", "numerodarticle"),
            ("NUMERO D ARTICLE", "numerodarticle"),
            ("  Stock  physique ", "stockphysique"),
            ("WarehouseLocationId", "warehouselocationid"),
            ("Unité de stock", "unitedestock"),
        ],
    )
    def test_normalises_accents_case_and_punctuation(self, raw, expected):
        assert normalise_header(raw) == expected

    def test_erp_aliases_resolve_to_contract_fields(self):
        contract = get_contract("book_stock")
        result = parse_rows(
            contract,
            [{"item_number": "P-1", "warehouse_id": "B06", "location_id": "L1",
              "qty": "10"}],
        )
        assert result.rows[0]["item_number"] == "P-1"
        assert result.ok


class TestNumberParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1234.56", Decimal("1234.56")),
            ("1 234,56", Decimal("1234.56")),
            ("1.234,56", Decimal("1234.56")),
            ("1,234.56", Decimal("1234.56")),
            ("-8.67", Decimal("-8.67")),
            (42, Decimal("42")),
        ],
    )
    def test_accepts_both_french_and_english_separators(self, raw, expected):
        assert to_decimal(raw) == expected

    def test_unparseable_value_raises_instead_of_silently_becoming_zero(self):
        with pytest.raises(ValueError):
            to_decimal("douze")

    def test_boolean_is_rejected_as_a_quantity(self):
        with pytest.raises(ValueError):
            to_decimal(True)


class TestParsing:
    def test_missing_required_column_is_reported(self):
        contract = get_contract("items")
        result = parse_rows(contract, [{"name": "sans référence"}])
        assert result.rows == []
        assert result.errors[0].column == "item_number"
        assert "obligatoire" in result.errors[0].message

    def test_bad_number_rejects_the_row_with_its_line_number(self):
        contract = get_contract("book_stock")
        result = parse_rows(
            contract,
            [
                {"item_number": "P-1", "warehouse_id": "B", "qty": "10"},
                {"item_number": "P-2", "warehouse_id": "B", "qty": "beaucoup"},
            ],
        )
        assert len(result.rows) == 1
        assert result.errors[0].line == 3  # header is line 1
        assert result.errors[0].value == "beaucoup"

    def test_duplicate_natural_keys_are_reported(self):
        contract = get_contract("items")
        result = parse_rows(
            contract, [{"item_number": "P-1"}, {"item_number": "P-1"}]
        )
        assert len(result.duplicate_keys) == 1

    def test_row_limit_is_enforced(self):
        contract = get_contract("items")
        result = parse_rows(
            contract, [{"item_number": f"P-{i}"} for i in range(10)], max_rows=5
        )
        assert len(result.rows) == 5
        assert "limité" in result.errors[0].message


class TestClipboard:
    def test_paste_without_header_uses_the_contract_order(self):
        contract = get_contract("count_sheet_lines")
        result = parse_clipboard(
            contract, "P-001\tVIS M6\tBDL\t2 725\tPCE", has_header=False
        )
        assert result.rows[0]["item_number"] == "P-001"
        assert result.rows[0]["qty"] == Decimal("2725")

    def test_paste_with_header_is_detected(self):
        contract = get_contract("count_sheet_lines")
        text = "Référence\tDésignation\tSection\tComptage\n" "P-001\tVIS\tBDL\t12"
        result = parse_clipboard(contract, text)
        assert len(result.rows) == 1
        assert result.rows[0]["item_number"] == "P-001"

    def test_semicolon_separated_paste(self):
        contract = get_contract("items")
        result = parse_clipboard(contract, "P-001;Vis;COMPONENT", has_header=False)
        assert result.rows[0]["name"] == "Vis"


class TestItemMapping:
    def test_free_text_type_is_mapped_onto_the_enum(self):
        items, errors = map_items(
            "c",
            [{"item_number": "P-1", "item_type": "Produit Fini"}],
            source=DataSource.FILE_IMPORT,
        )
        assert errors == []
        assert items[0].item_type is ItemType.FINISHED

    def test_unknown_type_falls_back_to_unknown_rather_than_guessing(self):
        items, _ = map_items(
            "c", [{"item_number": "P-1", "item_type": "zzz"}],
            source=DataSource.FILE_IMPORT,
        )
        assert items[0].item_type is ItemType.UNKNOWN

    def test_legacy_exclusion_flag_means_exclude_everywhere(self):
        items, _ = map_items(
            "c", [{"item_number": "P-1", "exclusions": "X"}],
            source=DataSource.FILE_IMPORT,
        )
        assert items[0].exclusions == {ExclusionScope.ALL}
        assert items[0].excluded_everywhere

    def test_multiple_exclusion_scopes(self):
        items, _ = map_items(
            "c", [{"item_number": "P-1", "exclusions": "GENERIC,BOM"}],
            source=DataSource.FILE_IMPORT,
        )
        assert items[0].excluded_from_generic
        assert items[0].excluded_from_bom
        assert not items[0].excluded_everywhere

    def test_item_numbers_are_normalised_so_case_cannot_split_an_article(self):
        items, _ = map_items(
            "c", [{"item_number": " mass-00040922 "}], source=DataSource.FILE_IMPORT
        )
        assert items[0].item_number == "MASS-00040922"


class TestBookStockMapping:
    def test_duplicate_triples_are_summed_not_overwritten(self):
        lines, _ = map_book_stock(
            "c",
            [
                {"item_number": "P-1", "warehouse_id": "B", "location_id": "L",
                 "qty": "10"},
                {"item_number": "P-1", "warehouse_id": "B", "location_id": "L",
                 "qty": "5"},
            ],
        )
        assert len(lines) == 1
        assert lines[0].qty == Decimal("15.000000")

    def test_missing_unit_cost_falls_back_to_the_referential_price(self):
        from inventory.domain.models import Item

        items = {"P-1": Item(campaign_id="c", item_number="P-1", std_price="42")}
        lines, _ = map_book_stock(
            "c",
            [{"item_number": "P-1", "warehouse_id": "B", "location_id": "L",
              "qty": "2"}],
            items=items,
        )
        assert lines[0].unit_cost == Decimal("42.00")
        assert lines[0].value == Decimal("84.00")


class TestJournalMapping:
    def test_maps_the_odata_export(self):
        lines, errors, warnings = map_journal_lines(
            [
                {
                    "journal_number": "NPEM-1", "item_number": "P-1",
                    "warehouse_id": "B06", "location_id": "PAL 01",
                    "counted_quantity": Decimal("8731"), "is_posted": True,
                    "journal_name_id": "INVE",
                }
            ]
        )
        assert errors == [] and warnings == []
        assert lines[0].qty == Decimal("8731")
        assert lines[0].is_posted is True
        assert str(lines[0].kind) == "INVE"

    def test_recovers_a_missing_location_from_the_journal_and_says_so(self):
        """A real production export dropped this cell on exactly one row."""
        rows = [
            {"journal_number": "J1", "item_number": "P-1", "warehouse_id": "B06VRAC",
             "location_id": "GENERIQUE", "counted_quantity": Decimal("10")},
            {"journal_number": "J1", "item_number": "P-2", "warehouse_id": "B06VRAC",
             "location_id": None, "counted_quantity": Decimal("15")},
        ]
        lines, errors, warnings = map_journal_lines(rows)
        assert errors == []
        assert len(lines) == 2
        assert lines[1].location_id == "GENERIQUE"
        assert "déduit" in warnings[0].message

    def test_ambiguous_missing_location_is_rejected_not_guessed(self):
        rows = [
            {"journal_number": "J1", "item_number": "P-1", "warehouse_id": "B",
             "location_id": "L1", "counted_quantity": Decimal("1")},
            {"journal_number": "J1", "item_number": "P-2", "warehouse_id": "B",
             "location_id": "L2", "counted_quantity": Decimal("1")},
            {"journal_number": "J1", "item_number": "P-3", "warehouse_id": "B",
             "location_id": None, "counted_quantity": Decimal("1")},
        ]
        lines, errors, _ = map_journal_lines(rows)
        assert len(lines) == 2
        assert "ambigu" in errors[0].message

    def test_missing_quantity_is_an_error(self):
        _, errors, _ = map_journal_lines(
            [{"item_number": "P-1", "warehouse_id": "B", "counted_quantity": None}]
        )
        assert errors[0].column == "counted_quantity"


class TestSheetLineMapping:
    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("BDL", CountSection.LINE_SIDE),
            ("Composants en bord de ligne", CountSection.LINE_SIDE),
            ("MOM_WAITING", CountSection.WIP),
            ("MOM waiting", CountSection.WIP),
            ("Statut MOM: Waiting for decision / on progress", CountSection.WIP),
            ("Eclatee", CountSection.WIP),
            ("MOM_OK", CountSection.WIP_OK),
            ("Statut MOM: OK", CountSection.WIP_OK),
            ("WIP", CountSection.WIP),
        ],
    )
    def test_legacy_section_labels_resolve(self, legacy, expected):
        lines, errors = map_sheet_lines(
            "c", "s", [{"item_number": "P-1", "section": legacy, "qty": "5"}],
            source=DataSource.MANUAL, id_factory=next_id,
        )
        assert errors == []
        assert lines[0].section is expected

    def test_unknown_section_is_an_error_not_a_silent_default(self):
        """Defaulting would skip a BOM explosion and lose a whole assembly."""
        lines, errors = map_sheet_lines(
            "c", "s", [{"item_number": "P-1", "section": "???", "qty": "5"}],
            source=DataSource.MANUAL, id_factory=next_id,
        )
        assert lines == []
        assert errors[0].column == "section"

    def test_blank_quantity_stays_blank(self):
        lines, _ = map_sheet_lines(
            "c", "s", [{"item_number": "P-1", "section": "BDL", "qty": None}],
            source=DataSource.MANUAL, id_factory=next_id,
        )
        assert lines[0].is_counted is False
        assert lines[0].qty_manual is None

    def test_manual_and_imported_values_land_in_different_columns(self):
        manual, _ = map_sheet_lines(
            "c", "s", [{"item_number": "P-1", "section": "BDL", "qty": "5"}],
            source=DataSource.MANUAL, id_factory=next_id,
        )
        imported, _ = map_sheet_lines(
            "c", "s", [{"item_number": "P-1", "section": "BDL", "qty": "5"}],
            source=DataSource.SCAN_AI, id_factory=next_id,
        )
        assert manual[0].qty_manual == Decimal("5.000000")
        assert manual[0].qty_imported is None
        assert imported[0].qty_imported == Decimal("5.000000")
        assert imported[0].qty_manual is None


class TestAdjustmentMapping:
    def test_erp_movement_labels_are_recognised(self):
        lines, _ = map_adjustments(
            "c",
            [
                {"item_number": "P-1", "kind": "Comptage", "qty": "-26"},
                {"item_number": "P-2", "kind": "Ajustement de stock", "qty": "53"},
            ],
            source=DataSource.FILE_IMPORT,
            id_factory=next_id,
        )
        assert lines[0].kind is AdjustmentKind.COUNT
        assert lines[1].kind is AdjustmentKind.ADJUSTMENT

    def test_dates_parse_in_both_common_formats(self):
        contract = get_contract("adjustments")
        result = parse_rows(
            contract,
            [
                {"item_number": "P-1", "physical_date": "13/06/2026", "qty": "1"},
                {"item_number": "P-2", "physical_date": "2026-06-13", "qty": "1"},
            ],
        )
        assert result.rows[0]["physical_date"] == dt.date(2026, 6, 13)
        assert result.rows[1]["physical_date"] == dt.date(2026, 6, 13)
