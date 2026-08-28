"""Lakebase (PostgreSQL) persistence layer.

Only :mod:`inventory.services` should import from here. The domain package must
never import this package — that dependency direction is what keeps the business
rules testable without a database.
"""

from .engine import Database, get_database, reset_database
from .repositories import (
    AdjustmentRepository,
    AnalysisRepository,
    AuditRepository,
    BackflushRepository,
    BookStockRepository,
    CampaignRepository,
    ConsolidationRepository,
    ErpJournalRepository,
    EvidenceBlobRepository,
    ImportBatchRepository,
    JournalRepository,
    ReferentialRepository,
    ScanJobRepository,
    SheetRepository,
    StockFlowRepository,
    new_id,
)

__all__ = [
    "Database",
    "get_database",
    "reset_database",
    "new_id",
    "AdjustmentRepository",
    "AnalysisRepository",
    "AuditRepository",
    "BookStockRepository",
    "CampaignRepository",
    "ConsolidationRepository",
    "EvidenceBlobRepository",
    "ImportBatchRepository",
    "ScanJobRepository",
    "ErpJournalRepository",
    "JournalRepository",
    "BackflushRepository",
    "ReferentialRepository",
    "SheetRepository",
    "StockFlowRepository",
]
