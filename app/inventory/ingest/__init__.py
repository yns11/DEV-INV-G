"""File and clipboard ingestion, driven by explicit column contracts."""

from .contracts import CONTRACTS, FieldSpec, GridContract, get_contract, list_contracts
from .mappers import (
    ImportedJournalLine,
    PreparedSheetRow,
    map_adjustments,
    map_backflush,
    map_bom_links,
    map_book_stock,
    map_count_sheets,
    map_items,
    map_journal_lines,
    map_locations,
    map_stock_flow_inputs,
    map_zones,
)
from .parser import (
    ParseResult,
    RowError,
    normalise_header,
    parse_clipboard,
    parse_rows,
    parse_tabular_bytes,
    read_table,
)

__all__ = [
    "CONTRACTS", "FieldSpec", "GridContract", "get_contract", "list_contracts",
    "ParseResult", "RowError", "normalise_header", "parse_clipboard", "parse_rows",
    "parse_tabular_bytes", "read_table",
    "ImportedJournalLine", "PreparedSheetRow", "map_adjustments", "map_backflush",
    "map_bom_links", "map_book_stock", "map_count_sheets", "map_items",
    "map_journal_lines", "map_locations", "map_stock_flow_inputs", "map_zones",
]
