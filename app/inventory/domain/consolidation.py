"""Consolidation of the GENERIQUE location — replacement for ``Compil GENERIQUE.xlsx``.

GENERIQUE is a single ERP location that physically covers dozens of areas: line
sides, picking zones, quality/metrology/expertise rooms, laboratories. Each area
is counted twice on paper by two independent teams, arbitrated when the two
counts disagree, then the whole thing is aggregated into **one** INVV counting
journal for ``B06VRAC / GENERIQUE``.

The legacy chain was: 40 Excel tabs → a ``DATA`` tab → five Power Query steps
(``BDL``, ``MOMOK``, ``ECLATEE``, ``JOURNAL``) → a copy/paste into the ERP.

What this module changes
------------------------
=================================  ==========================================
Legacy behaviour                    Behaviour here
=================================  ==========================================
Blank cell == 0                     Blank means *not counted* and is reported
Inner join drops unknown parents    Unknown parents are reported as exceptions
Section read from a text cell       Section is typed data, aliased at import
Exclusions applied only at the end  Applied per scope (ALL / GENERIC / BOM)
No trace of the explosion           Full ``parent → child`` breakdown kept
Silent unit mismatch                Unit coherence is a control finding
=================================  ==========================================

The output is deterministic: same inputs → byte-identical journal, which is what
makes a campaign reproducible months later from its frozen dossier.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from .bom import BomCycleError, BomIndex, ExplosionResult
from .enums import ControlSeverity, CountSection, SheetPass, SheetStatus
from .models import (
    ArbitrationLine,
    ConsolidatedLine,
    ControlFinding,
    CountSheet,
    CountSheetLine,
    Item,
    WipBreakdown,
    Zone,
)
from .quantities import ZERO, quantize_qty

__all__ = [
    "ZoneCounts",
    "ConsolidationInput",
    "ConsolidationResult",
    "build_arbitration_lines",
    "resolve_zone_quantities",
    "consolidate_generic",
]


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class ZoneCounts:
    """Everything needed to resolve one zone's retained quantities."""

    zone: Zone
    sheets: Sequence[CountSheet]
    #: All lines of all sheets of this zone, keyed by ``sheet.id``.
    lines_by_sheet: Mapping[str, Sequence[CountSheetLine]]
    #: Arbitration decisions recorded for this zone.
    arbitrations: Sequence[ArbitrationLine] = ()

    @property
    def passes_required(self) -> int:
        """How many independent counts *this* zone expects.

        Carried by the zone, not by the campaign: a single-pass zone cannot
        produce an arbitration discrepancy, so it must not be told that one of
        its two counts is missing.
        """
        return self.zone.passes


@dataclass(slots=True)
class ConsolidationInput:
    """Full input of a consolidation run."""

    campaign_id: str
    zones: Sequence[ZoneCounts]
    items: Mapping[str, Item]
    bom: BomIndex
    #: Relative gap under which pass-2 is accepted without arbitration.
    arbitration_tolerance: Decimal = ZERO
    #: When False, zones that are not fully counted are skipped instead of
    #: contributing partial data. Used for the live "preview" during counting.
    require_done_zones: bool = True


@dataclass(slots=True)
class ConsolidationResult:
    """Journal-ready output plus everything needed to explain it."""

    campaign_id: str
    lines: list[ConsolidatedLine] = field(default_factory=list)
    breakdown: list[WipBreakdown] = field(default_factory=list)
    findings: list[ControlFinding] = field(default_factory=list)
    #: Zones that contributed to this run.
    zones_included: list[str] = field(default_factory=list)
    #: Zones skipped because they are not finished.
    zones_skipped: list[str] = field(default_factory=list)

    @property
    def total_qty(self) -> Decimal:
        return quantize_qty(sum((line.qty for line in self.lines), ZERO))

    @property
    def blocking(self) -> list[ControlFinding]:
        return [f for f in self.findings if f.severity is ControlSeverity.BLOCKER]

    def as_journal_rows(self) -> list[dict[str, object]]:
        """Rows shaped for the ERP INVV counting journal."""
        return [
            {"ItemNumber": line.item_number, "CountedQuantity": line.qty,
             "Unit": line.unit}
            for line in self.lines
        ]


# --------------------------------------------------------------------------- #
# Step 1 — reconcile the two passes
# --------------------------------------------------------------------------- #

def _index_lines(
    lines: Iterable[CountSheetLine],
) -> dict[tuple[str, CountSection], Decimal]:
    """Sum a sheet's lines per (item, section).

    A sheet may legitimately list the same article twice (two pallets, two
    sub-areas), so quantities are summed rather than de-duplicated. Lines with
    no value at all are skipped: a blank cell is *not* a counted zero.
    """
    out: dict[tuple[str, CountSection], Decimal] = defaultdict(Decimal)
    for line in lines:
        if not line.is_counted:
            continue
        out[(line.item_number, line.section)] += line.qty
    return {k: quantize_qty(v) for k, v in out.items()}


def build_arbitration_lines(
    zone: ZoneCounts,
    *,
    campaign_id: str,
    id_factory,
) -> list[ArbitrationLine]:
    """Materialise the pass-1 / pass-2 comparison for a zone.

    Produces one line per (item, section) present in **either** pass, so that an
    article counted by one team and missed by the other shows up — the legacy
    process compared only what both sheets happened to contain.

    :param id_factory: callable returning a fresh identifier per line.
    """
    by_pass: dict[SheetPass, dict[tuple[str, CountSection], Decimal]] = {}
    for sheet in zone.sheets:
        lines = zone.lines_by_sheet.get(sheet.id, ())
        totals = _index_lines(lines)
        existing = by_pass.get(sheet.pass_no)
        if existing is None:
            by_pass[sheet.pass_no] = totals
        else:  # duplicate sheet for a pass: merge defensively
            for key, qty in totals.items():
                existing[key] = quantize_qty(existing.get(key, ZERO) + qty)

    p1 = by_pass.get(SheetPass.PASS_1, {})
    p2 = by_pass.get(SheetPass.PASS_2, {})
    existing_decisions = {
        (a.item_number, a.section): a for a in zone.arbitrations
    }

    out: list[ArbitrationLine] = []
    for key in sorted(set(p1) | set(p2), key=lambda k: (k[0], str(k[1]))):
        item_number, section = key
        prior = existing_decisions.get(key)
        out.append(
            ArbitrationLine(
                id=prior.id if prior else id_factory(),
                campaign_id=campaign_id,
                zone_id=zone.zone.id,
                item_number=item_number,
                section=section,
                qty_pass_1=p1.get(key),
                qty_pass_2=p2.get(key),
                qty_arbitrated=prior.qty_arbitrated if prior else None,
                decided_by=prior.decided_by if prior else None,
                decided_at=prior.decided_at if prior else None,
                comment=prior.comment if prior else "",
            )
        )
    return out


def resolve_zone_quantities(
    zone: ZoneCounts,
    *,
    passes_required: int | None = None,
    arbitration_tolerance: Decimal = ZERO,
) -> tuple[dict[tuple[str, CountSection], Decimal], list[ControlFinding]]:
    """Retained quantity per (item, section) for one zone.

    Resolution order — the first rule that applies wins:

    1. an explicit arbitration decision (``qty_arbitrated``);
    2. the two passes agree (or differ within *arbitration_tolerance*) → pass 2,
       which is the later and better-informed count;
    3. only one pass exists (single-pass zone, or the zone was counted once)
       → that pass, flagged as a warning **only when two passes were expected**;
    4. otherwise → the zone is not resolvable and a BLOCKER is emitted.

    :param passes_required: overrides the zone's own :attr:`Zone.passes`. A zone
        configured for a single count is not missing anything, so it must not be
        told that "only one team counted" — that warning only means something
        when two were expected.
    """
    if passes_required is None:
        passes_required = zone.passes_required
    findings: list[ControlFinding] = []
    by_pass: dict[SheetPass, dict[tuple[str, CountSection], Decimal]] = {}
    for sheet in zone.sheets:
        totals = _index_lines(zone.lines_by_sheet.get(sheet.id, ()))
        existing = by_pass.setdefault(sheet.pass_no, {})
        for key, qty in totals.items():
            existing[key] = quantize_qty(existing.get(key, ZERO) + qty)

    p1 = by_pass.get(SheetPass.PASS_1, {})
    p2 = by_pass.get(SheetPass.PASS_2, {})
    # Only *decided* arbitrations count. A quantity pre-filled in bulk is a
    # suggestion sitting in a field; posting it as if somebody had chosen it
    # would defeat the point of asking.
    decisions = {
        (a.item_number, a.section): a.qty_arbitrated
        for a in zone.arbitrations
        if a.is_resolved and a.qty_arbitrated is not None
    }

    retained: dict[tuple[str, CountSection], Decimal] = {}
    for key in set(p1) | set(p2) | set(decisions):
        item_number, section = key
        if key in decisions:
            retained[key] = decisions[key]
            continue

        q1, q2 = p1.get(key), p2.get(key)
        if q1 is not None and q2 is not None:
            if q1 == q2:
                retained[key] = q2
                continue
            base = max(abs(q1), abs(q2))
            if arbitration_tolerance > 0 and base > 0 and (
                abs(q2 - q1) / base <= arbitration_tolerance
            ):
                retained[key] = q2
                continue
            findings.append(
                ControlFinding(
                    code="ARBITRATION_PENDING",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"Zone {zone.zone.code} — {item_number} : comptage n°1 = {q1}, "
                        f"comptage n°2 = {q2}. Arbitrage requis."
                    ),
                    entity_type="zone",
                    entity_id=zone.zone.id,
                    item_number=item_number,
                    context={"pass1": str(q1), "pass2": str(q2),
                             "section": str(section)},
                )
            )
            continue

        single = q2 if q2 is not None else q1
        if single is None:  # unreachable given the key set, kept for safety
            continue
        retained[key] = single
        if passes_required >= 2:
            findings.append(
                ControlFinding(
                    code="SINGLE_PASS_ONLY",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"Zone {zone.zone.code} — {item_number} n'a été compté que "
                        f"par une seule équipe ({'n°2' if q2 is not None else 'n°1'})."
                    ),
                    entity_type="zone",
                    entity_id=zone.zone.id,
                    item_number=item_number,
                    context={"section": str(section)},
                )
            )

    return retained, findings


# --------------------------------------------------------------------------- #
# Step 2 — consolidate every zone into one journal
# --------------------------------------------------------------------------- #

def consolidate_generic(payload: ConsolidationInput) -> ConsolidationResult:
    """Aggregate every GENERIQUE zone into an ERP-ready INVV journal.

    Per section:

    * ``LINE_SIDE`` — counted quantity credited to the article as-is;
    * ``WIP_OK``    — the assembly is declared in the ERP, credited as-is;
    * ``WIP``       — the assembly is not declared, so it is exploded through
      the bill of materials and its **components** are credited instead.

    Articles excluded from the GENERIQUE scope (or excluded entirely) are dropped
    *after* the explosion, so a WIP assembly that is itself out of scope still
    correctly credits its in-scope components.
    """
    result = ConsolidationResult(campaign_id=payload.campaign_id)
    items = payload.items

    line_side: dict[str, Decimal] = defaultdict(Decimal)
    wip_ok: dict[str, Decimal] = defaultdict(Decimal)
    wip_exploded: dict[str, Decimal] = defaultdict(Decimal)
    contributors: dict[str, set[str]] = defaultdict(set)

    unknown_parents: set[str] = set()
    truncated_parents: set[str] = set()

    for zone_counts in payload.zones:
        zone = zone_counts.zone
        if payload.require_done_zones and not _zone_is_complete(zone_counts):
            result.zones_skipped.append(zone.code)
            continue
        result.zones_included.append(zone.code)

        retained, zone_findings = resolve_zone_quantities(
            zone_counts,
            arbitration_tolerance=payload.arbitration_tolerance,
        )
        result.findings.extend(zone_findings)

        assemblies: dict[str, Decimal] = defaultdict(Decimal)
        for (item_number, section), qty in retained.items():
            if qty == 0:
                continue
            contributors[item_number].add(zone.code)
            if section is CountSection.LINE_SIDE:
                line_side[item_number] += qty
            elif section is CountSection.WIP_OK:
                wip_ok[item_number] += qty
                _warn_if_not_assembly(item_number, items, zone.code, result.findings)
            else:  # CountSection.WIP
                assemblies[item_number] += qty

        if assemblies:
            try:
                explosion = payload.bom.explode(assemblies, zone_code=zone.code)
            except BomCycleError as exc:
                result.findings.append(
                    ControlFinding(
                        code="BOM_CYCLE",
                        severity=ControlSeverity.BLOCKER,
                        message=(
                            f"Zone {zone.code} : {exc}. La nomenclature doit être "
                            "corrigée avant consolidation."
                        ),
                        entity_type="bom",
                        context={"cycle": exc.cycle},
                    )
                )
                continue
            _merge_explosion(
                explosion, wip_exploded, contributors, zone.code, result.breakdown
            )
            unknown_parents |= explosion.unknown_parents
            truncated_parents |= explosion.truncated_parents

    _report_explosion_gaps(unknown_parents, truncated_parents, result.findings)

    # ---- assemble the journal ------------------------------------------------
    all_items = set(line_side) | set(wip_ok) | set(wip_exploded)
    for item_number in sorted(all_items):
        item = items.get(item_number)
        if item is not None and item.excluded_from_generic:
            continue
        qty_ls = quantize_qty(line_side.get(item_number, ZERO))
        qty_ok = quantize_qty(wip_ok.get(item_number, ZERO))
        qty_wip = quantize_qty(wip_exploded.get(item_number, ZERO))
        total = quantize_qty(qty_ls + qty_ok + qty_wip)
        if total == 0:
            # A net-zero article still deserves a journal line only if it was
            # genuinely counted at zero; a zero produced by offsetting sections
            # is a data problem, so surface it rather than emit a silent line.
            if qty_ls or qty_ok or qty_wip:
                result.findings.append(
                    ControlFinding(
                        code="NET_ZERO_CONSOLIDATION",
                        severity=ControlSeverity.WARNING,
                        message=(
                            f"{item_number} : les sections se compensent exactement "
                            "(total consolidé nul). À vérifier."
                        ),
                        entity_type="consolidation",
                        item_number=item_number,
                        context={"lineSide": str(qty_ls), "wipOk": str(qty_ok),
                                 "wipExploded": str(qty_wip)},
                    )
                )
            continue

        if item is None:
            result.findings.append(
                ControlFinding(
                    code="UNKNOWN_ITEM",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{item_number} est compté dans GENERIQUE mais absent du "
                        "référentiel articles de la campagne."
                    ),
                    entity_type="consolidation",
                    item_number=item_number,
                )
            )

        result.lines.append(
            ConsolidatedLine(
                campaign_id=payload.campaign_id,
                item_number=item_number,
                qty=total,
                unit=item.unit if item else "PCE",
                qty_line_side=qty_ls,
                qty_wip_ok=qty_ok,
                qty_wip_exploded=qty_wip,
                zone_codes=sorted(contributors.get(item_number, ())),
            )
        )

    result.breakdown.sort(
        key=lambda b: (b.zone_code, b.parent_item, b.child_item)
    )
    return result


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _zone_is_complete(zone: ZoneCounts) -> bool:
    """A zone contributes once every pass *it* requires is encoded and validated."""
    done = {
        sheet.pass_no
        for sheet in zone.sheets
        if sheet.status is SheetStatus.DONE
    }
    if zone.passes_required >= 2:
        return SheetPass.PASS_1 in done and SheetPass.PASS_2 in done
    return bool(done)


def _merge_explosion(
    explosion: ExplosionResult,
    target: dict[str, Decimal],
    contributors: dict[str, set[str]],
    zone_code: str,
    breakdown: list[WipBreakdown],
) -> None:
    for child, qty in explosion.components.items():
        target[child] = quantize_qty(target.get(child, ZERO) + qty)
        contributors[child].add(zone_code)
    breakdown.extend(explosion.breakdown)


def _warn_if_not_assembly(
    item_number: str,
    items: Mapping[str, Item],
    zone_code: str,
    findings: list[ControlFinding],
) -> None:
    """A ``WIP_OK`` line must reference a semi-finished or finished article."""
    item = items.get(item_number)
    if item is not None and not item.is_assembly:
        findings.append(
            ControlFinding(
                code="WIP_OK_NOT_ASSEMBLY",
                severity=ControlSeverity.WARNING,
                message=(
                    f"Zone {zone_code} — {item_number} est déclaré en section "
                    "« WIP assemblé » mais n'est ni un semi-fini ni un produit fini."
                ),
                entity_type="consolidation",
                item_number=item_number,
                context={"itemType": str(item.item_type)},
            )
        )


def _report_explosion_gaps(
    unknown_parents: set[str],
    truncated_parents: set[str],
    findings: list[ControlFinding],
) -> None:
    for parent in sorted(unknown_parents):
        findings.append(
            ControlFinding(
                code="WIP_WITHOUT_BOM",
                severity=ControlSeverity.BLOCKER,
                message=(
                    f"{parent} est compté en WIP mais n'a aucune nomenclature : "
                    "sa quantité serait perdue à l'éclatement. Reclassez la ligne "
                    "en « WIP assemblé » pour la compter telle quelle, ou "
                    "corrigez la nomenclature."
                ),
                entity_type="bom",
                item_number=parent,
                context={"remedy": "reclassify_wip_ok"},
            )
        )
    for parent in sorted(truncated_parents):
        findings.append(
            ControlFinding(
                code="BOM_DEPTH_TRUNCATED",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{parent} : l'éclatement a été arrêté à la profondeur maximale "
                    "configurée. Vérifier la structure fantôme."
                ),
                entity_type="bom",
                item_number=parent,
            )
        )
