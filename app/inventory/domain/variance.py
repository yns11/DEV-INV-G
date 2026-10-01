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
    BackflushLine,
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
    "at_standard_price",
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

def at_standard_price(
    lines: Iterable[BookStockLine], items: Mapping[str, Item]
) -> list[BookStockLine]:
    """Revaloriser des lignes de stock ERP au prix standard du référentiel.

    **Une seule base de valorisation dans toute la campagne** : `prix standard ×
    quantité`, pour le stock ERP comme pour le stock compté. C'est ce qui rend
    les deux totaux comparables — un écart en euros mesure alors une différence
    de *quantité*, et rien d'autre.

    Les lignes de stock portent bien un coût, mais il n'a pas une origine
    unique : celles du snapshot général portent ce que l'ERP tenait au gel,
    celles d'un emplacement précompté portent déjà le prix standard. Les écrans
    qui les affichaient telles quelles — la grille Stock ERP, son total, l'export
    Excel, la liste des articles non comptés — valorisaient donc autrement que
    les écarts et les KPI, sur les mêmes lignes.

    Le coût d'origine reste le secours : pour un article que le référentiel ne
    connaît pas, ou dont le prix standard est nul, mieux vaut la valeur que
    l'ERP portait que zéro.
    """
    out: list[BookStockLine] = []
    for line in lines:
        item = items.get(line.item_number)
        price = item.std_price if item else ZERO
        cost = price or line.unit_cost
        out.append(
            line if cost == line.unit_cost else line.model_copy(
                update={"unit_cost": cost}
            )
        )
    return out


def build_variances(
    *,
    campaign: Campaign,
    book_stock: Iterable[BookStockLine],
    counted: Iterable[CountedQty],
    items: Mapping[str, Item],
    locations: Mapping[LocationKey, Location] | None = None,
    adjustments: Iterable[AdjustmentLine] = (),
    backflush: Mapping[str, BackflushLine] | None = None,
    granularity: str = "item_location",
) -> list[VarianceLine]:
    """Reconcile book stock against counted stock.

    :param granularity: ``"item_location"`` keeps the warehouse/location detail
        (used for the operational exception list — "go and recount bin X"), while
        ``"item"`` collapses to the article (used for the financial analysis,
        because a transfer between two locations is not a stock variance).
    :param locations: when supplied, lines whose location is ``DISABLED`` are
        dropped entirely — quantities *and* values — as the spec requires.
    :param adjustments: post-count movements. Their signed quantities are *added
        to the count* to give the physical stock, which is what the variance is
        then measured against: an adjustment is a real movement, so once one is
        posted the count alone is no longer the current picture.
    :param backflush: the frozen backflush variance, per article. It is carried
        onto the line rather than netted into it — what production explains and
        what the ERP has already been corrected for are two different questions,
        and a single "corrected" quantity would answer neither.

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
    #: Le coût porté par les lignes de stock, gardé en **secours seulement**.
    #:
    #: La valorisation de la campagne est le **prix standard du référentiel**,
    #: partout et des deux côtés — stock ERP comme stock compté. C'est ce qui
    #: rend les deux totaux comparables : un écart de quantité vaut le prix de
    #: l'article, pas la différence entre deux façons de le valoriser.
    #:
    #: Le stock ERP a maintenant deux origines dans la même table — le snapshot
    #: général du jour J et la référence d'un emplacement précompté — et elles
    #: ne portent pas le même coût. Les laisser décider revenait à faire dépendre
    #: la valeur d'un article de l'ordre de ses lignes, et à valoriser le stock
    #: ERP et le comptage à deux bases différentes.
    #:
    #: Ce coût-là ne sert donc plus que pour un article que le référentiel ne
    #: connaît pas, ou dont le prix standard est nul : mieux vaut la valeur que
    #: l'ERP portait que zéro.
    fallback_cost: dict[str, Decimal] = {}
    units: dict[str, str] = {}

    for line in book_stock:
        if not location_enabled(line.warehouse_id, line.location_id):
            continue
        k = key_of(line.item_number, line.warehouse_id, line.location_id)
        book_qty[k] += line.qty
        if line.unit_cost:
            fallback_cost.setdefault(line.item_number, line.unit_cost)
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

    # The backflush is measured per article and has no location: production
    # consumes from a line, not from a bin. In the per-location reading it is
    # therefore attached only to the article's total, which is the granularity
    # at which it means anything — spreading it over bins would invent a
    # distribution the source never had.
    _bf = dict(backflush or {}) if collapse else {}

    out: list[VarianceLine] = []
    for k in sorted(set(book_qty) | set(counted_qty) | set(adjusted_qty)):
        item_number, wh, loc = k
        item = items.get(item_number)
        # **Un article exclu du périmètre ne produit aucun écart.**
        #
        # C'est la règle que `in_perimeter` énonce pour les lectures ERP, et
        # elle vaut ici pour la même raison : « un article délibérément laissé
        # hors du périmètre ne doit pas revenir par les quantités relevées
        # dessus ». Elle n'était appliquée qu'à l'entrée du stock ERP, dont la
        # ligne n'est plus chargée — mais le *comptage*, lui, arrivait par les
        # feuilles et les journaux, et un article exclu ressortait en écart
        # égal à la totalité du comptage, systématiquement matériel puisque son
        # stock ERP vaut zéro. Une exclusion produisait donc exactement ce
        # qu'elle existe pour éviter.
        #
        # `excluded_everywhere` et non `excluded_from_generic` : une exclusion
        # GENERIQUE ne retire l'article que de la consolidation des zones, et
        # son écart reste légitime là où il est bel et bien inventorié.
        #
        # Ce qui est compté sur un article exclu n'est pas perdu pour autant :
        # `EXCLUDED_ITEM_COUNTED` le signale dans les contrôles, journaux et
        # feuilles compris. Le retirer d'ici sans le dire ailleurs serait la
        # troncature muette que ce projet refuse.
        if item is not None and item.excluded_everywhere:
            continue
        # `prix standard × quantité`, des deux côtés de l'écart. Une seule base
        # de valorisation pour le stock ERP et pour le comptage : sinon l'écart
        # en euros mélangerait une différence de quantité et une différence de
        # méthode, et personne ne saurait dire laquelle il regarde.
        cost = item.std_price if item else ZERO
        if not cost:
            cost = fallback_cost.get(item_number, ZERO)
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
                backflush_qty=bf.net_qty if (bf := _bf.get(item_number)) else ZERO,
                backflush_measured=item_number in _bf,
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
    #: L'écart que le comptage seul montrait, avant tout ajustement. Conservé
    #: à côté de l'écart et non à sa place : la différence entre les deux est
    #: exactement ce que les ajustements ont fait.
    counted_variance_value: Decimal = ZERO
    backflush_share_value: Decimal = ZERO
    unexplained_value: Decimal = ZERO
    abs_unexplained_value: Decimal = ZERO
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
        self.counted_variance_value += line.counted_variance_value
        self.backflush_share_value += line.backflush_share_value
        self.unexplained_value += line.unexplained_value
        self.abs_unexplained_value += abs(line.unexplained_value)
        self.line_count += 1
        self.material_count += int(material)

    def finalise(self) -> None:
        self.book_qty = quantize_qty(self.book_qty)
        self.variance_qty = quantize_qty(self.variance_qty)
        self.abs_variance_qty = quantize_qty(self.abs_variance_qty)
        for attr in ("book_value", "variance_value", "abs_variance_value",
                     "counted_variance_value", "backflush_share_value",
                     "unexplained_value", "abs_unexplained_value"):
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
    #: The physical stock — counted plus what moved after. This, not the count,
    #: is what the variance measures, so it is reported next to the ERP total:
    #: the two figures on screen must be the two the subtraction uses.
    physical_qty: Decimal = ZERO
    physical_value: Decimal = ZERO

    #: Signed variance — "did we gain or lose value overall?"
    net_variance_qty: Decimal = ZERO
    net_variance_value: Decimal = ZERO
    #: Absolute variance — "how much did we get wrong?"
    gross_variance_qty: Decimal = ZERO
    gross_variance_value: Decimal = ZERO
    #: The gap the count alone showed, before the adjustments moved the stock.
    #: Reported beside the variance so the effect of the adjustments is readable
    #: as the difference between two figures rather than asserted in a sentence.
    counted_variance_value: Decimal = ZERO
    #: Signed total of the movements posted during the analysis phase.
    adjusted_value: Decimal = ZERO

    #: What the backflush accounts for, in the inventory convention, and what is
    #: left once it is taken off. Reported over the articles the backflush was
    #: actually measured on: averaging in the ones it never covered would dilute
    #: the rate towards zero and make a good explanation look like a poor one.
    backflush_share_value: Decimal = ZERO
    unexplained_value: Decimal = ZERO
    gross_unexplained_value: Decimal = ZERO
    #: The inventory variance of those same articles. Reported alongside the two
    #: above so the three read as one subtraction: variance − share = unexplained.
    #: Taking the campaign-wide variance instead would put a total over one
    #: population next to two over another, and the arithmetic on screen would
    #: not close.
    backflush_variance_value: Decimal = ZERO
    #: 1 − Σ|inexpliqué| / Σ|écart|, over the measured articles only.
    backflush_explanation_rate: Decimal | None = None
    backflush_line_count: int = 0

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
            "physicalQty": num(self.physical_qty),
            "physicalValue": num(self.physical_value),
            "netVarianceQty": num(self.net_variance_qty),
            "netVarianceValue": num(self.net_variance_value),
            "grossVarianceQty": num(self.gross_variance_qty),
            "grossVarianceValue": num(self.gross_variance_value),
            "countedVarianceValue": num(self.counted_variance_value),
            "adjustedValue": num(self.adjusted_value),
            "backflushShareValue": num(self.backflush_share_value),
            "unexplainedValue": num(self.unexplained_value),
            "grossUnexplainedValue": num(self.gross_unexplained_value),
            "backflushVarianceValue": num(self.backflush_variance_value),
            "backflushExplanationRate": num(self.backflush_explanation_rate),
            "backflushLineCount": self.backflush_line_count,
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
    #: Absolute variance of the articles the backflush covers. The explanation
    #: rate is a ratio of two sums over the *same* population, and mixing in the
    #: articles production never touched would answer a different question.
    measured_gap = ZERO
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
        kpi.physical_qty += line.physical_qty
        kpi.physical_value += line.physical_value
        kpi.net_variance_qty += line.variance_qty
        kpi.net_variance_value += line.variance_value
        kpi.gross_variance_qty += abs(line.variance_qty)
        kpi.gross_variance_value += abs(line.variance_value)
        kpi.counted_variance_value += line.counted_variance_value
        kpi.adjusted_value += line.adjusted_value
        if line.backflush_measured:
            kpi.backflush_line_count += 1
            kpi.backflush_share_value += line.backflush_share_value
            kpi.unexplained_value += line.unexplained_value
            kpi.gross_unexplained_value += abs(line.unexplained_value)
            kpi.backflush_variance_value += line.variance_value
            measured_gap += abs(line.variance_value)
        kpi.counted_only_count += int(line.counted_only)
        kpi.book_only_count += int(line.book_only)
        kpi.material_line_count += int(is_material(line, thresholds))

        if _is_accurate(line):
            kpi.accurate_line_count += 1

    kpi.book_qty = quantize_qty(kpi.book_qty)
    kpi.counted_qty = quantize_qty(kpi.counted_qty)
    kpi.physical_qty = quantize_qty(kpi.physical_qty)
    kpi.net_variance_qty = quantize_qty(kpi.net_variance_qty)
    kpi.gross_variance_qty = quantize_qty(kpi.gross_variance_qty)
    for attr in ("book_value", "counted_value", "physical_value",
                 "net_variance_value",
                 "gross_variance_value", "counted_variance_value",
                 "adjusted_value",
                 "backflush_share_value", "unexplained_value",
                 "gross_unexplained_value", "backflush_variance_value"):
        setattr(kpi, attr, quantize_money(getattr(kpi, attr)))

    explained = safe_ratio(kpi.gross_unexplained_value, abs(measured_gap))
    # Not clamped: a negative rate says taking the backflush into account widens
    # the gap instead of closing it, which is a finding, not a formula error.
    kpi.backflush_explanation_rate = None if explained is None else 1 - explained

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
