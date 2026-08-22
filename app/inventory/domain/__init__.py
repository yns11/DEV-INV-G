"""Pure business logic of the inventory campaign domain.

Nothing in this package imports a driver, a client or a framework: every module
is a function of its inputs. That is what makes the rules testable in
milliseconds and reproducible years later from a frozen campaign dossier.

Layers above this one (``inventory.db``, ``inventory.services``,
``inventory.api``) may import from here; the reverse is forbidden.
"""

from .bom import BomCycleError, BomIndex, ExplosionResult
from .consolidation import (
    ConsolidationInput,
    ConsolidationResult,
    ZoneCounts,
    build_arbitration_lines,
    consolidate_generic,
    resolve_zone_quantities,
)
from .controls import run_all_controls, summarise
from .enums import (
    AdjustmentKind,
    AuditAction,
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
    ZoneStatus,
    legacy_section_alias,
)
from .models import (
    AdjustmentLine,
    ArbitrationLine,
    AssignableCause,
    AuditEvent,
    BomLink,
    BookStockLine,
    Campaign,
    CampaignConfig,
    ConsolidatedLine,
    ControlFinding,
    CountJournal,
    CountJournalLine,
    CountSheet,
    CountSheetLine,
    Item,
    Location,
    LocationKey,
    Manager,
    Thresholds,
    VarianceAnalysis,
    VarianceLine,
    Warehouse,
    WipBreakdown,
    Zone,
    normalise_key,
)
from .printing import (
    BLANK_ROWS_PER_SECTION,
    PrintMode,
    available_print_modes,
    print_refusal,
)
from .quantities import quantize_money, quantize_qty, safe_ratio, to_decimal
from .variance import (
    CountedQty,
    KpiBlock,
    VarianceSet,
    aggregate_by,
    build_variances,
    compute_kpis,
    is_material,
    pareto,
)
from .workflow import (
    CAMPAIGN_TRANSITIONS,
    Editable,
    arbitration_required,
    assert_campaign_transition,
    campaign_transition_blockers,
    derive_zone_status,
    mutability_of,
    passes_for,
    zone_closure_blockers,
)

__all__ = [
    # enums
    "AdjustmentKind", "AuditAction", "CampaignStatus", "ControlSeverity",
    "CountSection", "DataSource", "ExclusionScope", "ItemCommonality", "ItemType",
    "JournalKind", "JournalStatus", "LocationStatus", "LocationType", "SheetPass",
    "ZoneStatus", "legacy_section_alias",
    # models
    "AdjustmentLine", "ArbitrationLine", "AssignableCause", "AuditEvent", "BomLink",
    "BookStockLine", "Campaign", "CampaignConfig", "ConsolidatedLine",
    "ControlFinding", "CountJournal", "CountJournalLine", "CountSheet",
    "CountSheetLine", "Item", "Location", "LocationKey", "Manager", "Thresholds",
    "VarianceAnalysis", "VarianceLine", "Warehouse", "WipBreakdown", "Zone",
    "normalise_key",
    # printing
    "BLANK_ROWS_PER_SECTION", "PrintMode", "available_print_modes", "print_refusal",
    # quantities
    "quantize_money", "quantize_qty", "safe_ratio", "to_decimal",
    # bom
    "BomCycleError", "BomIndex", "ExplosionResult",
    # consolidation
    "ConsolidationInput", "ConsolidationResult", "ZoneCounts",
    "build_arbitration_lines", "consolidate_generic", "resolve_zone_quantities",
    # variance
    "CountedQty", "KpiBlock", "VarianceSet", "aggregate_by", "build_variances",
    "compute_kpis", "is_material", "pareto",
    # controls
    "run_all_controls", "summarise",
    # workflow
    "CAMPAIGN_TRANSITIONS", "Editable", "arbitration_required",
    "assert_campaign_transition",
    "campaign_transition_blockers", "derive_zone_status", "mutability_of",
    "zone_closure_blockers",
    "passes_for",
]
