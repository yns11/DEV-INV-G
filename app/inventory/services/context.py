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
    BookStockRepository,
    CampaignRepository,
    ConsolidationRepository,
    Database,
    ImportBatchRepository,
    JournalRepository,
    ReferentialRepository,
    SheetRepository,
    get_database,
)
from ..domain.enums import AuditAction, CampaignStatus
from ..domain.models import Campaign
from ..domain.workflow import Editable, mutability_of
from ..errors import FrozenError

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
    def analysis(self) -> AnalysisRepository:
        return AnalysisRepository(self.db)

    @functools.cached_property
    def audit(self) -> AuditRepository:
        return AuditRepository(self.db)

    @functools.cached_property
    def imports(self) -> ImportBatchRepository:
        return ImportBatchRepository(self.db)

    # -- cross-cutting concerns ----------------------------------------------

    def guard(self, campaign: Campaign, aspect: str) -> None:
        """Raise :class:`FrozenError` if *aspect* is frozen in this phase.

        ``aspect`` is an attribute name of :class:`~inventory.domain.workflow.Editable`
        (``items``, ``book_stock``, ``count_journals``, …). Calling this before
        every write is what makes "geler" a guarantee rather than a convention.
        """
        editable: Editable = mutability_of(campaign.status)
        if not hasattr(editable, aspect):
            raise ValueError(f"unknown editable aspect {aspect!r}")
        if not getattr(editable, aspect):
            raise FrozenError(
                f"« {_ASPECT_LABELS.get(aspect, aspect)} » est gelé au statut "
                f"{_STATUS_LABELS[campaign.status]} de la campagne.",
                aspect=aspect,
                status=str(campaign.status),
            )

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
    "book_stock": "Le stock livre",
    "zones": "Les zones GENERIQUE",
    "count_journals": "Les journaux de comptage",
    "count_sheets": "Les feuilles de comptage",
    "adjustments": "Les ajustements",
    "analysis": "L'analyse des écarts",
}

_STATUS_LABELS = {
    CampaignStatus.PREPARATION: "PRÉPARATION",
    CampaignStatus.COUNTING: "COMPTAGE",
    CampaignStatus.ANALYSIS: "ANALYSE & AJUSTEMENTS",
    CampaignStatus.CLOSED: "CLÔTURÉE",
}
