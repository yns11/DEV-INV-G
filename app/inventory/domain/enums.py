"""Controlled vocabularies of the inventory domain.

Every enum value is a stable string: it is what gets persisted in Lakebase,
published to Delta and rendered in the UI. Renaming a value is a migration,
never a refactor.

Vocabulary note — the legacy Excel tool used the MOM (Manufacturing Operations
Management) wording ``MOM waiting`` / ``Eclaté``. The specification requires
those to be surfaced as **WIP** (work in progress). The enum members therefore
carry the new business wording; :func:`legacy_section_alias` maps the historical
labels found in old files onto them so that archived workbooks stay importable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

__all__ = [
    "CampaignStatus",
    "ItemType",
    "ItemCommonality",
    "ExclusionScope",
    "LocationType",
    "LocationStatus",
    "JournalKind",
    "JournalStatus",
    "SheetPass",
    "SheetStatus",
    "ZoneStatus",
    "CountSection",
    "DataSource",
    "AdjustmentKind",
    "FlowKind",
    "ControlSeverity",
    "AuditAction",
    "legacy_section_alias",
]


class CampaignStatus(StrEnum):
    """Lifecycle of a campaign — PREPARATION → COUNTING → ANALYSIS → CLOSED."""

    PREPARATION = "PREPARATION"
    COUNTING = "COUNTING"
    ANALYSIS = "ANALYSIS"
    CLOSED = "CLOSED"


class ItemType(StrEnum):
    """Nature of an article. Thresholds are configured per type."""

    COMPONENT = "COMPONENT"          # composant / BOP (bought-out part)
    SEMI_FINISHED = "SEMI_FINISHED"  # semi-fini
    FINISHED = "FINISHED"            # produit fini
    PACKAGING = "PACKAGING"          # emballage / conditionnement
    UNKNOWN = "UNKNOWN"              # present in a count but absent from the referential


class ItemCommonality(StrEnum):
    """Whether an article is dedicated to one programme or shared."""

    SPECIFIC = "SPECIFIC"
    COMMON = "COMMON"
    UNKNOWN = "UNKNOWN"


class ExclusionScope(StrEnum):
    """Three-level exclusion required by the specification.

    ``NONE``     the article participates everywhere;
    ``GENERIC``  excluded from the GENERIQUE consolidation and its analysis only;
    ``BOM``      ignored when exploding a parent's bill of materials;
    ``ALL``      excluded from every count and every analysis.

    ``GENERIC`` and ``BOM`` are independent facets, so they are stored as a set
    of scopes rather than a single value (see :class:`~inventory.domain.models.Item`).
    """

    NONE = "NONE"
    GENERIC = "GENERIC"
    BOM = "BOM"
    ALL = "ALL"

    @classmethod
    def normalise(cls, values: Any) -> set[ExclusionScope]:
        """The set an article really carries, from whatever was asked for.

        ``NONE`` is the absence of an exclusion rather than a fourth one, so it
        drops out; and ``ALL`` already means both facets, so it replaces them
        instead of sitting beside them. Without that last rule the same
        intention could be stored three ways — ``{ALL}``, ``{ALL, GENERIC}``,
        ``{ALL, GENERIC, BOM}`` — and a screen listing the raw set would say
        three different things about three identical articles.
        """
        if values is None:
            return set()
        if isinstance(values, (str, ExclusionScope)):
            values = [values]
        out = {cls(str(v).upper()) for v in values}
        out.discard(cls.NONE)
        return {cls.ALL} if cls.ALL in out else out


class LocationType(StrEnum):
    """How a location is counted."""

    LABEL = "LABEL"   # entrepôt à étiquettes → journal INVE généré par scan
    BULK = "BULK"     # entrepôt VRAC        → journal INVV saisi/consolidé
    UNKNOWN = "UNKNOWN"


class LocationStatus(StrEnum):
    """Active locations are in scope; disabled ones are fully ignored.

    A disabled location contributes neither quantity nor value to any KPI, and
    no journal is created for it.
    """

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class JournalKind(StrEnum):
    """ERP counting-journal name (``JournalNameId`` in the OData export)."""

    INVE = "INVE"  # inventaire par étiquette (scan)
    INVV = "INVV"  # inventaire vrac (saisie / consolidation)


class JournalStatus(StrEnum):
    """Progress of one counting journal (one per active warehouse+location).

    ``BOOK_ENFORCED`` covers locations inventoried separately *before* the book
    stock snapshot: the user forces the counted quantity to equal the book
    quantity, which closes the journal with a null variance by construction.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    POSTED = "POSTED"
    BOOK_ENFORCED = "BOOK_ENFORCED"


class SheetPass(StrEnum):
    """Which of the two independent counts a sheet materialises."""

    PASS_1 = "PASS_1"
    PASS_2 = "PASS_2"


class SheetStatus(StrEnum):
    """Life of a single printed counting sheet.

    PENDING → COUNTING (sheet handed to the counter) → ENCODING (sheet returned,
    scanned or being typed in) → DONE (encoding validated; reversible).
    """

    PENDING = "PENDING"
    COUNTING = "COUNTING"
    ENCODING = "ENCODING"
    DONE = "DONE"


class ZoneStatus(StrEnum):
    """Aggregated state of a physical zone of the GENERIQUE location."""

    PENDING = "PENDING"
    PASS_1_RUNNING = "PASS_1_RUNNING"
    PASS_2_RUNNING = "PASS_2_RUNNING"
    ARBITRATION = "ARBITRATION"
    DONE = "DONE"


class CountSection(StrEnum):
    """Section of a GENERIQUE counting sheet — drives the consolidation rule.

    * ``LINE_SIDE``  — "Composants en bord de ligne" (BDL): counted as-is.
    * ``WIP``        — legacy "Statut MOM: Waiting for decision / on progress":
      the assembly is *not* a valid stock unit yet, so it is exploded into its
      bill of materials.
    * ``WIP_OK``     — legacy "Statut MOM: OK": the assembly is declared in the
      ERP and counted as one assembled unit.
    """

    LINE_SIDE = "LINE_SIDE"
    WIP = "WIP"
    WIP_OK = "WIP_OK"


class DataSource(StrEnum):
    """Provenance of a quantity — always kept next to the value it produced."""

    ERP_IMPORT = "ERP_IMPORT"        # OData / Excel export of the counting journals
    FILE_IMPORT = "FILE_IMPORT"      # user-uploaded csv/xlsx matching a grid contract
    MANUAL = "MANUAL"                # typed or pasted in the app
    SCAN_AI = "SCAN_AI"              # extracted from a scanned sheet by the LLM
    CONSOLIDATION = "CONSOLIDATION"  # produced by the GENERIQUE consolidation engine
    ARBITRATION = "ARBITRATION"      # decided by a human during pass-1/pass-2 arbitration
    SYSTEM = "SYSTEM"                # derived by the application (e.g. book enforcement)


class AdjustmentKind(StrEnum):
    """Nature of a stock movement fed into the analysis phase."""

    COUNT = "COUNT"              # mouvement généré par un journal de comptage
    ADJUSTMENT = "ADJUSTMENT"    # journal d'ajustement saisi après analyse
    RECOUNT = "RECOUNT"          # recomptage post-inventaire
    OTHER = "OTHER"


class FlowKind(StrEnum):
    """A quantity the user loads to explain the period between two campaigns.

    Only the three the application cannot derive on its own. Production and
    theoretical consumption are read from the backflush fact table instead —
    asking somebody to retype what a warehouse already knows is how the legacy
    process introduced most of its errors.

    The sign lives here, not in the quantity: a shipment is stored positive and
    subtracted by the calculation. Letting it be typed negative would mean an
    expedition entered the wrong way round is added to the stock, and nothing on
    screen would show it.
    """

    RECEIPT = "RECEIPT"      # réceptions : entrées en stock sur la période
    SHIPMENT = "SHIPMENT"    # expéditions : sorties vers le client
    SCRAP = "SCRAP"          # rebuts : étape facultative


class ControlSeverity(StrEnum):
    """Severity of a control finding. ``BLOCKER`` prevents phase transitions."""

    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    INFO = "INFO"


class AuditAction(StrEnum):
    """Verbs recorded in the immutable audit trail."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    STATUS_CHANGE = "STATUS_CHANGE"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    FREEZE = "FREEZE"
    CONSOLIDATE = "CONSOLIDATE"
    ARBITRATE = "ARBITRATE"
    LOGIN = "LOGIN"


# --------------------------------------------------------------------------- #
# Legacy vocabulary bridge
# --------------------------------------------------------------------------- #

#: Historical section labels found in ``Compil GENERIQUE.xlsx`` and its Power
#: Query steps, mapped onto the current vocabulary. Keys are compared after
#: upper-casing and squeezing whitespace.
_LEGACY_SECTIONS: dict[str, CountSection] = {
    "BDL": CountSection.LINE_SIDE,
    "COMPOSANTS EN BORD DE LIGNE": CountSection.LINE_SIDE,
    "BORD DE LIGNE": CountSection.LINE_SIDE,
    "LINE_SIDE": CountSection.LINE_SIDE,
    "MOM_WAITING": CountSection.WIP,
    "MOM WAITING": CountSection.WIP,
    "STATUT MOM: WAITING FOR DECISION / ON PROGRESS": CountSection.WIP,
    "ECLATE": CountSection.WIP,
    "ECLATEE": CountSection.WIP,
    "WIP": CountSection.WIP,
    "MOM_OK": CountSection.WIP_OK,
    "MOM OK": CountSection.WIP_OK,
    "STATUT MOM: OK": CountSection.WIP_OK,
    "WIP_OK": CountSection.WIP_OK,
}


def legacy_section_alias(label: str | None) -> CountSection | None:
    """Resolve a historical section label onto a :class:`CountSection`.

    Returns ``None`` when the label is unknown, so callers can raise a precise
    validation error instead of silently mis-classifying a counted quantity —
    the exact failure mode that made the Excel tool untrustworthy.

    >>> legacy_section_alias("MOM waiting")
    <CountSection.WIP: 'WIP'>
    >>> legacy_section_alias("bdl")
    <CountSection.LINE_SIDE: 'LINE_SIDE'>
    >>> legacy_section_alias("???") is None
    True
    """
    if not label:
        return None
    key = " ".join(label.strip().upper().split())
    return _LEGACY_SECTIONS.get(key)
