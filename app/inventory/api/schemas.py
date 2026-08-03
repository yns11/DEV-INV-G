"""Request and response payloads for the HTTP API.

Separate from the domain models on purpose: the wire format is allowed to change
(camelCase for JavaScript, flattened shapes, extra display fields) without
touching the business objects, and a client can never inject a field the domain
did not intend to expose.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import (
    CampaignStatus,
    CountSection,
    ItemType,
    JournalStatus,
    LocationStatus,
    SheetStatus,
)

__all__ = [
    "ApiModel",
    "ErrorPayload",
    "CreateCampaignRequest",
    "CloneCampaignRequest",
    "TransitionRequest",
    "ThresholdPayload",
    "UpdateThresholdsRequest",
    "CampaignConfigPayload",
    "ImportRequest",
    "PasteRequest",
    "RowsRequest",
    "LocationStatusRequest",
    "JournalStatusRequest",
    "JournalLineRequest",
    "ZoneRequest",
    "SheetTransitionRequest",
    "SheetLinesRequest",
    "ArbitrationDecisionRequest",
    "ReclassifyRequest",
    "AnalysisRequest",
    "AdjustmentRowRequest",
]


class ApiModel(BaseModel):
    """Base for every payload: strict, and tolerant of camelCase input."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class ErrorPayload(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Campaign
# --------------------------------------------------------------------------- #

class CampaignConfigPayload(ApiModel):
    generic_warehouse: str = Field(default="B06VRAC", alias="genericWarehouse")
    generic_location: str = Field(default="GENERIQUE", alias="genericLocation")
    generic_passes: int = Field(default=2, ge=1, le=3, alias="genericPasses")
    arbitration_tolerance: Decimal = Field(
        default=Decimal("0"), ge=0, le=1, alias="arbitrationTolerance"
    )
    max_bom_depth: int = Field(default=10, ge=1, le=25, alias="maxBomDepth")
    currency: str = "EUR"


class ThresholdPayload(ApiModel):
    item_type: ItemType = Field(alias="itemType")
    value_abs_eur: Decimal = Field(default=Decimal("1000"), ge=0, alias="valueAbsEur")
    qty_relative: Decimal | None = Field(default=None, ge=0, alias="qtyRelative")
    qty_abs_floor: Decimal = Field(default=Decimal("0"), ge=0, alias="qtyAbsFloor")
    ira_tolerance: Decimal = Field(default=Decimal("0"), ge=0, alias="iraTolerance")


class CreateCampaignRequest(ApiModel):
    code: str = Field(min_length=3, max_length=50)
    label: str = Field(default="", max_length=200)
    count_date: dt.date = Field(alias="countDate")
    config: CampaignConfigPayload | None = None
    thresholds: list[ThresholdPayload] | None = None


class CloneCampaignRequest(ApiModel):
    source_campaign_id: str = Field(alias="sourceCampaignId")
    code: str = Field(min_length=3, max_length=50)
    label: str = Field(default="", max_length=200)
    count_date: dt.date = Field(alias="countDate")
    include_zones: bool = Field(default=True, alias="includeZones")
    include_sheet_lines: bool = Field(default=True, alias="includeSheetLines")


class TransitionRequest(ApiModel):
    target: CampaignStatus


class UpdateThresholdsRequest(ApiModel):
    thresholds: list[ThresholdPayload]


# --------------------------------------------------------------------------- #
# Imports
# --------------------------------------------------------------------------- #

class ImportRequest(ApiModel):
    """Options accompanying a multipart file upload."""

    sheet: str | None = None
    replace: bool = False
    dry_run: bool = Field(default=False, alias="dryRun")


class PasteRequest(ApiModel):
    """A clipboard block pasted into a grid."""

    text: str = Field(min_length=1)
    dry_run: bool = Field(default=False, alias="dryRun")
    replace: bool = False


class RowsRequest(ApiModel):
    """Rows typed or edited directly in a grid."""

    rows: list[dict[str, Any]]
    dry_run: bool = Field(default=False, alias="dryRun")
    replace: bool = False


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #

class LocationKeyPayload(ApiModel):
    warehouse_id: str = Field(alias="warehouseId")
    location_id: str = Field(default="", alias="locationId")


class LocationStatusRequest(ApiModel):
    locations: list[LocationKeyPayload] = Field(min_length=1)
    status: LocationStatus


class JournalStatusRequest(ApiModel):
    journal_ids: list[str] = Field(min_length=1, alias="journalIds")
    status: JournalStatus


class JournalLineRequest(ApiModel):
    line_id: str | None = Field(default=None, alias="lineId")
    item_number: str = Field(min_length=1, alias="itemNumber")
    qty: Decimal | None = None
    unit: str = "PCE"
    comment: str = ""
    expected_version: int | None = Field(default=None, alias="expectedVersion")


# --------------------------------------------------------------------------- #
# GENERIQUE
# --------------------------------------------------------------------------- #

class ZoneRequest(ApiModel):
    code: str = Field(min_length=1, max_length=100)
    label: str = Field(default="", max_length=200)
    sector: str = Field(default="", max_length=120)
    display_order: int = Field(default=0, alias="displayOrder")


class SheetTransitionRequest(ApiModel):
    target: SheetStatus
    counter_name: str | None = Field(default=None, alias="counterName")


class SheetLineRow(ApiModel):
    id: str | None = None
    item_number: str = Field(alias="itemNumber")
    section: CountSection = CountSection.LINE_SIDE
    qty: Decimal | None = None
    unit: str = "PCE"
    comment: str = ""
    display_order: int | None = Field(default=None, alias="displayOrder")


class SheetLinesRequest(ApiModel):
    lines: list[SheetLineRow]
    replace: bool = False


class ArbitrationDecisionRequest(ApiModel):
    qty: Decimal = Field(ge=0)
    comment: str = ""


class ReclassifyRequest(ApiModel):
    """Move counting-sheet lines to another section.

    Used to resolve a WIP assembly that has no bill of materials: it is counted
    as itself instead of being exploded.
    """

    line_ids: list[str] = Field(min_length=1, alias="lineIds")
    section: CountSection = CountSection.WIP_OK


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #

class AnalysisRequest(ApiModel):
    item_number: str = Field(alias="itemNumber")
    cause_code: str | None = Field(default=None, alias="causeCode")
    comment: str = ""
    accepted: bool = False


class AdjustmentRowRequest(ApiModel):
    id: str | None = None
    item_number: str = Field(alias="itemNumber")
    warehouse_id: str = Field(default="", alias="warehouseId")
    location_id: str = Field(default="", alias="locationId")
    kind: Literal["COUNT", "ADJUSTMENT", "RECOUNT", "OTHER"] = "ADJUSTMENT"
    qty: Decimal = Decimal("0")
    unit: str = "PCE"
    value: Decimal = Decimal("0")
    journal_number: str = Field(default="", alias="journalNumber")
    physical_date: dt.date | None = Field(default=None, alias="physicalDate")
    reason_code: str = Field(default="", alias="reasonCode")
    comment: str = ""
