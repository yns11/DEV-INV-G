"""Domain entities.

These models are pure data + invariants: they never touch the database, the
warehouse or HTTP. That separation is what lets the whole business logic
(BOM explosion, GENERIQUE consolidation, variance, controls) be unit-tested
without any Databricks dependency — the property the Excel tool never had.

Naming follows the business vocabulary of the specification (campagne, journal,
feuille de comptage, zone, écart) with English identifiers, and the *legacy*
labels are only ever seen at the import boundary.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AdjustmentKind,
    CampaignStatus,
    ControlSeverity,
    CountSection,
    DataSource,
    ExclusionScope,
    ItemCommonality,
    ItemType,
    JournalKind,
    JournalStatus,
    LocationStatus,
    LocationType,
    SheetPass,
    SheetStatus,
    ZoneStatus,
)
from .quantities import ZERO, quantize_money, quantize_qty, to_decimal

__all__ = [
    "DomainModel",
    "Qty",
    "Money",
    "normalise_key",
    "LocationKey",
    "Thresholds",
    "CampaignConfig",
    "Campaign",
    "Item",
    "BomLink",
    "Warehouse",
    "Location",
    "Manager",
    "BookStockLine",
    "CountJournal",
    "CountJournalLine",
    "Zone",
    "CountSheet",
    "CountSheetLine",
    "ArbitrationLine",
    "ConsolidatedLine",
    "WipBreakdown",
    "AdjustmentLine",
    "VarianceLine",
    "ControlFinding",
    "AuditEvent",
    "AssignableCause",
    "VarianceAnalysis",
]


# --------------------------------------------------------------------------- #
# Base types
# --------------------------------------------------------------------------- #

class DomainModel(BaseModel):
    """Common configuration for every domain entity."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        frozen=False,
        populate_by_name=True,
    )


def _as_qty(value: Any) -> Decimal:
    return quantize_qty(to_decimal(value))


def _as_money(value: Any) -> Decimal:
    return quantize_money(to_decimal(value))


#: A quantity, stored with 6 decimals. Fields declare the type and rely on the
#: ``_as_qty`` validators above for normalisation.
Qty = Annotated[Decimal, "quantity, 6 decimals"]
#: A monetary amount in the campaign currency, stored with 2 decimals.
Money = Annotated[Decimal, "monetary amount, 2 decimals"]


_WHITESPACE_RE = re.compile(r"\s+")


def normalise_key(value: str | None) -> str:
    """Canonical form of a business key (warehouse, location, item number).

    Upper-cases, collapses internal whitespace and trims. The specification
    explicitly requires warehouses and locations to be upper-cased when the book
    stock is frozen; applying the same rule to *every* key removes the whole
    family of "``PAL B2S 01``" vs "``pal b2s  01``" duplicates that produced
    phantom variances in the legacy files.

    >>> normalise_key("  pal b2s   01 ")
    'PAL B2S 01'
    >>> normalise_key(None)
    ''
    """
    if value is None:
        return ""
    return _WHITESPACE_RE.sub(" ", value.strip()).upper()


class LocationKey(DomainModel):
    """Composite identity of a stock location.

    Two locations in different warehouses may share a name, so the *pair* is the
    key — never a concatenated string, per the modelling rules of the spec.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    warehouse_id: str
    location_id: str

    @field_validator("warehouse_id", "location_id", mode="before")
    @classmethod
    def _normalise(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.warehouse_id} / {self.location_id}"

    @property
    def is_blank(self) -> bool:
        return not self.warehouse_id and not self.location_id


# --------------------------------------------------------------------------- #
# Campaign
# --------------------------------------------------------------------------- #

class Thresholds(DomainModel):
    """Materiality thresholds for one :class:`~inventory.domain.enums.ItemType`.

    A variance is *material* when it breaches **all** the configured gates that
    apply (value AND relative quantity), which keeps the exception list short
    and actionable. ``None`` disables a gate.
    """

    item_type: ItemType
    #: Absolute variance value in EUR above which the line is an exception.
    value_abs_eur: Decimal = Field(default=Decimal("1000"))
    #: |Δqty| / book_qty above which the line is an exception (0.05 = 5 %).
    qty_relative: Decimal | None = Field(default=Decimal("0.02"))

    @field_validator("value_abs_eur", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Decimal:
        return _as_money(v)

    @field_validator("qty_relative", mode="before")
    @classmethod
    def _ratio(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        d = to_decimal(v)
        if d < 0:
            raise ValueError("a tolerance ratio cannot be negative")
        return d


class CampaignConfig(DomainModel):
    """Frozen configuration of a campaign (part of the immutable dossier)."""

    generic_warehouse: str = "B06VRAC"
    generic_location: str = "GENERIQUE"
    #: Number of independent counts required on GENERIQUE zones.
    generic_passes: int = Field(default=2, ge=1, le=3)
    #: Relative gap between pass 1 and pass 2 above which arbitration is
    #: mandatory rather than automatic (0 = any difference triggers arbitration).
    arbitration_tolerance: Decimal = Field(default=Decimal("0"))
    #: Maximum BOM explosion depth. Guards against pathological structures; a
    #: cycle is detected and reported regardless of this value.
    max_bom_depth: int = Field(default=10, ge=1, le=25)
    #: Currency of every monetary amount in the campaign.
    currency: str = "EUR"

    @field_validator("generic_warehouse", "generic_location", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> str:
        return normalise_key(str(v))

    @property
    def generic_key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.generic_warehouse, location_id=self.generic_location
        )


class Campaign(DomainModel):
    """An inventory campaign — the immutable dossier the whole app revolves around."""

    id: str
    code: str
    label: str
    count_date: dt.date
    status: CampaignStatus = CampaignStatus.PREPARATION
    config: CampaignConfig = Field(default_factory=CampaignConfig)
    thresholds: list[Thresholds] = Field(default_factory=list)

    #: Set when the referentials are frozen (entering COUNTING).
    referentials_frozen_at: dt.datetime | None = None
    #: Set when the book stock snapshot is taken and frozen.
    book_stock_frozen_at: dt.datetime | None = None
    #: Set when counting is closed (entering ANALYSIS).
    counting_frozen_at: dt.datetime | None = None
    closed_at: dt.datetime | None = None

    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime | None = None
    #: Code of the campaign this one was duplicated from, if any.
    cloned_from_code: str | None = None
    #: Version of the calculation engine that produced the stored derived data.
    engine_version: str = "1.0.0"

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: Any) -> str:
        code = normalise_key(str(v)).replace(" ", "-")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,49}", code):
            raise ValueError(
                "campaign code must be 3-50 chars of A-Z, 0-9, '.', '_' or '-'"
            )
        return code

    def threshold_for(self, item_type: ItemType) -> Thresholds:
        """Thresholds configured for *item_type*, or a permissive default."""
        for t in self.thresholds:
            if t.item_type is item_type:
                return t
        return Thresholds(item_type=item_type)

    @property
    def is_frozen(self) -> bool:
        return self.status is CampaignStatus.CLOSED


# --------------------------------------------------------------------------- #
# Referentials (snapshotted per campaign)
# --------------------------------------------------------------------------- #

class Item(DomainModel):
    """An article, as frozen for one campaign."""

    campaign_id: str
    item_number: str
    name: str = ""
    search_name: str = ""
    item_group: str = ""
    lifecycle_state: str = ""
    item_type: ItemType = ItemType.UNKNOWN
    #: Business family — MEL, STATOR, ONDULEUR, ROTOR, …
    category: str = ""
    #: Programme the article belongs to — M2BEV, M3, M4, M3GEN2, M2ERAD, …
    program: str = ""
    commonality: ItemCommonality = ItemCommonality.UNKNOWN
    unit: str = "PCE"
    #: Standard cost used to value quantities, in the campaign currency.
    std_price: Decimal = ZERO
    #: Facets of exclusion; empty set == fully in scope.
    exclusions: set[ExclusionScope] = Field(default_factory=set)
    source: DataSource = DataSource.FILE_IMPORT

    @field_validator("item_number", mode="before")
    @classmethod
    def _item_number(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("item_number is required")
        return key

    @field_validator("unit", "category", "program", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("std_price", mode="before")
    @classmethod
    def _price(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)

    @field_validator("exclusions", mode="before")
    @classmethod
    def _exclusions(cls, v: Any) -> set[ExclusionScope]:
        return ExclusionScope.normalise(v)

    # -- scope helpers --------------------------------------------------------
    @property
    def excluded_everywhere(self) -> bool:
        return ExclusionScope.ALL in self.exclusions

    @property
    def excluded_from_generic(self) -> bool:
        """Excluded from the GENERIQUE consolidation and its analysis."""
        return self.excluded_everywhere or ExclusionScope.GENERIC in self.exclusions

    @property
    def excluded_from_bom(self) -> bool:
        """Ignored when exploding a parent's bill of materials."""
        return self.excluded_everywhere or ExclusionScope.BOM in self.exclusions

    @property
    def is_assembly(self) -> bool:
        return self.item_type in (ItemType.SEMI_FINISHED, ItemType.FINISHED)

    def value_of(self, qty: Decimal) -> Decimal:
        """Valuation of *qty* at the frozen standard price."""
        return quantize_money(qty * self.std_price)


class BomLink(DomainModel):
    """One parent → child edge of the bill of materials, frozen per campaign."""

    campaign_id: str
    parent_item: str
    child_item: str
    #: Quantity of *child* consumed by one *parent*.
    qty_per: Decimal
    unit: str = "PCE"
    #: 0 for the top level; kept to reproduce the ERP's effective BOM view.
    level: int = 1
    #: Whether this version of the recipe is the one in force.
    #:
    #: The ERP keeps every version of a bill of materials, active or not, and
    #: the campaign now loads them all — an assembly whose only recipe is
    #: retired *has* a structure, and reporting it as having none produced a
    #: page of alerts nobody could act on. Only the active versions are exploded
    #: though: adding a retired quantity to a live one would inflate the
    #: component count with parts the assembly no longer contains.
    active: bool = True

    @field_validator("parent_item", "child_item", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("BOM link requires both a parent and a child item")
        return key

    @field_validator("qty_per", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        d = _as_qty(v)
        if d <= 0:
            raise ValueError("qty_per must be strictly positive")
        return d

    @model_validator(mode="after")
    def _no_self_link(self) -> Self:
        if self.parent_item == self.child_item:
            raise ValueError(
                f"BOM link {self.parent_item} → itself is a one-node cycle"
            )
        return self


class Warehouse(DomainModel):
    """A warehouse of the site. The single-site assumption makes `site` noise."""

    campaign_id: str
    warehouse_id: str
    label: str = ""
    type: LocationType = LocationType.UNKNOWN
    status: LocationStatus = LocationStatus.ACTIVE

    @field_validator("warehouse_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("warehouse_id is required")
        return key


class Location(DomainModel):
    """A stock location inside a warehouse."""

    campaign_id: str
    warehouse_id: str
    location_id: str
    zone: str = ""
    type: LocationType = LocationType.UNKNOWN
    status: LocationStatus = LocationStatus.ACTIVE
    source: DataSource = DataSource.SYSTEM

    @field_validator("warehouse_id", "location_id", "zone", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @model_validator(mode="after")
    def _require_warehouse(self) -> Self:
        if not self.warehouse_id:
            raise ValueError("warehouse_id is required")
        return self

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def is_active(self) -> bool:
        return self.status is LocationStatus.ACTIVE


class Manager(DomainModel):
    """One of the campaign's managers (« gestionnaire ») and their identity.

    A manager is a *perimeter*, not a permission: warehouses and zones are
    assigned to one so that each person can filter the screens down to their own
    work. Everybody keeps the right to act everywhere — see the focus mode.

    ``actor`` is the signed-in identity forwarded by the platform (an email).
    It is what lets the server resolve "my perimeter" without the client ever
    naming a manager, which is what makes the filtering trustworthy.
    """

    campaign_id: str
    code: str
    label: str = ""
    actor: str = ""
    active: bool = True
    display_order: int = 0

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "").replace(" ", "_")
        if not key:
            raise ValueError("manager code is required")
        return key

    @field_validator("actor", mode="before")
    @classmethod
    def _actor(cls, v: Any) -> str:
        return str(v or "").strip().lower()


class BookStockLine(DomainModel):
    """One line of the frozen ERP book stock (``stock ERP``) snapshot."""

    campaign_id: str
    item_number: str
    warehouse_id: str
    location_id: str
    qty: Decimal = ZERO
    unit: str = "PCE"
    #: Unit cost captured at snapshot time; the campaign is valued with it.
    unit_cost: Decimal = ZERO

    @field_validator("item_number", "warehouse_id", "location_id", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @field_validator("unit_cost", mode="before")
    @classmethod
    def _cost(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def value(self) -> Decimal:
        return quantize_money(self.qty * self.unit_cost)


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #

class CountJournal(DomainModel):
    """One ERP counting journal — exactly one per active warehouse+location."""

    id: str
    campaign_id: str
    warehouse_id: str
    location_id: str
    kind: JournalKind = JournalKind.INVV
    status: JournalStatus = JournalStatus.PENDING
    #: ERP journal number (``NPEM-522160``); empty until the journal exists.
    journal_number: str = ""
    description: str = ""
    posted_at: dt.datetime | None = None
    #: True when the journal was auto-created from an imported line whose
    #: location was absent from the book stock (book qty = 0, counted > 0).
    auto_created: bool = False
    updated_at: dt.datetime | None = None

    @field_validator("warehouse_id", "location_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def is_complete(self) -> bool:
        """A journal contributes to progress once posted or book-enforced."""
        return self.status in (JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED)


class CountJournalLine(DomainModel):
    """One counted article inside a journal.

    The imported value and the manually corrected value are kept side by side —
    never overwritten — so that a reload of the ERP export can refresh
    ``qty_imported`` without destroying a human decision, and so that the audit
    trail shows exactly what a person changed.
    """

    id: str
    journal_id: str
    campaign_id: str
    item_number: str
    #: Value as received from the ERP export / file import.
    qty_imported: Decimal | None = None
    #: Value typed or pasted by a user; wins over ``qty_imported`` when set.
    qty_manual: Decimal | None = None
    unit: str = "PCE"
    source: DataSource = DataSource.ERP_IMPORT
    comment: str = ""
    updated_by: str | None = None
    updated_at: dt.datetime | None = None

    @field_validator("item_number", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty_imported", "qty_manual", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return _as_qty(v)

    @property
    def qty(self) -> Decimal:
        """Effective counted quantity: the manual value when present."""
        return self.qty_manual if self.qty_manual is not None else (
            self.qty_imported if self.qty_imported is not None else ZERO
        )

    @property
    def effective_source(self) -> DataSource:
        return DataSource.MANUAL if self.qty_manual is not None else self.source

    @property
    def is_overridden(self) -> bool:
        return self.qty_manual is not None and self.qty_manual != self.qty_imported


class Zone(DomainModel):
    """A physical zone of the GENERIQUE location (line side, picking, lab, …).

    GENERIQUE is one ERP location but many physical areas; each is counted twice
    by two independent teams, then arbitrated.
    """

    id: str
    campaign_id: str
    code: str
    label: str = ""
    status: ZoneStatus = ZoneStatus.PENDING
    #: Free-text owner/sector, used for dispatching printed sheets.
    sector: str = ""
    display_order: int = 0
    #: Number of independent counts this zone requires. Two is the rule; one is
    #: the assumed exception for an area where a second team adds nothing.
    passes: int = Field(default=2, ge=1, le=2)
    #: True when the sheet is deliberately blank — the counter writes down what
    #: they find, there is no pre-printed article list. Distinguishing this from
    #: "the list was never prepared" is what stops the preparation controls from
    #: reporting a normal free-entry sheet as a defect.
    free_entry: bool = False
    #: Code of the manager (:class:`Manager`) this zone is assigned to; empty
    #: when nobody owns it yet.
    manager_code: str = ""
    #: Whether a negative counted quantity is accepted on this zone's sheets.
    #: Off by default: one does not find minus twenty screws in a bin, so a
    #: negative is almost always a typo, and catching it at the keyboard is far
    #: cheaper than explaining it at the variance meeting. Correction sheets are
    #: the legitimate exception, and they say so.
    allow_negative: bool = False

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("zone code is required")
        return key


class CountSheet(DomainModel):
    """One printed counting sheet: a (zone, pass) pair."""

    id: str
    campaign_id: str
    zone_id: str
    pass_no: SheetPass
    status: SheetStatus = SheetStatus.PENDING
    counter_name: str = ""
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    #: UC volume path of the scanned sheet, when one was uploaded.
    evidence_path: str | None = None
    #: Mean confidence reported by the extraction model, in [0, 1].
    extraction_confidence: float | None = None
    updated_at: dt.datetime | None = None


class CountSheetLine(DomainModel):
    """One article counted on a sheet, within a section.

    ``section`` decides how the quantity is consolidated: as-is for
    ``LINE_SIDE`` and ``WIP_OK``, exploded through the BOM for ``WIP``.
    """

    id: str
    sheet_id: str
    campaign_id: str
    item_number: str
    section: CountSection = CountSection.LINE_SIDE
    #: Pre-printed / imported value.
    qty_imported: Decimal | None = None
    #: Value typed by the encoder, or corrected after an AI extraction.
    qty_manual: Decimal | None = None
    unit: str = "PCE"
    source: DataSource = DataSource.MANUAL
    #: Per-line confidence when the value came from ``SCAN_AI``.
    confidence: float | None = None
    comment: str = ""
    display_order: int = 0

    @field_validator("item_number", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty_imported", "qty_manual", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return _as_qty(v)

    @property
    def qty(self) -> Decimal:
        return self.qty_manual if self.qty_manual is not None else (
            self.qty_imported if self.qty_imported is not None else ZERO
        )

    @property
    def is_counted(self) -> bool:
        """A blank cell is *not* a zero: it means the line was not counted."""
        return self.qty_manual is not None or self.qty_imported is not None

    @property
    def was_ai_corrected(self) -> bool:
        """The model read this line and a human then typed over it.

        ``confidence`` survives a manual edit — the extraction is kept beside
        the correction, never replaced by it — so the two together are the
        record that somebody reviewed the machine. That is what a second,
        multi-sheet scan must not silently undo.
        """
        return self.confidence is not None and self.qty_manual is not None


class ArbitrationLine(DomainModel):
    """A discrepancy between pass 1 and pass 2, and the quantity retained."""

    id: str
    campaign_id: str
    zone_id: str
    item_number: str
    section: CountSection
    qty_pass_1: Decimal | None = None
    qty_pass_2: Decimal | None = None
    qty_arbitrated: Decimal | None = None
    decided_by: str | None = None
    decided_at: dt.datetime | None = None
    comment: str = ""

    @field_validator("qty_pass_1", "qty_pass_2", "qty_arbitrated", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return _as_qty(v)

    @property
    def is_resolved(self) -> bool:
        """A human has *decided*, not merely been offered a value.

        Resolution is stamped by ``decided_at``, never by the presence of a
        quantity. Filling ``qty_arbitrated`` in bulk is a convenience — it saves
        typing the same figure forty times — and treating that convenience as a
        decision would post forty quantities nobody ever looked at.
        """
        return self.decided_at is not None

    @property
    def is_proposed(self) -> bool:
        """A quantity is on the table, waiting for someone to confirm or change it."""
        return self.qty_arbitrated is not None and self.decided_at is None

    @property
    def gap(self) -> Decimal:
        return (self.qty_pass_2 or ZERO) - (self.qty_pass_1 or ZERO)


class WipBreakdown(DomainModel):
    """Traceability of one exploded WIP assembly.

    Answers the specification's requirement to "see what the WIP is made of"
    instead of only its aggregated value.
    """

    parent_item: str
    parent_qty: Decimal
    child_item: str
    #: Cumulated quantity of *child* per one *parent*, across all BOM levels.
    qty_per_parent: Decimal
    child_qty: Decimal
    depth: int
    zone_code: str = ""


class ConsolidatedLine(DomainModel):
    """One line of the GENERIQUE consolidation, ready to post as an INVV journal."""

    campaign_id: str
    item_number: str
    qty: Decimal
    unit: str = "PCE"
    #: Split of the total by origin, for drill-down in the UI.
    qty_line_side: Decimal = ZERO
    qty_wip_ok: Decimal = ZERO
    qty_wip_exploded: Decimal = ZERO
    #: Zones that contributed, for the "who counted this" question.
    zone_codes: list[str] = Field(default_factory=list)

    @property
    def has_wip(self) -> bool:
        return self.qty_wip_exploded != 0


# --------------------------------------------------------------------------- #
# Adjustments & analysis
# --------------------------------------------------------------------------- #

class AdjustmentLine(DomainModel):
    """A stock movement recorded during the analysis phase.

    Covers both the movements generated by the counting journals and the manual
    adjustment journals posted afterwards — the ERP remains the master, the app
    mirrors the movements to keep the balance sheet live.
    """

    id: str
    campaign_id: str
    item_number: str
    warehouse_id: str = ""
    location_id: str = ""
    kind: AdjustmentKind = AdjustmentKind.ADJUSTMENT
    #: Signed quantity: positive = stock increase, negative = stock decrease.
    qty: Decimal = ZERO
    unit: str = "PCE"
    #: Signed value of the movement, as valued by the ERP.
    value: Decimal = ZERO
    journal_number: str = ""
    physical_date: dt.date | None = None
    reason_code: str = ""
    comment: str = ""
    source: DataSource = DataSource.FILE_IMPORT
    created_at: dt.datetime | None = None

    @field_validator("item_number", "warehouse_id", "location_id", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)


class VarianceLine(DomainModel):
    """A computed variance between book stock and counted stock.

    Produced by :mod:`inventory.domain.variance`; never stored as a source of
    truth, always recomputable from the frozen snapshot + counts + adjustments.
    """

    campaign_id: str
    item_number: str
    warehouse_id: str = ""
    location_id: str = ""
    item_type: ItemType = ItemType.UNKNOWN
    category: str = ""
    program: str = ""
    unit: str = "PCE"
    unit_cost: Decimal = ZERO

    book_qty: Decimal = ZERO
    counted_qty: Decimal = ZERO
    adjusted_qty: Decimal = ZERO

    #: True when the article/location appears in a count but not in the book
    #: stock, or vice-versa — the two cases that used to disappear silently.
    counted_only: bool = False
    book_only: bool = False

    @property
    def book_value(self) -> Decimal:
        return quantize_money(self.book_qty * self.unit_cost)

    @property
    def variance_qty(self) -> Decimal:
        """Counted minus book, before adjustments."""
        return quantize_qty(self.counted_qty - self.book_qty)

    @property
    def variance_value(self) -> Decimal:
        return quantize_money(self.variance_qty * self.unit_cost)

    @property
    def residual_qty(self) -> Decimal:
        """Variance still unexplained after the posted adjustments."""
        return quantize_qty(self.variance_qty - self.adjusted_qty)

    @property
    def residual_value(self) -> Decimal:
        return quantize_money(self.residual_qty * self.unit_cost)

    @property
    def final_qty(self) -> Decimal:
        """Stock after inventory = book + variance."""
        return quantize_qty(self.book_qty + self.variance_qty)


class ControlFinding(DomainModel):
    """One finding produced by the control engine."""

    code: str
    severity: ControlSeverity
    message: str
    #: Business coordinates of the offending object, for deep-linking.
    entity_type: str = ""
    entity_id: str = ""
    item_number: str = ""
    warehouse_id: str = ""
    location_id: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class AssignableCause(DomainModel):
    """A standard root cause, from the site's referential."""

    code: str
    label: str
    family: str = ""
    description: str = ""
    display_order: int = 0
    active: bool = True


class VarianceAnalysis(DomainModel):
    """Human analysis attached to an article's variance."""

    id: str
    campaign_id: str
    item_number: str
    cause_code: str | None = None
    comment: str = ""
    analyst: str | None = None
    #: Set when the analyst confirms the residual is understood and accepted.
    accepted: bool = False
    #: Optional AI proposal kept separate from the human decision.
    ai_suggested_cause: str | None = None
    ai_confidence: float | None = None
    ai_rationale: str = ""
    updated_at: dt.datetime | None = None


class AuditEvent(DomainModel):
    """One immutable entry of the audit trail."""

    id: str
    campaign_id: str | None
    at: dt.datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str = ""
    summary: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    request_id: str | None = None
