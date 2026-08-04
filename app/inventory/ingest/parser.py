"""Tabular parsing against a :class:`~inventory.ingest.contracts.GridContract`.

Handles the three ways data enters the application, all through the same code
path so they cannot drift apart:

* an uploaded ``.xlsx`` / ``.xls`` / ``.csv`` / ``.tsv`` file;
* a clipboard paste (the user selects a block in Excel and hits Ctrl-V in a
  grid cell — the spec asks for this explicitly);
* rows typed directly into the grid and posted as JSON.

Two design decisions matter:

**Nothing is dropped silently.** A row that fails validation produces a
:class:`RowError` carrying the source line number, the offending column and the
raw value. The legacy Power Query filtered bad rows away with
``Table.SelectRows`` and nobody ever knew.

**Headers are matched leniently, values strictly.** Accents, case, punctuation
and non-breaking spaces are normalised when matching a header against the
contract's aliases, so an ERP export loads unchanged. Values, on the other hand,
must parse — an unparseable quantity is an error, never a zero.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain.quantities import to_decimal
from ..errors import ValidationError
from .contracts import FieldSpec, GridContract

__all__ = [
    "RowError",
    "ParseResult",
    "normalise_header",
    "parse_rows",
    "parse_tabular_bytes",
    "parse_clipboard",
    "read_table",
]


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class RowError:
    """One rejected row, with everything needed to fix it."""

    line: int
    column: str
    value: Any
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "value": None if self.value is None else str(self.value)[:200],
            "message": self.message,
        }


@dataclass(slots=True)
class ParseResult:
    """Outcome of parsing a table against a contract."""

    contract_key: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    #: Headers found in the source that the contract does not know about.
    unknown_columns: list[str] = field(default_factory=list)
    #: Required contract columns missing from the source.
    missing_columns: list[str] = field(default_factory=list)
    rows_received: int = 0
    #: Rows whose natural key appears more than once in the source.
    duplicate_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing_columns

    # Deliberately no `as_report()` here. A parse result is turned into a
    # client payload by `ImportOutcome.as_dict()` and by nothing else: a second
    # serialiser for the same DTO drifted from the first, and the client — which
    # cannot tell a dry run from a commit — crashed on the key it lost.


# --------------------------------------------------------------------------- #
# Header matching
# --------------------------------------------------------------------------- #

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def normalise_header(value: Any) -> str:
    """Canonical form used to match a source header against contract aliases.

    Strips accents, case, punctuation and every kind of space, so
    ``"Numéro d'article"``, ``"NUMERO D ARTICLE"`` and ``"ItemNumber"`` all
    reduce to comparable tokens.

    >>> normalise_header("Numéro d'article")
    'numerodarticle'
    >>> normalise_header("  Stock  physique ")
    'stockphysique'
    """
    text = str(value or "")
    text = text.replace(" ", " ").replace(" ", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _PUNCT_RE.sub("", text.strip().lower())


def _alias_map(contract: GridContract) -> dict[str, FieldSpec]:
    """Every accepted header spelling → the field it feeds."""
    mapping: dict[str, FieldSpec] = {}
    for spec in contract.fields:
        for candidate in (spec.name, spec.label, *spec.aliases):
            mapping.setdefault(normalise_header(candidate), spec)
    return mapping


# --------------------------------------------------------------------------- #
# Value coercion
# --------------------------------------------------------------------------- #

_TRUE = {"1", "true", "vrai", "yes", "oui", "y", "o", "x"}
_FALSE = {"0", "false", "faux", "no", "non", "n", ""}

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y",
)


def _coerce(spec: FieldSpec, raw: Any) -> Any:
    """Convert one cell to the contract type, or raise ``ValueError``."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.replace(" ", " ").strip()
        if raw == "":
            return None

    match spec.type:
        case "string":
            return str(raw).strip()
        case "number":
            return to_decimal(raw)
        case "integer":
            value = to_decimal(raw)
            if value != value.to_integral_value():
                raise ValueError(f"{raw!r} n'est pas un entier")
            return int(value)
        case "boolean":
            text = str(raw).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise ValueError(f"{raw!r} n'est pas un booléen")
        case "date":
            return _parse_date(raw)
        case "datetime":
            return _parse_datetime(raw)
        case "enum":
            text = str(raw).strip()
            upper = text.upper().replace(" ", "_").replace("-", "_")
            if upper in spec.choices:
                return upper
            # Enum values are also matched leniently, then validated: this is
            # where "MOM waiting" becomes "WIP" without the caller caring.
            for choice in spec.choices:
                if normalise_header(choice) == normalise_header(text):
                    return choice
            return text  # left to the domain mapper, which knows the aliases
        case _:  # pragma: no cover - exhaustive over FieldType
            return raw


def _parse_date(raw: Any) -> dt.date:
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    text = str(raw).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{raw!r} n'est pas une date reconnue")


def _parse_datetime(raw: Any) -> dt.datetime:
    if isinstance(raw, dt.datetime):
        return raw
    if isinstance(raw, dt.date):
        return dt.datetime.combine(raw, dt.time.min, tzinfo=dt.UTC)
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.datetime.combine(_parse_date(raw), dt.time.min, tzinfo=dt.UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


# --------------------------------------------------------------------------- #
# Core parsing
# --------------------------------------------------------------------------- #

def parse_rows(
    contract: GridContract,
    rows: Iterable[dict[str, Any]],
    *,
    max_rows: int = 200_000,
    first_line: int = 2,
) -> ParseResult:
    """Validate already-keyed rows (JSON grid edits) against *contract*.

    :param first_line: line number attributed to the first row in error
        messages; 2 matches a spreadsheet whose row 1 is the header.
    """
    result = ParseResult(contract_key=contract.key)
    specs = {f.name: f for f in contract.fields}
    seen_keys: dict[str, int] = {}

    for offset, raw_row in enumerate(rows):
        line = first_line + offset
        result.rows_received += 1
        if result.rows_received > max_rows:
            result.errors.append(
                RowError(line, "*", None,
                         f"Import limité à {max_rows:,} lignes par lot.".replace(",", " "))
            )
            break

        clean: dict[str, Any] = {}
        row_failed = False
        for name, spec in specs.items():
            raw = raw_row.get(name, raw_row.get(spec.label))
            try:
                value = _coerce(spec, raw)
            except ValueError as exc:
                result.errors.append(RowError(line, name, raw, str(exc)))
                row_failed = True
                continue
            if value is None:
                value = spec.default
            if spec.required and (value is None or value == ""):
                result.errors.append(
                    RowError(line, name, raw,
                             f"La colonne « {spec.label} » est obligatoire.")
                )
                row_failed = True
                continue
            clean[name] = value

        if row_failed:
            continue

        if contract.natural_key:
            key = "|".join(str(clean.get(k, "")).upper() for k in contract.natural_key)
            previous = seen_keys.get(key)
            if previous is not None:
                result.duplicate_keys.append(f"{key} (lignes {previous} et {line})")
            else:
                seen_keys[key] = line

        result.rows.append(clean)

    return result


def read_table(
    payload: bytes, *, filename: str = "", sheet: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    """Read a spreadsheet or delimited file into ``(headers, rows)``.

    ``.xlsx`` is streamed read-only so a 100 000-row export does not
    materialise twice in the app container's 6 GB of RAM.
    """
    lowered = filename.lower()
    if lowered.endswith((".xlsx", ".xlsm", ".xltx")) or payload[:2] == b"PK":
        return _read_xlsx(payload, sheet=sheet)
    return _read_delimited(payload)


def _read_xlsx(
    payload: bytes, *, sheet: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    import openpyxl

    workbook = openpyxl.load_workbook(
        io.BytesIO(payload), data_only=True, read_only=True
    )
    try:
        worksheet = workbook[sheet] if sheet else workbook.worksheets[0]
        iterator = worksheet.iter_rows(values_only=True)
        headers: list[str] = []
        rows: list[list[Any]] = []
        for raw in iterator:
            values = list(raw)
            if not headers:
                if all(v is None or str(v).strip() == "" for v in values):
                    continue  # skip leading blank rows
                headers = [str(v).strip() if v is not None else "" for v in values]
                continue
            if all(v is None or str(v).strip() == "" for v in values):
                continue  # skip blank separator rows
            rows.append(values)
        return headers, rows
    finally:
        workbook.close()


def _read_delimited(payload: bytes) -> tuple[list[str], list[list[Any]]]:
    text = _decode(payload)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]
        dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(str(c).strip() for c in r)]
    if not rows:
        return [], []
    return [c.strip() for c in rows[0]], [list(r) for r in rows[1:]]


def _decode(payload: bytes) -> str:
    """Decode bytes, tolerating the encodings Excel actually emits."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _map_headers(
    contract: GridContract, headers: Sequence[str]
) -> tuple[dict[int, FieldSpec], list[str], list[str]]:
    """Match source headers onto contract fields.

    Returns ``(column index → field, unknown headers, missing required fields)``.
    """
    aliases = _alias_map(contract)
    mapping: dict[int, FieldSpec] = {}
    unknown: list[str] = []
    for index, header in enumerate(headers):
        spec = aliases.get(normalise_header(header))
        if spec is None:
            if str(header).strip():
                unknown.append(str(header))
            continue
        mapping.setdefault(index, spec)

    matched = {spec.name for spec in mapping.values()}
    missing = [f.label for f in contract.fields if f.required and f.name not in matched]
    return mapping, unknown, missing


def _rows_as_dicts(
    mapping: dict[int, FieldSpec], rows: Iterable[Sequence[Any]]
) -> Iterator[dict[str, Any]]:
    for row in rows:
        out: dict[str, Any] = {}
        for index, spec in mapping.items():
            if index < len(row):
                out[spec.name] = row[index]
        yield out


def parse_tabular_bytes(
    contract: GridContract,
    payload: bytes,
    *,
    filename: str = "",
    sheet: str | None = None,
    max_rows: int = 200_000,
) -> ParseResult:
    """Parse an uploaded file against *contract*."""
    headers, rows = read_table(payload, filename=filename, sheet=sheet)
    if not headers:
        raise ValidationError(
            "Le fichier ne contient aucune ligne d'en-tête exploitable.",
            filename=filename,
        )
    mapping, unknown, missing = _map_headers(contract, headers)
    result = parse_rows(
        contract, _rows_as_dicts(mapping, rows), max_rows=max_rows
    )
    result.unknown_columns = unknown
    result.missing_columns = missing
    return result


def parse_clipboard(
    contract: GridContract,
    text: str,
    *,
    has_header: bool | None = None,
    max_rows: int = 200_000,
) -> ParseResult:
    """Parse a tab-separated clipboard paste from Excel.

    :param has_header: when ``None``, header presence is detected by checking
        whether the first row's cells match contract aliases. Pasting a block
        *without* its header is the common case, so columns then fall back to
        the contract's declared order — which is exactly what the on-screen grid
        shows.
    """
    lines = [l for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    if not lines:
        return ParseResult(contract_key=contract.key)

    delimiter = "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else ",")
    grid = [line.split(delimiter) for line in lines]

    if has_header is None:
        aliases = _alias_map(contract)
        hits = sum(1 for cell in grid[0] if normalise_header(cell) in aliases)
        has_header = hits >= max(1, len(grid[0]) // 2)

    if has_header:
        headers = [c.strip() for c in grid[0]]
        body = grid[1:]
        mapping, unknown, missing = _map_headers(contract, headers)
    else:
        body = grid
        mapping = dict(enumerate(contract.fields))
        unknown, missing = [], []

    result = parse_rows(contract, _rows_as_dicts(mapping, body), max_rows=max_rows)
    result.unknown_columns = unknown
    result.missing_columns = missing
    return result
