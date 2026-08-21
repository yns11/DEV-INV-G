"""Service context — the unit of work shared by every use case.

Bundles the repositories, the settings and the acting user so that a service
method never has to reach for a global. It also centralises two cross-cutting
concerns that are easy to forget one call at a time:

* **guard** — is this write allowed in the campaign's current phase?
* **audit** — record what happened, by whom, with the before/after payload.
"""

from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..db import (
    AdjustmentRepository,
    AnalysisRepository,
    AuditRepository,
    BackflushRepository,
    BookStockRepository,
    CampaignRepository,
    ConsolidationRepository,
    Database,
    ImportBatchRepository,
    JournalRepository,
    ReferentialRepository,
    SheetRepository,
    StockFlowRepository,
    get_database,
)
from ..domain.enums import AuditAction, CampaignStatus
from ..domain.models import Campaign
from ..domain.sequence import Progress, blocking_reason
from ..domain.workflow import Editable, mutability_of
from ..errors import FrozenError
from ..evidence import EvidenceStore

__all__ = ["ServiceContext", "utcnow"]

#: Version of the calculation engine, stamped on every derived artefact so a
#: figure can always be traced back to the code that produced it.
ENGINE_VERSION = "1.0.0"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# No ``slots=True``: the repositories below are ``functools.cached_property``,
# which needs an instance ``__dict__`` to memoise into.
@dataclass
class ServiceContext:
    """Everything a use case needs, built once per request."""

    actor: str
    db: Database = field(default_factory=get_database)
    settings: Settings = field(default_factory=get_settings)
    request_id: str | None = None
    #: Memoised by :meth:`progress`, keyed by campaign; see there.
    _progress: dict[str, Progress] = field(default_factory=dict, repr=False)

    # -- repositories (lazily built, cached per context) ----------------------

    @functools.cached_property
    def campaigns(self) -> CampaignRepository:
        return CampaignRepository(self.db)

    @functools.cached_property
    def referentials(self) -> ReferentialRepository:
        return ReferentialRepository(self.db)

    @functools.cached_property
    def book_stock(self) -> BookStockRepository:
        return BookStockRepository(self.db)

    @functools.cached_property
    def journals(self) -> JournalRepository:
        return JournalRepository(self.db)

    @functools.cached_property
    def sheets(self) -> SheetRepository:
        return SheetRepository(self.db)

    @functools.cached_property
    def consolidation(self) -> ConsolidationRepository:
        return ConsolidationRepository(self.db)

    @functools.cached_property
    def adjustments(self) -> AdjustmentRepository:
        return AdjustmentRepository(self.db)

    @functools.cached_property
    def backflush(self) -> BackflushRepository:
        return BackflushRepository(self.db)

    @functools.cached_property
    def stock_flow(self) -> StockFlowRepository:
        return StockFlowRepository(self.db)

    @functools.cached_property
    def analysis(self) -> AnalysisRepository:
        return AnalysisRepository(self.db)

    @functools.cached_property
    def audit(self) -> AuditRepository:
        return AuditRepository(self.db)

    @functools.cached_property
    def imports(self) -> ImportBatchRepository:
        return ImportBatchRepository(self.db)

    @functools.cached_property
    def evidence(self) -> EvidenceStore:
        """L'archive des pièces justificatives. Pas un dépôt : un volume UC."""
        return EvidenceStore(self.settings)

    # -- cross-cutting concerns ----------------------------------------------

    def guard(self, campaign: Campaign, aspect: str) -> None:
        """Refuse a write that this phase freezes, or that comes too early.

        Two different questions, checked at one place because a caller must not
        be able to satisfy one and forget the other.

        *Is it frozen?* — ``aspect`` is an attribute of
        :class:`~inventory.domain.workflow.Editable` (``items``, ``book_stock``,
        ``count_entries``, …), and the phase decides. This is what makes
        "geler" a guarantee rather than a convention.

        *Is it too early?* — within a phase the steps are still ordered, and
        :mod:`inventory.domain.sequence` holds that order. ``post_journal`` is a
        pseudo-aspect: nothing is frozen by it, but it must not happen before
        the ERP stock has stopped moving.
        """
        editable: Editable = mutability_of(campaign.status)
        if aspect != "post_journal":
            if not hasattr(editable, aspect):
                raise ValueError(f"unknown editable aspect {aspect!r}")
            if not getattr(editable, aspect):
                raise FrozenError(
                    f"« {_ASPECT_LABELS.get(aspect, aspect)} » est gelé au statut "
                    f"{_STATUS_LABELS[campaign.status]} de la campagne.",
                    aspect=aspect,
                    status=str(campaign.status),
                )

        reason = blocking_reason(aspect, self.progress(campaign))
        if reason:
            raise FrozenError(reason, aspect=aspect, status=str(campaign.status))

    def progress(self, campaign: Campaign) -> Progress:
        """What the campaign already holds, counted once per request.

        The guard runs before every write, so this is cached: the counts cannot
        change under a single request, and re-issuing four ``count(*)`` queries
        per line of an import would be a real cost for an answer that is already
        known.

        Keyed by campaign, not merely memoised. A request handles one campaign
        almost always — but "almost" is what makes a cache dangerous: cloning
        reads two, and a shared entry would answer the second with the first's
        counts, silently unlocking or blocking the wrong steps.
        """
        cached = self._progress.get(campaign.id)
        if cached is None:
            cached = Progress(
                items=self.referentials.count_items(campaign.id),
                zones=len(self.sheets.list_zones(campaign.id)),
                book_stock_lines=self.book_stock.count(campaign.id),
                book_stock_frozen=campaign.book_stock_frozen_at is not None,
            )
            self._progress[campaign.id] = cached
        return cached

    def forget_progress(self, campaign_id: str | None = None) -> None:
        """Drop the cached counts after a write that changes them.

        Called by the importers: loading the referential inside a request that
        then creates the sheets must not be judged on the counts taken before
        the load.
        """
        if campaign_id is None:
            self._progress.clear()
        else:
            self._progress.pop(campaign_id, None)

    def record(
        self,
        *,
        campaign_id: str | None,
        action: AuditAction | str,
        entity_type: str,
        entity_id: str = "",
        summary: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        conn: Any = None,
    ) -> str:
        """Append one audit event attributed to the acting user."""
        return self.audit.record(
            campaign_id=campaign_id,
            actor=self.actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            before=before,
            after=after,
            request_id=self.request_id,
            conn=conn,
        )


_ASPECT_LABELS = {
    "thresholds": "Les seuils",
    "items": "Le référentiel articles",
    "boms": "Les nomenclatures",
    "locations": "Le référentiel emplacements",
    "book_stock": "Le stock ERP",
    "zones": "Les zones GENERIQUE",
    "count_journals": "Les journaux de comptage",
    "count_sheets": "Les feuilles de comptage",
    "count_entries": "La saisie des comptages",
    "post_journal": "Le postage des journaux",
    "adjustments": "Les ajustements",
    "analysis": "L'analyse des écarts",
    "backflush": "L'écart backflush",
    "stock_flow": "La réconciliation entre campagnes",
}

_STATUS_LABELS = {
    CampaignStatus.PREPARATION: "PRÉPARATION",
    CampaignStatus.COUNTING: "COMPTAGE",
    CampaignStatus.ANALYSIS: "ANALYSE & AJUSTEMENTS",
    CampaignStatus.CLOSED: "CLÔTURÉE",
}
