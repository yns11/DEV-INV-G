"""Repositories — the only place that knows SQL.

Each repository maps one aggregate between Lakebase rows and domain models.
Services above call them; nothing below them exists. Three rules are enforced
consistently across all of them:

* **Bulk over loops.** Imports use ``COPY`` or multi-row ``INSERT … ON CONFLICT``
  so loading a 100 000-line ERP export is one round trip, not 100 000.
* **Logical deletes.** ``deleted_at`` is set; rows never disappear, so the audit
  trail always resolves and a mistaken deletion is one UPDATE away from undone.
* **Optimistic concurrency.** Mutating writes check ``row_version``; two people
  editing the same journal line get a 409, not a silent last-write-wins.

Un module par agrégat
---------------------
C'était un seul fichier de près de trois mille lignes, où quatorze dépôts se
suivaient sans se connaître. Rien n'y était faux ; deux choses y étaient
pénibles. Ouvrir « les dépôts » pour corriger une requête de comptage obligeait
à traverser le référentiel, l'audit et la réconciliation — et deux personnes
travaillant sur deux agrégats différents se retrouvaient dans le même fichier à
chaque fusion.

Le découpage suit les agrégats, qui étaient déjà les sections du fichier. Rien
d'autre n'a changé : ce paquet réexporte exactement ce que le module exportait,
sous les mêmes noms, si bien qu'aucun appelant n'a été touché.
"""

from ._base import new_id
from .analysis import AdjustmentRepository, AnalysisRepository
from .audit import AuditRepository, ImportBatchRepository
from .backflush import BackflushRepository
from .book_stock import BookStockRepository
from .campaign import CampaignRepository
from .consolidation import ConsolidationRepository
from .erp_journal import (
    EarlyCountBatchRepository,
    EarlyCountDriftRepository,
    ErpJournalRepository,
)
from .evidence import EvidenceBlobRepository
from .journal import JournalRepository
from .operations import OperationsRepository
from .referential import ReferentialRepository
from .scan_job import ScanJobRepository
from .sheet import SheetRepository
from .stock_flow import StockFlowRepository

__all__ = [
    "new_id",
    "CampaignRepository",
    "ReferentialRepository",
    "BookStockRepository",
    "JournalRepository",
    "ErpJournalRepository",
    "EarlyCountBatchRepository",
    "EarlyCountDriftRepository",
    "SheetRepository",
    "ConsolidationRepository",
    "EvidenceBlobRepository",
    "AdjustmentRepository",
    "AnalysisRepository",
    "BackflushRepository",
    "StockFlowRepository",
    "AuditRepository",
    "ImportBatchRepository",
    "ScanJobRepository",
    "OperationsRepository",
]
