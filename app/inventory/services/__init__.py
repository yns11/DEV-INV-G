"""Use cases: they orchestrate the domain and the repositories.

A service method is the unit of business behaviour. It is where the phase guard
runs, where the transaction boundary lives, and where the audit event is
written — so no router and no repository has to remember any of that.
"""

from .analysis_service import AnalysisService
from .assistant_service import AssistantService
from .campaign_service import DEFAULT_THRESHOLDS, CampaignService
from .consolidation_service import ConsolidationService
from .context import ENGINE_VERSION, ServiceContext, utcnow
from .counting_service import CountingService
from .evidence_service import ArchivedEvidence, EvidenceService
from .generic_service import GenericService
from .import_service import ImportOutcome, ImportService
from .manager_service import ManagerService, Perimeter
from .referential_service import BookStockView, LocationView, ReferentialService
from .report_service import ReportService
from .scan_jobs import (
    ScanJobService,
    abandon_orphan_jobs,
    shutdown_workers,
)
from .scan_service import ScanService
from .stock_flow_service import StockFlowService

__all__ = [
    "AnalysisService",
    "AssistantService",
    "CampaignService",
    "ArchivedEvidence",
    "BookStockView",
    "CountingService",
    "DEFAULT_THRESHOLDS",
    "EvidenceService",
    "LocationView",
    "ENGINE_VERSION",
    "ConsolidationService",
    "GenericService",
    "ScanService",
    "ImportOutcome",
    "ImportService",
    "ManagerService",
    "Perimeter",
    "ReferentialService",
    "ReportService",
    "ScanJobService",
    "abandon_orphan_jobs",
    "shutdown_workers",
    "ServiceContext",
    "StockFlowService",
    "utcnow",
]
