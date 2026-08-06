"""Control engine — the guard-rails the Excel process never had.

Each control is a small, independently testable function returning
:class:`~inventory.domain.models.ControlFinding` objects. They are cheap enough
to run on every write, which is what makes the "exception first" principle of
the specification real: the user is shown what is wrong *while* they work, not
in a post-mortem three weeks later.

Severity contract
-----------------
``BLOCKER``  prevents a phase transition (and, for some, prevents posting).
``WARNING``  must be looked at but does not stop the campaign.
``INFO``     contextual, useful in the audit trail.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence

from .bom import BomIndex
from .enums import (
    ControlSeverity,
    ItemType,
    JournalStatus,
    LocationStatus,
    SheetPass,
)
from .models import (
    BomLink,
    BookStockLine,
    Campaign,
    ControlFinding,
    CountJournal,
    CountJournalLine,
    CountSheet,
    CountSheetLine,
    Item,
    Location,
    LocationKey,
    VarianceLine,
    Zone,
)
from .quantities import ZERO
from .variance import is_material

__all__ = [
    "check_referentials",
    "check_book_stock",
    "check_journals",
    "check_variances",
    "check_zones",
    "run_all_controls",
    "summarise",
]


# --------------------------------------------------------------------------- #
# Referentials
# --------------------------------------------------------------------------- #

def check_referentials(
    *,
    items: Mapping[str, Item],
    bom_links: Sequence[BomLink],
    bom_index: BomIndex | None = None,
) -> list[ControlFinding]:
    """Structural integrity of the article and BOM referentials."""
    findings: list[ControlFinding] = []

    # -- BOM edges pointing at unknown articles -------------------------------
    missing_parents: set[str] = set()
    missing_children: set[str] = set()
    for link in bom_links:
        if link.parent_item not in items:
            missing_parents.add(link.parent_item)
        if link.child_item not in items:
            missing_children.add(link.child_item)

    for parent in sorted(missing_parents):
        findings.append(
            ControlFinding(
                code="BOM_PARENT_UNKNOWN",
                severity=ControlSeverity.WARNING,
                message=(
                    f"La nomenclature référence l'assemblage {parent}, absent du "
                    "référentiel articles."
                ),
                entity_type="bom",
                item_number=parent,
            )
        )
    for child in sorted(missing_children):
        findings.append(
            ControlFinding(
                code="BOM_CHILD_UNKNOWN",
                severity=ControlSeverity.WARNING,
                message=(
                    f"La nomenclature référence le composant {child}, absent du "
                    "référentiel articles."
                ),
                entity_type="bom",
                item_number=child,
            )
        )

    # -- cycles ----------------------------------------------------------------
    index = bom_index or BomIndex(bom_links)
    for cycle in index.find_cycles():
        findings.append(
            ControlFinding(
                code="BOM_CYCLE",
                severity=ControlSeverity.BLOCKER,
                message="Cycle de nomenclature : " + " → ".join(cycle),
                entity_type="bom",
                context={"cycle": cycle},
            )
        )

    # -- assemblies without a structure ---------------------------------------
    for item in items.values():
        if item.is_assembly and not item.excluded_everywhere and not index.has_bom(
            item.item_number
        ):
            findings.append(
                ControlFinding(
                    code="ASSEMBLY_WITHOUT_BOM",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{item.item_number} est déclaré "
                        f"{'produit fini' if item.item_type is ItemType.FINISHED else 'semi-fini'} "
                        "mais n'a aucune nomenclature : il ne pourra pas être éclaté "
                        "s'il est compté en WIP."
                    ),
                    entity_type="item",
                    item_number=item.item_number,
                )
            )

    # -- valuation gaps --------------------------------------------------------
    unpriced = [
        i.item_number
        for i in items.values()
        if i.std_price == 0 and not i.excluded_everywhere
    ]
    if unpriced:
        findings.append(
            ControlFinding(
                code="ITEMS_WITHOUT_PRICE",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{len(unpriced)} article(s) ont un prix standard nul : leurs écarts "
                    "seront valorisés à 0 € et disparaîtront des analyses en valeur."
                ),
                entity_type="item",
                context={"sample": sorted(unpriced)[:20], "count": len(unpriced)},
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Book stock snapshot
# --------------------------------------------------------------------------- #

def check_book_stock(
    *,
    book_stock: Sequence[BookStockLine],
    items: Mapping[str, Item],
    locations: Mapping[LocationKey, Location] | None = None,
) -> list[ControlFinding]:
    """Coherence of the frozen ERP snapshot."""
    findings: list[ControlFinding] = []

    if not book_stock:
        return [
            ControlFinding(
                code="BOOK_STOCK_EMPTY",
                severity=ControlSeverity.BLOCKER,
                message="Le stock livre est vide : aucun écart ne pourra être calculé.",
                entity_type="book_stock",
            )
        ]

    unknown_items = sorted({
        line.item_number for line in book_stock if line.item_number not in items
    })
    if unknown_items:
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_UNKNOWN_ITEM",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{len(unknown_items)} article(s) du stock livre sont absents du "
                    "référentiel de la campagne."
                ),
                entity_type="book_stock",
                context={"sample": unknown_items[:20], "count": len(unknown_items)},
            )
        )

    # Duplicate (item, warehouse, location) triples would double-count the book.
    duplicates = [
        key
        for key, n in Counter(
            (l.item_number, l.warehouse_id, l.location_id) for l in book_stock
        ).items()
        if n > 1
    ]
    if duplicates:
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_DUPLICATE_KEY",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{len(duplicates)} triplet(s) article/entrepôt/emplacement "
                    "apparaissent plusieurs fois dans le stock livre ; les quantités "
                    "ont été sommées."
                ),
                entity_type="book_stock",
                context={"sample": [list(d) for d in duplicates[:20]]},
            )
        )

    # Unit drift between the snapshot and the referential silently changes the
    # meaning of every quantity compared afterwards.
    for line in book_stock:
        item = items.get(line.item_number)
        if item and line.unit and item.unit and line.unit != item.unit:
            findings.append(
                ControlFinding(
                    code="UNIT_MISMATCH",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{line.item_number} : unité {line.unit} dans le stock livre "
                        f"contre {item.unit} dans le référentiel."
                    ),
                    entity_type="book_stock",
                    item_number=line.item_number,
                    warehouse_id=line.warehouse_id,
                    location_id=line.location_id,
                    context={"snapshot": line.unit, "referential": item.unit},
                )
            )

    negatives = [l for l in book_stock if l.qty < 0]
    if negatives:
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_NEGATIVE",
                severity=ControlSeverity.INFO,
                message=(
                    f"{len(negatives)} ligne(s) de stock livre sont négatives — "
                    "généralement une consommation antérieure à la réception."
                ),
                entity_type="book_stock",
                context={"count": len(negatives)},
            )
        )

    if locations:
        orphans = sorted({
            f"{l.warehouse_id} / {l.location_id}"
            for l in book_stock
            if LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id)
            not in locations
        })
        if orphans:
            findings.append(
                ControlFinding(
                    code="BOOK_STOCK_UNKNOWN_LOCATION",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{len(orphans)} emplacement(s) du stock livre ne figurent pas "
                        "dans le référentiel emplacements."
                    ),
                    entity_type="location",
                    context={"sample": orphans[:20]},
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# Counting journals
# --------------------------------------------------------------------------- #

def check_journals(
    *,
    journals: Sequence[CountJournal],
    lines_by_journal: Mapping[str, Sequence[CountJournalLine]],
    items: Mapping[str, Item],
    locations: Mapping[LocationKey, Location] | None = None,
) -> list[ControlFinding]:
    """Coherence of the counting journals and their lines."""
    findings: list[ControlFinding] = []

    seen_keys: Counter[tuple[str, str]] = Counter()
    for journal in journals:
        seen_keys[(journal.warehouse_id, journal.location_id)] += 1
        lines = lines_by_journal.get(journal.id, ())

        # A journal on a disabled location must not exist at all.
        if locations is not None:
            loc = locations.get(journal.key)
            if loc is not None and loc.status is LocationStatus.DISABLED:
                findings.append(
                    ControlFinding(
                        code="JOURNAL_ON_DISABLED_LOCATION",
                        severity=ControlSeverity.BLOCKER,
                        message=(
                            f"Le journal {journal.key} porte sur un emplacement "
                            "désactivé : il doit être supprimé ou l'emplacement "
                            "réactivé."
                        ),
                        entity_type="count_journal",
                        entity_id=journal.id,
                        warehouse_id=journal.warehouse_id,
                        location_id=journal.location_id,
                    )
                )

        if journal.status is JournalStatus.POSTED and not lines:
            findings.append(
                ControlFinding(
                    code="POSTED_JOURNAL_EMPTY",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"Le journal {journal.key} est posté mais ne contient aucune "
                        "ligne : l'emplacement sera compté à zéro."
                    ),
                    entity_type="count_journal",
                    entity_id=journal.id,
                    warehouse_id=journal.warehouse_id,
                    location_id=journal.location_id,
                )
            )

        item_counts: Counter[str] = Counter()
        for line in lines:
            item_counts[line.item_number] += 1
            item = items.get(line.item_number)
            if item is None:
                findings.append(
                    ControlFinding(
                        code="JOURNAL_UNKNOWN_ITEM",
                        severity=ControlSeverity.WARNING,
                        message=(
                            f"{line.item_number} est compté dans {journal.key} mais "
                            "absent du référentiel articles."
                        ),
                        entity_type="count_journal_line",
                        entity_id=line.id,
                        item_number=line.item_number,
                        warehouse_id=journal.warehouse_id,
                        location_id=journal.location_id,
                    )
                )
            else:
                if item.excluded_everywhere:
                    findings.append(
                        ControlFinding(
                            code="EXCLUDED_ITEM_COUNTED",
                            severity=ControlSeverity.WARNING,
                            message=(
                                f"{line.item_number} est exclu du périmètre mais "
                                f"apparaît dans le journal {journal.key}."
                            ),
                            entity_type="count_journal_line",
                            entity_id=line.id,
                            item_number=line.item_number,
                        )
                    )
                if line.unit and item.unit and line.unit != item.unit:
                    findings.append(
                        ControlFinding(
                            code="UNIT_MISMATCH",
                            severity=ControlSeverity.WARNING,
                            message=(
                                f"{line.item_number} : unité comptée {line.unit} "
                                f"contre {item.unit} au référentiel."
                            ),
                            entity_type="count_journal_line",
                            entity_id=line.id,
                            item_number=line.item_number,
                            context={"counted": line.unit, "referential": item.unit},
                        )
                    )
            if line.qty < 0:
                findings.append(
                    ControlFinding(
                        code="NEGATIVE_COUNT",
                        severity=ControlSeverity.BLOCKER,
                        message=(
                            f"{line.item_number} est compté négativement "
                            f"({line.qty}) dans {journal.key} : un comptage physique "
                            "ne peut pas être négatif."
                        ),
                        entity_type="count_journal_line",
                        entity_id=line.id,
                        item_number=line.item_number,
                    )
                )

        for item_number, n in item_counts.items():
            if n > 1:
                findings.append(
                    ControlFinding(
                        code="DUPLICATE_COUNT_LINE",
                        severity=ControlSeverity.WARNING,
                        message=(
                            f"{item_number} apparaît {n} fois dans le journal "
                            f"{journal.key} ; les quantités seront sommées."
                        ),
                        entity_type="count_journal",
                        entity_id=journal.id,
                        item_number=item_number,
                        context={"occurrences": n},
                    )
                )

    for (wh, loc), n in seen_keys.items():
        if n > 1:
            findings.append(
                ControlFinding(
                    code="DUPLICATE_JOURNAL",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{n} journaux existent pour {wh} / {loc} ; il doit y en avoir "
                        "exactement un par emplacement actif."
                    ),
                    entity_type="count_journal",
                    warehouse_id=wh,
                    location_id=loc,
                    context={"occurrences": n},
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# Variances
# --------------------------------------------------------------------------- #

def check_variances(
    *, campaign: Campaign, variances: Sequence[VarianceLine]
) -> list[ControlFinding]:
    """Findings derived from the reconciled variances."""
    findings: list[ControlFinding] = []

    never_counted = [v for v in variances if v.book_only and v.book_qty != 0]
    if never_counted:
        value = sum((abs(v.book_value) for v in never_counted), ZERO)
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_NOT_COUNTED",
                severity=ControlSeverity.BLOCKER,
                message=(
                    f"{len(never_counted)} couple(s) article/emplacement portent du "
                    f"stock livre ({value:,.0f} €) sans aucun comptage. Ils seront "
                    "soldés à zéro si l'inventaire est clôturé en l'état."
                ),
                entity_type="variance",
                context={"count": len(never_counted), "bookValue": str(value)},
            )
        )

    ghosts = [v for v in variances if v.counted_only and v.counted_qty != 0]
    if ghosts:
        findings.append(
            ControlFinding(
                code="COUNTED_WITHOUT_BOOK_STOCK",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{len(ghosts)} couple(s) article/emplacement ont été comptés alors "
                    "que l'ERP n'y voyait aucun stock."
                ),
                entity_type="variance",
                context={"count": len(ghosts)},
            )
        )

    for line in variances:
        thresholds = campaign.threshold_for(line.item_type)
        if not is_material(line, thresholds):
            continue
        findings.append(
            ControlFinding(
                code="MATERIAL_VARIANCE",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{line.item_number} : écart de {line.variance_qty} {line.unit} "
                    f"({line.variance_value:,.0f} €) au-delà des seuils "
                    f"{line.item_type}."
                ),
                entity_type="variance",
                item_number=line.item_number,
                warehouse_id=line.warehouse_id,
                location_id=line.location_id,
                context={
                    "varianceQty": str(line.variance_qty),
                    "varianceValue": str(line.variance_value),
                    "bookQty": str(line.book_qty),
                },
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# GENERIQUE zones
# --------------------------------------------------------------------------- #

def check_zones(
    *,
    zones: Sequence[Zone],
    sheets: Sequence[CountSheet],
    lines_by_sheet: Mapping[str, Sequence[CountSheetLine]] | None = None,
) -> list[ControlFinding]:
    """Preparation controls on the GENERIQUE zones.

    Two defects, both of which are cheap to fix in preparation and expensive to
    discover on the morning of the count:

    * a zone whose sheets carry no pre-printed article list — **unless** it is
      declared as a free-entry sheet. That distinction is the whole reason
      :attr:`Zone.free_entry` exists: a deliberately blank sheet and a sheet
      somebody forgot to prepare look exactly alike otherwise, and flagging both
      teaches people to ignore the warning;
    * a zone missing one of the sheets its own ``passes`` requires — a second
      counter with no sheet is a second count that will not happen.
    """
    lines_by_sheet = lines_by_sheet or {}
    findings: list[ControlFinding] = []
    sheets_by_zone: dict[str, list[CountSheet]] = defaultdict(list)
    for sheet in sheets:
        sheets_by_zone[sheet.zone_id].append(sheet)

    for zone in zones:
        zone_sheets = sheets_by_zone.get(zone.id, [])
        expected = {SheetPass.PASS_1, SheetPass.PASS_2} if zone.passes >= 2 else {
            SheetPass.PASS_1
        }
        missing = expected - {s.pass_no for s in zone_sheets}
        if missing:
            findings.append(
                ControlFinding(
                    code="ZONE_MISSING_SHEET",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"Zone {zone.code} : la feuille de comptage "
                        f"{', '.join(sorted(str(p) for p in missing))} manque alors "
                        f"que la zone demande {zone.passes} comptage(s)."
                    ),
                    entity_type="zone",
                    entity_id=zone.id,
                    context={"passes": zone.passes},
                )
            )

        if zone.free_entry:
            continue
        if any(lines_by_sheet.get(s.id) for s in zone_sheets):
            continue
        findings.append(
            ControlFinding(
                code="ZONE_WITHOUT_LINES",
                severity=ControlSeverity.WARNING,
                message=(
                    f"Zone {zone.code} : aucune ligne pré-imprimée. Chargez sa "
                    "liste d'articles, ou déclarez-la en saisie libre si le "
                    "compteur doit écrire ce qu'il trouve."
                ),
                entity_type="zone",
                entity_id=zone.id,
                context={"remedy": "import_count_sheets_or_free_entry"},
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_all_controls(
    *,
    campaign: Campaign,
    items: Mapping[str, Item] | None = None,
    bom_links: Sequence[BomLink] = (),
    bom_index: BomIndex | None = None,
    book_stock: Sequence[BookStockLine] = (),
    locations: Mapping[LocationKey, Location] | None = None,
    journals: Sequence[CountJournal] = (),
    lines_by_journal: Mapping[str, Sequence[CountJournalLine]] | None = None,
    zones: Sequence[Zone] = (),
    sheets: Sequence[CountSheet] = (),
    lines_by_sheet: Mapping[str, Sequence[CountSheetLine]] | None = None,
    variances: Sequence[VarianceLine] = (),
) -> list[ControlFinding]:
    """Run every control applicable to the data that was supplied.

    Each block is optional: passing only what a screen has loaded keeps the call
    cheap, and the caller decides how much context to pay for.
    """
    items = items or {}
    findings: list[ControlFinding] = []
    if items or bom_links:
        findings += check_referentials(
            items=items, bom_links=bom_links, bom_index=bom_index
        )
    if book_stock:
        findings += check_book_stock(
            book_stock=book_stock, items=items, locations=locations
        )
    if journals:
        findings += check_journals(
            journals=journals,
            lines_by_journal=lines_by_journal or {},
            items=items,
            locations=locations,
        )
    if zones:
        findings += check_zones(
            zones=zones, sheets=sheets, lines_by_sheet=lines_by_sheet
        )
    if variances:
        findings += check_variances(campaign=campaign, variances=variances)
    return findings


def summarise(findings: Iterable[ControlFinding]) -> dict[str, object]:
    """Counts per severity and per code, for the exception banner."""
    by_severity: Counter[str] = Counter()
    by_code: defaultdict[str, int] = defaultdict(int)
    for f in findings:
        by_severity[str(f.severity)] += 1
        by_code[f.code] += 1
    return {
        "total": sum(by_severity.values()),
        "bySeverity": dict(by_severity),
        "byCode": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "hasBlocker": by_severity.get(str(ControlSeverity.BLOCKER), 0) > 0,
    }
