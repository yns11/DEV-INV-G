"""Use cases: they orchestrate the domain and the repositories.

A service method is the unit of business behaviour. It is where the phase guard
runs, where the transaction boundary lives, and where the audit event is
written — so no router and no repository has to remember any of that.
"""

from .analysis_service import AnalysisService
from .campaign_service import DEFAULT_THRESHOLDS, CampaignService
from .context import ENGINE_VERSION, ServiceContext, utcnow
from .counting_service import CountingService
from .generic_service import GenericService
from .import_service import ImportOutcome, ImportService
from .report_service import ReportService

__all__ = [
    "AnalysisService",
    "CampaignService",
    "CountingService",
    "DEFAULT_THRESHOLDS",
    "ENGINE_VERSION",
    "GenericService",
    "ImportOutcome",
    "ImportService",
    "ReportService",
    "ServiceContext",
    "utcnow",
]
