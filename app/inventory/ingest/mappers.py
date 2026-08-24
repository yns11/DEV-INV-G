"""Parsed rows → domain objects.

Kept apart from :mod:`inventory.ingest.parser` so that the *shape* of a file and
the *meaning* of its values evolve independently. This is also where legacy
vocabulary is translated: an archived ``Compil GENERIQUE`` sheet with a
``MOM_WAITING`` source column maps to :attr:`CountSection.WIP` here and nowhere
else in the codebase.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..domain.enums import (
    AdjustmentKind,
    CountSection,
    DataSource,
    ExclusionScope,
    FlowKind,
    FlowSource,
    ItemCommonality,
    ItemType,
    JournalKind,
    LocationStatus,
    LocationType,
    legacy_section_alias,
)
from ..domain.models import (
    AdjustmentLine,
    BackflushLine,
    BomLink,
    BookStockLine,
    Item,
    Location,
    StockFlowInput,
    Zone,
    normalise_key,
)
from .contracts import is_active_status
from .parser import RowError

__all__ = [
    "ImportedJournalLine",
    "PreparedSheetRow",
    "map_items",
    "map_bom_links",
    "map_book_stock",
    "map_journal_lines",
    "map_count_sheets",
    "map_adjustments",
    "map_backflush",
    "map_stock_flow_inputs",
    "map_zones",
    "map_locations",
]


# --------------------------------------------------------------------------- #
# Referentials
# --------------------------------------------------------------------------- #

#: Free-text product types seen in the historical exports, mapped onto the enum.
_ITEM_TYPE_ALIASES: dict[str, ItemType] = {
    "COMPOSANT": ItemType.COMPONENT,
    "COMPO": ItemType.COMPONENT,
    "BOP": ItemType.COMPONENT,
    "COMPONENT": ItemType.COMPONENT,
    "SEMI-FINI": ItemType.SEMI_FINISHED,
    "SEMI FINI": ItemType.SEMI_FINISHED,
    "SEMIFINI": ItemType.SEMI_FINISHED,
    "SF": ItemType.SEMI_FINISHED,
    "WIP": ItemType.SEMI_FINISHED,
    "SEMI_FINISHED": ItemType.SEMI_FINISHED,
    "PRODUIT FINI": ItemType.FINISHED,
    "PRODUIT-FINI": ItemType.FINISHED,
    "PF": ItemType.FINISHED,
    "FINISHED": ItemType.FINISHED,
    "APRES VENTE": ItemType.FINISHED,
    "PACKAGING": ItemType.PACKAGING,
    "EMBALLAGE": ItemType.PACKAGING,
    "EMBLG": ItemType.PACKAGING,
}


def _item_type(value: Any) -> ItemType:
    if not value:
        return ItemType.UNKNOWN
    key = normalise_key(str(value))
    if key in ItemType.__members__:
        return ItemType[key]
    return _ITEM_TYPE_ALIASES.get(key, ItemType.UNKNOWN)


def _exclusions(value: Any) -> set[ExclusionScope]:
    """Parse a comma/semicolon separated exclusion list.

    Also accepts the legacy convention where the mere presence of an article in
    the ``A EXCLURE`` tab meant "exclude everywhere": a bare ``X``, ``OUI`` or
    ``TRUE`` maps to :attr:`ExclusionScope.ALL`.
    """
    if value in (None, "", False):
        return set()
    if value is True:
        return {ExclusionScope.ALL}
    out: set[ExclusionScope] = set()
    for token in str(value).replace(";", ",").split(","):
        key = normalise_key(token)
        if not key:
            continue
        if key in ("X", "OUI", "YES", "TRUE", "1", "ALL", "TOUT", "TOUS"):
            out.add(ExclusionScope.ALL)
        elif key in ("GENERIC", "GENERIQUE"):
            out.add(ExclusionScope.GENERIC)
        elif key in ("BOM", "NOMENCLATURE"):
            out.add(ExclusionScope.BOM)
    return out


def map_items(
    campaign_id: str, rows: Iterable[Mapping[str, Any]], *, source: DataSource
) -> tuple[list[Item], list[RowError]]:
    """Build :class:`Item` objects from parsed ``items`` rows.

    One object per article number, the first occurrence winning. The silver
    table computes the programme by cascading up the bill of materials, and that
    climb can fan out: the same article came back twice, with two programmes.
    The upsert downstream would have taken whichever row came last — that is,
    whichever the ERP happened to emit last — so the referential would differ
    between two loads of the same data. The parser still reports the duplicated
    keys; what changes is that the outcome is now decided here, and stable.
    """
    by_number: dict[str, Item] = {}
    errors: list[RowError] = []
    for index, row in enumerate(rows, start=2):
        try:
            item = (
                Item(
                    campaign_id=campaign_id,
                    item_number=row["item_number"],
                    name=row.get("name") or "",
                    search_name=row.get("search_name") or "",
                    item_group=row.get("item_group") or "",
                    lifecycle_state=row.get("lifecycle_state") or "",
                    item_type=_item_type(row.get("item_type")),
                    category=row.get("category") or "",
                    program=row.get("program") or "",
                    commonality=_commonality(row.get("commonality"), row.get("program")),
                    unit=row.get("unit") or "PCE",
                    std_price=row.get("std_price") or 0,
                    exclusions=_exclusions(row.get("exclusions")),
                    source=source,
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "item_number", row.get("item_number"), str(exc)))
            continue
        by_number.setdefault(item.item_number, item)
    return list(by_number.values()), errors


def _commonality(value: Any, program: Any) -> ItemCommonality:
    if value:
        key = normalise_key(str(value))
        if key in ItemCommonality.__members__:
            return ItemCommonality[key]
        if key in ("COMMUN", "COMMON"):
            return ItemCommonality.COMMON
        if key in ("SPECIFIQUE", "SPECIFIC"):
            return ItemCommonality.SPECIFIC
    # No explicit value: an article tied to a programme is specific by
    # construction; one with no programme is shared.
    return ItemCommonality.SPECIFIC if program else ItemCommonality.COMMON


def map_bom_links(
    campaign_id: str, rows: Iterable[Mapping[str, Any]]
) -> tuple[list[BomLink], list[RowError]]:
    """One edge per parent/child pair, the version in force winning.

    The ERP keeps every version of a recipe, so the same pair arrives several
    times: once in force, once or more retired. The campaign stores one edge per
    pair — that is what the explosion walks — and the flag records whether the
    surviving one is live.

    Collapsing here rather than letting the upsert decide is the whole point:
    the upsert keeps whichever row came last, and « last » is the ERP's row
    order. A retired version arriving after the live one silently replaced it,
    which turned an assembly with a perfectly good recipe into one that could
    not be exploded.
    """
    by_pair: dict[tuple[str, str], BomLink] = {}
    errors: list[RowError] = []
    for index, row in enumerate(rows, start=2):
        try:
            link = BomLink(
                campaign_id=campaign_id,
                parent_item=row["parent_item"],
                child_item=row["child_item"],
                qty_per=row["qty_per"],
                unit=row.get("unit") or "PCE",
                active=is_active_status(row.get("statut")),
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "qty_per", row.get("qty_per"), str(exc)))
            continue

        key = (link.parent_item, link.child_item)
        previous = by_pair.get(key)
        # An active version always wins; between two of the same state the last
        # one wins, as before.
        if previous is None or link.active or not previous.active:
            by_pair[key] = link
    return list(by_pair.values()), errors


def map_locations(
    campaign_id: str, rows: Iterable[Mapping[str, Any]]
) -> tuple[list[Location], list[RowError]]:
    locations: list[Location] = []
    errors: list[RowError] = []
    for index, row in enumerate(rows, start=2):
        try:
            locations.append(
                Location(
                    campaign_id=campaign_id,
                    warehouse_id=row["warehouse_id"],
                    location_id=row.get("location_id") or "",
                    zone=row.get("zone") or "",
                    type=LocationType(row.get("type") or "UNKNOWN"),
                    status=LocationStatus(row.get("status") or "ACTIVE"),
                    source=DataSource.FILE_IMPORT,
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "warehouse_id", row.get("warehouse_id"), str(exc)))
    return locations, errors


def map_zones(
    campaign_id: str, rows: Iterable[Mapping[str, Any]], *, id_factory
) -> tuple[list[Zone], list[RowError]]:
    zones: list[Zone] = []
    errors: list[RowError] = []
    for index, row in enumerate(rows, start=2):
        try:
            zones.append(
                Zone(
                    id=id_factory(),
                    campaign_id=campaign_id,
                    code=row["code"],
                    label=row.get("label") or "",
                    sector=row.get("sector") or "",
                    display_order=int(row.get("display_order") or 0),
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "code", row.get("code"), str(exc)))
    return zones, errors


# --------------------------------------------------------------------------- #
# Book stock
# --------------------------------------------------------------------------- #

def map_book_stock(
    campaign_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    items: Mapping[str, Item],
) -> tuple[list[BookStockLine], list[RowError], list[RowError]]:
    """Build the frozen snapshot.

    Duplicate ``(item, warehouse, location)`` triples are **summed**, not
    overwritten: the ERP export legitimately splits one location's stock across
    several rows when batch or status dimensions differ, and dropping all but
    the last would understate the book.

    **Le référentiel articles fait foi**, comme pour les feuilles de comptage, et
    quel que soit le mode d'import. Une ligne dont la référence lui est inconnue
    est une erreur de ligne : le snapshot sert de base à tous les écarts de la
    campagne, et une référence qu'aucun article ne décrit n'a ni désignation, ni
    prix, ni type — son écart serait affiché en quantité nue, non valorisé, et
    hors de toute règle de matérialité.

    Un article **hors périmètre** n'est pas une erreur de ligne : c'est une
    décision de campagne, déjà prise, sur laquelle le fichier ERP n'a pas d'avis.
    Sa ligne est donc **écartée** et signalée, jamais refusée. Son stock ne peut
    pas entrer — l'inventaire ne compte pas cet article, son stock ERP
    produirait un écart égal à la totalité du stock — mais faire d'un choix
    délibéré une ligne rejetée avait une conséquence bien pire : le stock ERP
    remplace l'ensemble existant, donc une seule ligne rejetée annule toute
    l'écriture. Un périmètre restreint rendait le chargement impossible.

    C'est la règle que la lecture du backflush applique déjà, et le contraire de
    l'article inconnu : l'un est un manque de données, l'autre une décision.

    When the export carries no unit cost, the referential's standard price is
    used so that every line is valued.
    """
    aggregated: dict[tuple[str, str, str], BookStockLine] = {}
    errors: list[RowError] = []
    skipped: list[RowError] = []

    for index, row in enumerate(rows, start=2):
        try:
            line = BookStockLine(
                campaign_id=campaign_id,
                item_number=row["item_number"],
                warehouse_id=row["warehouse_id"],
                location_id=row.get("location_id") or "",
                qty=row.get("qty") or 0,
                unit=row.get("unit") or "PCE",
                unit_cost=row.get("unit_cost") or 0,
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "qty", row.get("qty"), str(exc)))
            continue

        item = items.get(line.item_number)
        if item is None:
            errors.append(
                RowError(index, "item_number", row.get("item_number"),
                         f"L'article {line.item_number} est absent du référentiel "
                         "de la campagne. Complétez le référentiel articles : un "
                         "import de stock ne crée jamais d'article.")
            )
            continue
        if item.excluded_everywhere:
            skipped.append(
                RowError(index, "item_number", row.get("item_number"),
                         f"L'article {line.item_number} est hors du périmètre de "
                         "la campagne : sa ligne de stock n'est pas chargée. "
                         "Levez l'exclusion sur la grille Articles pour "
                         "l'inventorier.")
            )
            continue

        if line.unit_cost == 0:
            line.unit_cost = item.std_price

        key = (line.item_number, line.warehouse_id, line.location_id)
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = line
        else:
            existing.qty += line.qty
            if existing.unit_cost == 0:
                existing.unit_cost = line.unit_cost

    return list(aggregated.values()), errors, skipped


# --------------------------------------------------------------------------- #
# Counting journals
# --------------------------------------------------------------------------- #

class ImportedJournalLine:
    """A counting-journal line before it is attached to a persisted journal.

    The ERP export is keyed by (warehouse, location); the application's journals
    are keyed the same way, so the service layer resolves the pair to a journal
    id and then materialises :class:`CountJournalLine` objects.
    """

    __slots__ = (
        "counting_date",
        "description",
        "is_posted",
        "item_number",
        "journal_number",
        "kind",
        "location_id",
        "posted_at",
        "qty",
        "unit",
        "warehouse_id",
    )

    def __init__(
        self,
        *,
        item_number: str,
        warehouse_id: str,
        location_id: str,
        qty: Decimal,
        unit: str = "PCE",
        journal_number: str = "",
        kind: JournalKind = JournalKind.INVV,
        is_posted: bool = False,
        posted_at: dt.datetime | None = None,
        description: str = "",
        counting_date: dt.datetime | None = None,
    ) -> None:
        self.item_number = item_number
        self.warehouse_id = warehouse_id
        self.location_id = location_id
        self.qty = qty
        self.unit = unit
        self.journal_number = journal_number
        self.kind = kind
        self.is_posted = is_posted
        self.posted_at = posted_at
        self.description = description
        self.counting_date = counting_date

    @property
    def key(self) -> tuple[str, str]:
        return (self.warehouse_id, self.location_id)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ImportedJournalLine({self.item_number} @ {self.warehouse_id}/"
            f"{self.location_id} = {self.qty})"
        )


def map_journal_lines(
    rows: Sequence[Mapping[str, Any]]
) -> tuple[list[ImportedJournalLine], list[RowError], list[RowError]]:
    """Build journal lines from the ERP OData export.

    Returns ``(lines, errors, warnings)``.

    **Location recovery.** A real production export can carry a row whose
    ``WarehouseLocationId`` is null while every other row of the same journal
    names the same location — the extraction lost one cell. Dropping the row
    would silently lose the counted quantity (the legacy behaviour), and
    guessing without saying so would be just as bad. So the location is
    recovered from the journal's other rows *only when they are unanimous*, and
    the correction is reported as a warning that surfaces in the import report.
    """
    rows = list(rows)
    lines: list[ImportedJournalLine] = []
    errors: list[RowError] = []
    warnings: list[RowError] = []

    # Pass 1 — what location does each journal number use?
    locations_per_journal: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        journal = str(row.get("journal_number") or "").strip().upper()
        warehouse = normalise_key(str(row.get("warehouse_id") or ""))
        location = normalise_key(str(row.get("location_id") or ""))
        if journal and location:
            locations_per_journal.setdefault(journal, set()).add((warehouse, location))

    # Pass 2 — map, recovering unambiguous missing locations.
    for index, row in enumerate(rows, start=2):
        item_number = normalise_key(str(row.get("item_number") or ""))
        warehouse_id = normalise_key(str(row.get("warehouse_id") or ""))
        location_id = normalise_key(str(row.get("location_id") or ""))
        journal_number = str(row.get("journal_number") or "").strip()

        if not item_number:
            errors.append(RowError(index, "item_number", row.get("item_number"),
                                   "Numéro d'article manquant."))
            continue
        if not warehouse_id:
            errors.append(RowError(index, "warehouse_id", row.get("warehouse_id"),
                                   "Entrepôt manquant."))
            continue

        qty = row.get("counted_quantity")
        if qty is None:
            errors.append(RowError(index, "counted_quantity", qty,
                                   "Quantité comptée manquante."))
            continue

        if not location_id and journal_number:
            candidates = {
                loc for wh, loc in locations_per_journal.get(journal_number.upper(), ())
                if wh == warehouse_id
            }
            if len(candidates) == 1:
                location_id = candidates.pop()
                warnings.append(
                    RowError(
                        index, "location_id", None,
                        f"Emplacement absent de l'export : déduit du journal "
                        f"{journal_number} → « {location_id} ». "
                        "Vérifiez la ligne avant de poster.",
                    )
                )
            elif candidates:
                errors.append(
                    RowError(
                        index, "location_id", None,
                        f"Emplacement absent et ambigu : le journal {journal_number} "
                        f"porte sur {len(candidates)} emplacements différents.",
                    )
                )
                continue

        kind_raw = normalise_key(str(row.get("journal_name_id") or "INVV"))
        kind = JournalKind.INVE if kind_raw == "INVE" else JournalKind.INVV

        lines.append(
            ImportedJournalLine(
                item_number=item_number,
                warehouse_id=warehouse_id,
                location_id=location_id,
                qty=qty,
                unit=normalise_key(str(row.get("unit") or "PCE")) or "PCE",
                journal_number=journal_number,
                kind=kind,
                is_posted=bool(row.get("is_posted")),
                posted_at=row.get("posted_date_time"),
                description=str(row.get("description") or "").strip(),
                counting_date=row.get("counting_date"),
            )
        )
    return lines, errors, warnings


# --------------------------------------------------------------------------- #
# Counting sheets
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class PreparedSheetRow:
    """One ``[feuille, article, section]`` triple of a prepared sheet.

    Deliberately *not* a :class:`CountSheetLine`: at mapping time the zone may
    not exist yet, and the same triple will be materialised once per counting
    pass of that zone. The service layer resolves the sheet ids and multiplies.
    """

    sheet_code: str
    item_number: str
    section: CountSection
    unit: str = "PCE"

    @property
    def key(self) -> tuple[str, CountSection]:
        return (self.item_number, self.section)


def map_count_sheets(
    rows: Iterable[Mapping[str, Any]],
    *,
    items: Mapping[str, Item],
) -> tuple[list[PreparedSheetRow], list[RowError]]:
    """Build the prepared content of the GENERIQUE sheets.

    Two rules make this import safe to run against a campaign under preparation:

    * an unrecognised section is an **error**, never a default — silently
      treating an unknown status as "line side" would skip a BOM explosion and
      quietly lose the components of a whole assembly;
    * an article absent from the campaign's article referential is a **row
      error**, not an article created on the fly. The referential is the truth
      of the campaign, and loading sheets must not be able to extend it as a
      side effect.
    """
    out: list[PreparedSheetRow] = []
    errors: list[RowError] = []
    for offset, row in enumerate(rows):
        index = offset + 2

        sheet_code = normalise_key(str(row.get("sheet_code") or ""))
        if not sheet_code:
            errors.append(
                RowError(index, "sheet_code", row.get("sheet_code"),
                         "La feuille (zone) est obligatoire.")
            )
            continue

        item_number = normalise_key(str(row.get("item_number") or ""))
        if not item_number:
            errors.append(
                RowError(index, "item_number", row.get("item_number"),
                         "Le numéro d'article est obligatoire.")
            )
            continue
        if item_number not in items:
            errors.append(
                RowError(index, "item_number", row.get("item_number"),
                         f"L'article {item_number} est absent du référentiel de la "
                         "campagne. Complétez le référentiel articles : un import "
                         "de feuilles ne crée jamais d'article.")
            )
            continue

        raw_section = row.get("section")
        section = _resolve_section(raw_section)
        if section is None:
            errors.append(
                RowError(index, "section", raw_section,
                         "Section inconnue. Valeurs acceptées : LINE_SIDE, WIP, "
                         "WIP_OK (ou les anciens libellés BDL / MOM waiting / MOM OK).")
            )
            continue

        out.append(
            PreparedSheetRow(
                sheet_code=sheet_code,
                item_number=item_number,
                section=section,
                unit=normalise_key(str(row.get("unit") or "PCE")) or "PCE",
            )
        )
    return out, errors


def _resolve_section(value: Any) -> CountSection | None:
    if value in (None, ""):
        return CountSection.LINE_SIDE  # the default section of a printed sheet
    text = str(value).strip()
    upper = text.upper().replace(" ", "_").replace("-", "_")
    if upper in CountSection.__members__:
        return CountSection[upper]
    return legacy_section_alias(text)


# --------------------------------------------------------------------------- #
# Adjustments
# --------------------------------------------------------------------------- #

#: Movement labels used by the ERP transaction export.
_KIND_ALIASES: dict[str, AdjustmentKind] = {
    "COMPTAGE": AdjustmentKind.COUNT,
    "COUNT": AdjustmentKind.COUNT,
    "COUNTING": AdjustmentKind.COUNT,
    "AJUSTEMENT DE STOCK": AdjustmentKind.ADJUSTMENT,
    "AJUSTEMENT": AdjustmentKind.ADJUSTMENT,
    "ADJUSTMENT": AdjustmentKind.ADJUSTMENT,
    "RECOMPTAGE": AdjustmentKind.RECOUNT,
    "RECOUNT": AdjustmentKind.RECOUNT,
}


def map_adjustments(
    campaign_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    source: DataSource,
    id_factory,
) -> tuple[list[AdjustmentLine], list[RowError]]:
    out: list[AdjustmentLine] = []
    errors: list[RowError] = []
    for index, row in enumerate(rows, start=2):
        try:
            out.append(
                AdjustmentLine(
                    id=id_factory(),
                    campaign_id=campaign_id,
                    item_number=row["item_number"],
                    warehouse_id=row.get("warehouse_id") or "",
                    location_id=row.get("location_id") or "",
                    kind=_adjustment_kind(row.get("kind")),
                    qty=row.get("qty") or 0,
                    unit=row.get("unit") or "PCE",
                    value=row.get("value") or 0,
                    journal_number=str(row.get("journal_number") or ""),
                    physical_date=row.get("physical_date"),
                    reason_code=str(row.get("reason_code") or ""),
                    comment=str(row.get("comment") or ""),
                    source=source,
                )
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "item_number", row.get("item_number"), str(exc)))
    return out, errors


def _adjustment_kind(value: Any) -> AdjustmentKind:
    if not value:
        return AdjustmentKind.ADJUSTMENT
    key = normalise_key(str(value))
    if key in AdjustmentKind.__members__:
        return AdjustmentKind[key]
    return _KIND_ALIASES.get(key, AdjustmentKind.OTHER)


def map_backflush(
    campaign_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    period_start: dt.date,
    period_end: dt.date,
    items: Mapping[str, Item] | None = None,
) -> tuple[list[BackflushLine], list[RowError]]:
    """Build the frozen backflush read.

    Duplicate article numbers are **summed**, like the book stock and for the
    same reason: an export split by parent, or a workbook with one tab per
    production line, legitimately carries the same component twice, and keeping
    only the last row would understate the variance rather than report it.

    Rows whose net variance, both components and both consumptions are all zero
    are dropped. Absence of data means a nil variance — the guide is explicit —
    so storing an explicit zero adds a row to every screen and changes no
    figure. The exception is a row that carries a *count* of parents or weeks:
    that one says « measured, and it came out at zero », which is worth keeping.

    ``items`` is the campaign's perimeter when it is supplied: in the referential
    and not excluded from it. The fact table covers the whole plant, and an
    article the campaign does not inventory has no variance to attribute to it.
    """
    aggregated: dict[str, BackflushLine] = {}
    errors: list[RowError] = []

    for index, row in enumerate(rows, start=2):
        try:
            line = BackflushLine(
                campaign_id=campaign_id,
                item_number=row["item_number"],
                period_start=period_start,
                period_end=period_end,
                unit=row.get("unit") or "PCE",
                net_qty=row.get("net_qty") or 0,
                under_consumed_qty=row.get("under_consumed_qty") or 0,
                over_consumed_qty=row.get("over_consumed_qty") or 0,
                theoretical_qty=row.get("theoretical_qty") or 0,
                actual_qty=row.get("actual_qty") or 0,
                parent_count=row.get("parent_count") or 0,
                week_count=row.get("week_count") or 0,
                source_loaded_at=_as_datetime(row.get("source_loaded_at")),
            )
        except (ValueError, KeyError) as exc:
            errors.append(
                RowError(index, "net_qty", row.get("net_qty"), str(exc))
            )
            continue

        if items is not None and line.item_number not in items:
            continue
        if not _carries_information(line):
            continue

        existing = aggregated.get(line.item_number)
        if existing is None:
            aggregated[line.item_number] = line
            continue
        existing.net_qty += line.net_qty
        existing.under_consumed_qty += line.under_consumed_qty
        existing.over_consumed_qty += line.over_consumed_qty
        existing.theoretical_qty += line.theoretical_qty
        existing.actual_qty += line.actual_qty
        existing.parent_count = max(existing.parent_count, line.parent_count)
        existing.week_count = max(existing.week_count, line.week_count)

    return list(aggregated.values()), errors


def _carries_information(line: BackflushLine) -> bool:
    """Whether the row says anything a missing row would not say."""
    return bool(
        line.net_qty
        or line.under_consumed_qty
        or line.over_consumed_qty
        or line.theoretical_qty
        or line.actual_qty
        or line.parent_count
        or line.week_count
    )


def map_stock_flow_inputs(
    run_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    kind: FlowKind,
    items: Mapping[str, Item] | None = None,
) -> tuple[list[StockFlowInput], list[RowError]]:
    """Build one step's loaded quantities.

    Summed on duplicates, again: a year of receipts exported month by month
    lists the same reference twelve times, and that is the normal shape of the
    file rather than a mistake to report.
    """
    aggregated: dict[str, StockFlowInput] = {}
    errors: list[RowError] = []

    for index, row in enumerate(rows, start=2):
        try:
            line = StockFlowInput(
                run_id=run_id,
                item_number=row["item_number"],
                kind=kind,
                qty=row.get("qty") or 0,
                unit=row.get("unit") or "PCE",
                source=FlowSource.FILE,
            )
        except (ValueError, KeyError) as exc:
            errors.append(RowError(index, "qty", row.get("qty"), str(exc)))
            continue

        if items is not None and line.item_number not in items:
            continue

        existing = aggregated.get(line.item_number)
        if existing is None:
            aggregated[line.item_number] = line
        else:
            existing.qty += line.qty

    return list(aggregated.values()), errors


def _as_datetime(value: Any) -> dt.datetime | None:
    """A timestamp from a cell, whatever the source spelled it as."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.UTC)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
