"""Variance computation and inventory KPIs.

Three different questions get asked about the same campaign, and conflating them
is why the legacy ``RAPPORT`` tab produced numbers nobody could defend:

``NET``       "did we gain or lose value overall?" — signed sum, offsets allowed.
``GROSS``     "how much did we get wrong?" — sum of absolute variances; a +100 k€
              and a −100 k€ error are two errors, not zero.
``IRA``       "what share of our stock records were right?" — the WMS standard
              *Inventory Record Accuracy*: count the item/location pairs within
              tolerance, ignoring how big the euro amounts are.

All three are produced here, each with an explicit definition, and every one is
recomputable from the frozen snapshot so a figure quoted in a steering committee
can always be reconstructed months later.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from .enums import ItemType, LocationStatus
from .models import (
    AdjustmentLine,
    BookStockLine,
    Campaign,
    Item,
    Location,
    LocationKey,
    Thresholds,
    VarianceLine,
)
from .quantities import ZERO, quantize_money, quantize_qty, safe_ratio

__all__ = [
    "CountedQty",
    "VarianceSet",
    "KpiBlock",
    "build_variances",
    "aggregate_by",
    "compute_kpis",
    "is_material",
    "pareto",
    "AggregationKey",
]


@dataclass(frozen=True, slots=True)
class CountedQty:
    """A counted quantity located in the warehouse/location grid."""

    item_number: str
    warehouse_id: str
    location_id: str
    qty: Decimal


# --------------------------------------------------------------------------- #
# Variance construction
# --------------------------------------------------------------------------- #

def build_variances(
    *,
    campaign: Campaign,
    book_stock: Iterable[BookStockLine],
    counted: Iterable[CountedQty],
    items: Mapping[str, Item],
    locations: Mapping[LocationKey, Location] | None = None,
    adjustments: Iterable[AdjustmentLine] = (),
    granularity: str = "item_location",
) -> list[VarianceLine]:
    """Reconcile book stock against counted stock.

    :param granularity: ``"item_location"`` keeps the warehouse/location detail
        (used for the operational exception list — "go and recount bin X"), while
        ``"item"`` collapses to the article (used for the financial analysis,
        because a transfer between two locations is not a stock variance).
    :param locations: when supplied, lines whose location is ``DISABLED`` are
        dropped entirely — quantities *and* values — as the spec requires.
    :param adjustments: post-count movements; their signed quantities are
        subtracted from the variance to produce the residual.

    Both directions of the outer join are materialised: an article counted but
    absent from the book stock (``counted_only``) and an article in the book
    stock never counted (``book_only``) are the two blind spots of the legacy
    ``SUMIFS`` approach, and both are the interesting ones.
    """
    collapse = granularity == "item"

    def key_of(item: str, wh: str, loc: str) -> tuple[str, str, str]:
        return (item, "", "") if collapse else (item, wh, loc)

    def location_enabled(wh: str, loc: str) -> bool:
        if locations is None:
            return True
        entry = locations.get(LocationKey(warehouse_id=wh, location_id=loc))
        return entry is None or entry.status is LocationStatus.ACTIVE

    book_qty: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    unit_cost: dict[str, Decimal] = {}
    units: dict[str, str] = {}

    for line in book_stock:
        if not location_enabled(line.warehouse_id, line.location_id):
            continue
        k = key_of(line.item_number, line.warehouse_id, line.location_id)
        book_qty[k] += line.qty
        # The snapshot cost wins over the referential price: it is the value the
        # ERP actually carried at freeze time.
        if line.unit_cost:
            unit_cost.setdefault(line.item_number, line.unit_cost)
        if line.unit:
            units.setdefault(line.item_number, line.unit)

    counted_qty: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    for c in counted:
        if not location_enabled(c.warehouse_id, c.location_id):
            continue
        counted_qty[key_of(c.item_number, c.warehouse_id, c.location_id)] += c.qty

    adjusted_qty: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    for adj in adjustments:
        if not location_enabled(adj.warehouse_id, adj.location_id):
            continue
        adjusted_qty[key_of(adj.item_number, adj.warehouse_id, adj.location_id)] += adj.qty

    out: list[VarianceLine] = []
    for k in sorted(set(book_qty) | set(counted_qty) | set(adjusted_qty)):
        item_number, wh, loc = k
        item = items.get(item_number)
        cost = unit_cost.get(item_number)
        if cost is None:
            cost = item.std_price if item else ZERO
        out.append(
            VarianceLine(
                campaign_id=campaign.id,
                item_number=item_number,
                warehouse_id=wh,
                location_id=loc,
                item_type=item.item_type if item else ItemType.UNKNOWN,
                category=item.category if item else "",
                program=item.program if item else "",
                unit=units.get(item_number, item.unit if item else "PCE"),
                unit_cost=cost,
                book_qty=quantize_qty(book_qty.get(k, ZERO)),
                counted_qty=quantize_qty(counted_qty.get(k, ZERO)),
                adjusted_qty=quantize_qty(adjusted_qty.get(k, ZERO)),
                counted_only=k not in book_qty and k in counted_qty,
                book_only=k in book_qty and k not in counted_qty,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Materiality
# --------------------------------------------------------------------------- #

def is_material(line: VarianceLine, thresholds: Thresholds) -> bool:
    """Whether a variance breaches the materiality thresholds of its item type.

    A line is material when **every** configured gate is breached:

    * absolute value  ``|Δ€| >= value_abs_eur``
    * relative qty    ``|Δqty| / book_qty >= qty_relative``

    Requiring all gates (rather than any) keeps the exception list at a size a
    team can actually work through on the day of the inventory. A line with no
    book quantity at all is always material: stock that the ERP does not know
    about cannot be dismissed as a rounding difference.
    """
    dq = abs(line.variance_qty)
    if dq == 0:
        return False
    if line.book_qty == 0:
        return True
    if abs(line.variance_value) < thresholds.value_abs_eur:
        return False
    if thresholds.qty_relative is not None:
        ratio = safe_ratio(dq, abs(line.book_qty))
        if ratio is not None and ratio < thresholds.qty_relative:
            return False
    return True


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

#: Dimensions the analysis screens may group by.
AggregationKey = str

_KEY_ACCESSORS = {
    "item": lambda v: v.item_number,
    "warehouse": lambda v: v.warehouse_id,
    "location": lambda v: f"{v.warehouse_id} / {v.location_id}",
    "item_type": lambda v: str(v.item_type),
    "category": lambda v: v.category or "(non catégorisé)",
    "program": lambda v: v.program or "(commun)",
}


@dataclass(slots=True)
class VarianceSet:
    """A group of variance lines plus its pre-computed totals."""

    key: str
    lines: list[VarianceLine] = field(default_factory=list)

    book_qty: Decimal = ZERO
    book_value: Decimal = ZERO
    variance_qty: Decimal = ZERO
    variance_value: Decimal = ZERO
    abs_variance_qty: Decimal = ZERO
    abs_variance_value: Decimal = ZERO
    residual_value: Decimal = ZERO
    line_count: int = 0
    material_count: int = 0

    def add(self, line: VarianceLine, *, material: bool) -> None:
        self.lines.append(line)
        self.book_qty += line.book_qty
        self.book_value += line.book_value
        self.variance_qty += line.variance_qty
        self.variance_value += line.variance_value
        self.abs_variance_qty += abs(line.variance_qty)
        self.abs_variance_value += abs(line.variance_value)
        self.residual_value += line.residual_value
        self.line_count += 1
        self.material_count += int(material)

    def finalise(self) -> None:
        self.book_qty = quantize_qty(self.book_qty)
        self.variance_qty = quantize_qty(self.variance_qty)
        self.abs_variance_qty = quantize_qty(self.abs_variance_qty)
        for attr in ("book_value", "variance_value", "abs_variance_value",
                     "residual_value"):
            setattr(self, attr, quantize_money(getattr(self, attr)))


def aggregate_by(
    lines: Iterable[VarianceLine],
    dimension: AggregationKey,
    *,
    campaign: Campaign | None = None,
    keep_lines: bool = False,
) -> list[VarianceSet]:
    """Group variances along *dimension* and total them.

    :param keep_lines: keep the underlying lines in each group. Off by default so
        an aggregate over 100 000 lines stays small enough for an HTTP response.
    """
    accessor = _KEY_ACCESSORS.get(dimension)
    if accessor is None:
        raise ValueError(
            f"unknown aggregation dimension {dimension!r}; "
            f"expected one of {sorted(_KEY_ACCESSORS)}"
        )

    groups: dict[str, VarianceSet] = {}
    for line in lines:
        thresholds = (
            campaign.threshold_for(line.item_type)
            if campaign
            else Thresholds(item_type=line.item_type)
        )
        group = groups.setdefault(accessor(line), VarianceSet(key=accessor(line)))
        group.add(line, material=is_material(line, thresholds))

    out = list(groups.values())
    for group in out:
        group.finalise()
        if not keep_lines:
            group.lines = []
    out.sort(key=lambda g: abs(g.variance_value), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class KpiBlock:
    """Headline figures of a campaign.

    Every ratio is ``None`` rather than 0 when its base is zero, so the UI can
    render "n/a" instead of a misleading 0 %.
    """

    book_qty: Decimal = ZERO
    book_value: Decimal = ZERO
    counted_qty: Decimal = ZERO
    counted_value: Decimal = ZERO

    #: Signed variance — "did we gain or lose value overall?"
    net_variance_qty: Decimal = ZERO
    net_variance_value: Decimal = ZERO
    #: Absolute variance — "how much did we get wrong?"
    gross_variance_qty: Decimal = ZERO
    gross_variance_value: Decimal = ZERO
    #: Variance still unexplained after adjustments.
    residual_value: Decimal = ZERO

    #: 1 − |Σ Δ€| / Σ book€ — the optimistic view (offsets allowed).
    net_reliability_value: Decimal | None = None
    #: 1 − Σ|Δ€| / Σ book€ — the honest view; this is the one to steer on.
    gross_reliability_value: Decimal | None = None
    #: Same, on quantities.
    gross_reliability_qty: Decimal | None = None
    #: Share of item/location records within tolerance (WMS standard IRA).
    ira: Decimal | None = None

    line_count: int = 0
    accurate_line_count: int = 0
    material_line_count: int = 0
    counted_only_count: int = 0
    book_only_count: int = 0

    def as_dict(self) -> dict[str, object]:
        def num(v: Decimal | None) -> float | None:
            return None if v is None else float(v)

        return {
            "bookQty": num(self.book_qty),
            "bookValue": num(self.book_value),
            "countedQty": num(self.counted_qty),
            "countedValue": num(self.counted_value),
            "netVarianceQty": num(self.net_variance_qty),
            "netVarianceValue": num(self.net_variance_value),
            "grossVarianceQty": num(self.gross_variance_qty),
            "grossVarianceValue": num(self.gross_variance_value),
            "residualValue": num(self.residual_value),
            "netReliabilityValue": num(self.net_reliability_value),
            "grossReliabilityValue": num(self.gross_reliability_value),
            "grossReliabilityQty": num(self.gross_reliability_qty),
            "ira": num(self.ira),
            "lineCount": self.line_count,
            "accurateLineCount": self.accurate_line_count,
            "materialLineCount": self.material_line_count,
            "countedOnlyCount": self.counted_only_count,
            "bookOnlyCount": self.book_only_count,
        }


def compute_kpis(
    lines: Iterable[VarianceLine], *, campaign: Campaign | None = None
) -> KpiBlock:
    """Headline KPIs over a set of variance lines.

    A record counts as *accurate* for IRA when ``|Δqty| / book_qty`` is within
    counted quantity matches the book exactly. There is no tolerance: a record
    that is off by one is a record that was wrong, and softening that would make
    the indicator agree with itself rather than with the shelf.
    Records with a zero book quantity are accurate only when nothing was counted
    either — "we thought there was nothing and there was nothing".
    """
    kpi = KpiBlock()
    for line in lines:
        thresholds = (
            campaign.threshold_for(line.item_type)
            if campaign
            else Thresholds(item_type=line.item_type)
        )
        kpi.line_count += 1
        kpi.book_qty += line.book_qty
        kpi.book_value += line.book_value
        kpi.counted_qty += line.counted_qty
        kpi.counted_value += line.counted_qty * line.unit_cost
        kpi.net_variance_qty += line.variance_qty
        kpi.net_variance_value += line.variance_value
        kpi.gross_variance_qty += abs(line.variance_qty)
        kpi.gross_variance_value += abs(line.variance_value)
        kpi.residual_value += line.residual_value
        kpi.counted_only_count += int(line.counted_only)
        kpi.book_only_count += int(line.book_only)
        kpi.material_line_count += int(is_material(line, thresholds))

        if _is_accurate(line):
            kpi.accurate_line_count += 1

    kpi.book_qty = quantize_qty(kpi.book_qty)
    kpi.counted_qty = quantize_qty(kpi.counted_qty)
    kpi.net_variance_qty = quantize_qty(kpi.net_variance_qty)
    kpi.gross_variance_qty = quantize_qty(kpi.gross_variance_qty)
    for attr in ("book_value", "counted_value", "net_variance_value",
                 "gross_variance_value", "residual_value"):
        setattr(kpi, attr, quantize_money(getattr(kpi, attr)))

    base_value = abs(kpi.book_value)
    net_ratio = safe_ratio(abs(kpi.net_variance_value), base_value)
    gross_ratio = safe_ratio(kpi.gross_variance_value, base_value)
    qty_ratio = safe_ratio(kpi.gross_variance_qty, abs(kpi.book_qty))

    kpi.net_reliability_value = None if net_ratio is None else _clamp(1 - net_ratio)
    kpi.gross_reliability_value = None if gross_ratio is None else _clamp(1 - gross_ratio)
    kpi.gross_reliability_qty = None if qty_ratio is None else _clamp(1 - qty_ratio)
    kpi.ira = (
        Decimal(kpi.accurate_line_count) / Decimal(kpi.line_count)
        if kpi.line_count
        else None
    )
    return kpi


def _is_accurate(line: VarianceLine) -> bool:
    if line.book_qty == 0:
        return line.counted_qty == 0
    return line.variance_qty == 0


def _clamp(value: Decimal) -> Decimal:
    """Keep a reliability ratio inside [-1, 1] for display sanity.

    A gross variance larger than the book value legitimately produces a negative
    reliability; it is clamped at −1 so a single pathological article cannot make
    a chart unreadable, and the raw ratio stays available in the numerator/
    denominator fields.
    """
    if value < Decimal("-1"):
        return Decimal("-1")
    return min(value, Decimal("1"))


# --------------------------------------------------------------------------- #
# Pareto
# --------------------------------------------------------------------------- #

def pareto(
    groups: Sequence[VarianceSet], *, coverage: Decimal = Decimal("0.8")
) -> list[VarianceSet]:
    """The smallest set of groups covering *coverage* of the absolute variance.

    This is the "attack list": in every campaign analysed, fewer than 30 articles
    carried more than 80 % of the euro variance, and everything else was noise.
    """
    total = sum((g.abs_variance_value for g in groups), ZERO)
    if total <= 0:
        return []
    ranked = sorted(groups, key=lambda g: g.abs_variance_value, reverse=True)
    out: list[VarianceSet] = []
    cumulated = ZERO
    for group in ranked:
        out.append(group)
        cumulated += group.abs_variance_value
        if cumulated / total >= coverage:
            break
    return out
