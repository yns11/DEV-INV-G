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
    DriftResolution,
    ExclusionScope,
    ItemType,
    JournalStatus,
    LocationStatus,
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
    "ItemPatch",
    "ItemExclusionsRequest",
    "BomLinkPatch",
    "BomLinkKey",
    "BomActivationRequest",
    "TableExportRequest",
    "ImportRequest",
    "PasteRequest",
    "RowsRequest",
    "LocationStatusRequest",
    "JournalStatusRequest",
    "JournalLineRequest",
    "JournalScopeRequest",
    "EarlyBatchRequest",
    "UnsealRequest",
    "DriftResolutionRequest",
    "ZoneRequest",
    "ZonePassesRequest",
    "ZoneNegativeRequest",
    "ZoneAssignmentRequest",
    "ManagerRow",
    "ManagerRowsRequest",
    "WarehouseAssignment",
    "WarehouseAssignmentRequest",
    "ZoneClosureRequest",
    "SheetLinesRequest",
    "ZoneDeleteRequest",
    "SheetLineDeleteRequest",
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
    #: Deux au maximum : au-delà, la campagne annonçait un comptage que rien
    #: ne savait produire. Voir ``CampaignConfig.generic_passes``.
    generic_passes: int = Field(default=2, ge=1, le=2, alias="genericPasses")
    arbitration_tolerance: Decimal = Field(
        default=Decimal("0"), ge=0, le=1, alias="arbitrationTolerance"
    )
    max_bom_depth: int = Field(default=10, ge=1, le=25, alias="maxBomDepth")
    currency: str = "EUR"


class ThresholdPayload(ApiModel):
    item_type: ItemType = Field(alias="itemType")
    value_abs_eur: Decimal = Field(default=Decimal("1000"), ge=0, alias="valueAbsEur")
    qty_relative: Decimal | None = Field(default=None, ge=0, alias="qtyRelative")


class ItemPatch(ApiModel):
    """One article, edited in place.

    Every field is optional and ``None`` means "leave it alone": the grid sends
    what the user changed, not a whole row rebuilt from what happened to be on
    screen. The business key is not here — an article number is the identity of
    the line, and renaming it would be a different article.
    """

    name: str | None = None
    item_type: ItemType | None = Field(default=None, alias="itemType")
    category: str | None = None
    program: str | None = None
    unit: str | None = None
    std_price: Decimal | None = Field(default=None, ge=0, alias="stdPrice")
    #: Typed rather than free strings: an unknown scope has to be refused at the
    #: door, because stored it would only fail later, when the referential is
    #: read back and every screen breaks at once.
    exclusions: list[ExclusionScope] | None = None


class ItemExclusionsRequest(ApiModel):
    """The exclusion of a whole selection, in one decision.

    Setting it article by article is what people actually do — a family, a
    programme, a supplier's whole range — so the batch is the operation, not a
    convenience wrapper around the single edit. An empty ``exclusions`` puts the
    selection back fully in scope, which is the same gesture in reverse and must
    cost exactly as little.
    """

    item_numbers: list[str] = Field(min_length=1, max_length=50_000, alias="itemNumbers")
    exclusions: list[ExclusionScope] = Field(default_factory=list)


class BomLinkPatch(ApiModel):
    """One bill-of-materials edge, edited in place.

    Parent and child identify the edge, so they are required and never changed;
    changing either is deleting one link and creating another, which the grid
    already offers.
    """

    parent_item: str = Field(alias="parentItem", min_length=1)
    child_item: str = Field(alias="childItem", min_length=1)
    qty_per: Decimal | None = Field(default=None, gt=0, alias="qtyPer")
    unit: str | None = None
    #: Whether this version is in force. Only those are exploded.
    active: bool | None = None


class BomLinkKey(ApiModel):
    parent_item: str = Field(alias="parentItem", min_length=1)
    child_item: str = Field(alias="childItem", min_length=1)


class BomActivationRequest(ApiModel):
    """Put a batch of edges in force, or retire them."""

    links: list[BomLinkKey] = Field(min_length=1, max_length=50_000)
    active: bool


class TableColumn(ApiModel):
    key: str = Field(min_length=1)
    label: str = ""


class TableExportRequest(ApiModel):
    """A grid's visible rows, on their way to a spreadsheet.

    The rows come *from the client* rather than being recomputed server-side,
    and that is the point: what gets exported is what is on screen — the
    filtering, the sorting and the selection the user actually made. Rebuilding
    it from the query parameters would work for the two or three grids backed by
    a single endpoint and quietly lie for all the others.
    """

    title: str = Field(default="Export", max_length=120)
    columns: list[TableColumn] = Field(min_length=1, max_length=80)
    rows: list[dict[str, Any]] = Field(max_length=50_000)


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


class CampaignSettingsRequest(ApiModel):
    """Les réglages de campagne autres que les seuils.

    Un seul aujourd'hui. Une requête dédiée plutôt qu'un champ ajouté à la
    configuration complète : celle-ci porte l'emplacement générique, le nombre
    de comptages et la devise, qui sont gelés bien plus tôt, et les faire
    voyager ensemble obligerait l'écran à renvoyer des valeurs qu'il n'a pas le
    droit de changer.
    """

    allow_formulas: bool = Field(alias="allowFormulas")


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
    #: Autoriser l'écriture d'un ensemble amputé.
    #:
    #: Les grilles qui **remplacent** ce qui existe — le stock ERP, l'écart
    #: backflush, une nomenclature en mode remplacement — refusent d'écrire dès
    #: qu'une ligne est rejetée : les lignes manquantes deviendraient des lignes
    #: supprimées, et rien ne dirait lesquelles. Ce drapeau lève le refus, se
    #: voit dans le rapport du lot, et n'est jamais le défaut.
    allow_partial: bool = Field(default=False, alias="allowPartial")


class RowsRequest(ApiModel):
    """Rows typed or edited directly in a grid."""

    rows: list[dict[str, Any]]
    dry_run: bool = Field(default=False, alias="dryRun")
    replace: bool = False
    #: Autoriser l'écriture d'un ensemble amputé.
    #:
    #: Les grilles qui **remplacent** ce qui existe — le stock ERP, l'écart
    #: backflush, une nomenclature en mode remplacement — refusent d'écrire dès
    #: qu'une ligne est rejetée : les lignes manquantes deviendraient des lignes
    #: supprimées, et rien ne dirait lesquelles. Ce drapeau lève le refus, se
    #: voit dans le rapport du lot, et n'est jamais le défaut.
    allow_partial: bool = Field(default=False, alias="allowPartial")


class StockFlowRunRequest(ApiModel):
    """Which earlier campaign to compare against.

    The period is not part of the request: it *is* the two count dates. Letting
    it be typed would allow a period that does not match the inventories it
    claims to bracket.
    """

    baseline_campaign_id: str = Field(alias="baselineCampaignId", min_length=1)


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


class JournalScopeRequest(ApiModel):
    """Les emplacements qu'un journal ERP couvre réellement.

    Ils ne se déduisent pas de ses lignes : certaines ne portent un autre
    emplacement que pour matérialiser un déplacement. D'où une déclaration, et
    non un calcul.
    """

    locations: list[LocationKeyPayload] = Field(min_length=1)


class EarlyBatchRequest(ApiModel):
    code: str = Field(min_length=3, max_length=50)
    label: str = ""
    counted_on: dt.date | None = Field(default=None, alias="countedOn")
    erp_journal_ids: list[str] = Field(min_length=1, alias="erpJournalIds")


class UnsealRequest(ApiModel):
    """Le descellement annule une preuve datée : il se motive."""

    reason: str = Field(min_length=1)


class DriftResolutionRequest(ApiModel):
    drift_ids: list[str] = Field(min_length=1, alias="driftIds")
    resolution: DriftResolution
    #: Obligatoire pour ``KEEP_EARLY`` — le service le vérifie, parce que c'est
    #: une règle métier et non une contrainte de forme : cette issue laisse la
    #: campagne et l'ERP en désaccord, et il faut dire pourquoi.
    cause_code: str = Field(default="", alias="causeCode")
    comment: str = ""


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
    passes: int | None = Field(default=None, ge=1, le=2)
    #: A zone created here carries no pre-printed article list, which is what a
    #: free-entry sheet *is*. Overridable for the caller who intends to load one
    #: right after.
    free_entry: bool = Field(default=True, alias="freeEntry")
    manager_code: str = Field(default="", alias="managerCode")


class ZonePassesRequest(ApiModel):
    """Bulk change of how many independent counts a selection of zones needs."""

    zone_ids: list[str] = Field(min_length=1, alias="zoneIds")
    passes: int = Field(ge=1, le=2)


class ZoneNegativeRequest(ApiModel):
    """Allow — or forbid again — negative counted quantities on a selection."""

    zone_ids: list[str] = Field(min_length=1, alias="zoneIds")
    allowed: bool


class ZoneAssignmentRequest(ApiModel):
    """Attach zones to a manager; an empty code detaches them."""

    zone_ids: list[str] = Field(min_length=1, alias="zoneIds")
    manager_code: str = Field(default="", alias="managerCode")


class ManagerRow(ApiModel):
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(default="", max_length=120)
    #: Identity forwarded by the authentication proxy — usually an e-mail.
    actor: str = Field(default="", max_length=200)
    active: bool = True
    display_order: int = Field(default=0, alias="displayOrder")


class ManagerRowsRequest(ApiModel):
    managers: list[ManagerRow] = Field(min_length=1)


class WarehouseAssignment(ApiModel):
    warehouse_id: str = Field(min_length=1, max_length=60, alias="warehouseId")
    #: Empty clears the assignment and lets the ``AUTRES`` catch-all apply.
    manager_code: str = Field(default="", alias="managerCode")


class WarehouseAssignmentRequest(ApiModel):
    assignments: list[WarehouseAssignment] = Field(min_length=1)


class ZoneClosureRequest(ApiModel):
    """Déclarer une zone terminée, ou la rouvrir. La seule décision d'état
    qui reste au parcours de comptage."""

    closed: bool = True


class SheetLineRow(ApiModel):
    id: str | None = None
    item_number: str = Field(alias="itemNumber")
    section: CountSection = CountSection.LINE_SIDE
    #: Un nombre, ou l'opération que le compteur a écrite (« 3*48+7 »).
    #:
    #: Le type accepte le texte pour que l'expression **arrive jusqu'au
    #: service**, seul endroit qui connaisse la campagne et donc le réglage
    #: « Accepter des formules dans les comptages ». Déclarée ``Decimal`` seule,
    #: la validation Pydantic refusait l'opération ici, avec un message de
    #: contrat — « input should be a valid decimal » — qui ne dit rien à qui
    #: recopie une feuille et ne mentionne aucun réglage.
    #:
    #: La conversion reste obligatoire, elle a seulement lieu un cran plus loin :
    #: voir :func:`inventory.domain.formula.resolve_quantity`.
    qty: Decimal | str | None = None
    unit: str = "PCE"
    comment: str = ""
    display_order: int | None = Field(default=None, alias="displayOrder")


class SheetLinesRequest(ApiModel):
    lines: list[SheetLineRow]
    replace: bool = False
    #: La version de la feuille que l'écran avait sous les yeux.
    #:
    #: Facultative parce que tous les appelants ne l'ont pas — un collage depuis
    #: Excel n'ajoute que des lignes, une extraction IA écrit une feuille qu'elle
    #: vient de lire. L'écran de saisie, lui, la transmet toujours : c'est là que
    #: deux personnes se retrouvent sur la même feuille.
    expected_version: int | None = Field(default=None, alias="expectedVersion")


class SheetLineDeleteRequest(ApiModel):
    """A selection of counting-sheet lines to remove."""

    line_ids: list[str] = Field(min_length=1, max_length=20_000, alias="lineIds")


class ZoneDeleteRequest(ApiModel):
    """Les zones à retirer, avec leurs feuilles — une ou tout un lot."""

    zone_ids: list[str] = Field(min_length=1, max_length=5_000, alias="zoneIds")


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
