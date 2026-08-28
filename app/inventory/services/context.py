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
    EarlyCountBatchRepository,
    EarlyCountDriftRepository,
    ErpJournalRepository,
    EvidenceBlobRepository,
    ImportBatchRepository,
    JournalRepository,
    ReferentialRepository,
    ScanJobRepository,
    SheetRepository,
    StockFlowRepository,
    get_database,
)
from ..domain.access import Role, restrict, role_of
from ..domain.enums import AuditAction, CampaignStatus
from ..domain.models import Campaign
from ..domain.sequence import Progress, blocking_reason
from ..domain.workflow import Editable, mutability_of
from ..errors import FrozenError, PermissionDeniedError
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
    #: Memoised by :meth:`role`, keyed by campaign, pour la même raison.
    _roles: dict[str, Role] = field(default_factory=dict, repr=False)

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
    def erp_journals(self) -> ErpJournalRepository:
        return ErpJournalRepository(self.db)

    @functools.cached_property
    def early_counts(self) -> EarlyCountBatchRepository:
        return EarlyCountBatchRepository(self.db)

    @functools.cached_property
    def drifts(self) -> EarlyCountDriftRepository:
        return EarlyCountDriftRepository(self.db)

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
    def scan_jobs(self) -> ScanJobRepository:
        return ScanJobRepository(self.db)

    @functools.cached_property
    def evidence_blobs(self) -> EvidenceBlobRepository:
        return EvidenceBlobRepository(self.db)

    @functools.cached_property
    def evidence(self) -> EvidenceStore:
        """L'archive des pièces justificatives : un volume UC, ou la base.

        Le dépôt lui est passé même en mode « volume » — il ne coûte qu'une
        référence, et le construire ici plutôt qu'à la volée garde l'archive sur
        la connexion du contexte au lieu d'en ouvrir une seconde.
        """
        return EvidenceStore(self.settings, blobs=self.evidence_blobs)

    # -- cross-cutting concerns ----------------------------------------------

    def role(self, campaign: Campaign) -> Role:
        """Ce que l'utilisateur courant est vis-à-vis de cette campagne.

        Mémoïsé par campagne, et pour la même raison que :meth:`progress` : la
        garde s'exécute avant chaque écriture, et un import de deux cent mille
        lignes ne doit pas relire la liste des gestionnaires à chaque ligne.

        Clé par campagne, jamais partagée : le clonage en lit deux, et une
        entrée commune répondrait à la seconde avec le rôle de la première —
        c'est-à-dire ouvrirait une campagne sur les droits d'une autre.
        """
        cached = self._roles.get(campaign.id)
        if cached is None:
            cached = role_of(
                self.actor, campaign, self.referentials.list_managers(campaign.id)
            )
            self._roles[campaign.id] = cached
        return cached

    def permissions(self, campaign: Campaign) -> Editable:
        """Ce que l'utilisateur courant peut réellement modifier.

        L'intersection des deux barrières — la phase et l'identité. C'est cet
        objet que l'API renvoie et que l'interface lit pour désactiver ses
        boutons, ce qui fait qu'aucun écran n'a besoin de connaître la notion
        de rôle pour se comporter correctement.
        """
        return restrict(mutability_of(campaign.status), self.role(campaign))

    def require_write(self, campaign: Campaign) -> None:
        """Refuse toute écriture à qui n'est ni propriétaire ni gestionnaire."""
        if self.role(campaign).may_write:
            return
        owner = campaign.created_by or "son créateur"
        raise PermissionDeniedError(
            f"Vous consultez la campagne {campaign.code} en lecture seule. "
            f"Demandez à {owner} de vous déclarer comme gestionnaire pour "
            "pouvoir la modifier.",
            campaignId=campaign.id,
            role=str(self.role(campaign)),
            createdBy=campaign.created_by,
        )

    def require_owner(self, campaign: Campaign, action: str) -> None:
        """Refuse à un gestionnaire une action réservée au propriétaire.

        Deux seulement, et elles portent sur la campagne plutôt que sur ses
        données : déclarer les gestionnaires, et supprimer la campagne. Un
        gestionnaire qui pourrait déclarer les autres s'accorderait le droit
        d'en accorder.
        """
        if self.role(campaign).is_owner:
            return
        owner = campaign.created_by or "son créateur"
        raise PermissionDeniedError(
            f"{action.capitalize()} est réservé à {owner}, qui a créé la "
            f"campagne {campaign.code}.",
            campaignId=campaign.id,
            role=str(self.role(campaign)),
            createdBy=campaign.created_by,
        )

    def guard(self, campaign: Campaign, aspect: str) -> None:
        """Refuse a write that this phase freezes, or that comes too early.

        Trois questions, vérifiées au même endroit parce qu'un appelant ne doit
        pas pouvoir en satisfaire une et oublier les autres.

        *Qui écrit ?* — propriétaire ou gestionnaire déclaré. Posée en premier
        parce que c'est le refus le plus utile à lire : « vous êtes en lecture
        seule » se corrige en demandant un droit, « c'est gelé » ne se corrige
        pas du tout.

        *Is it frozen?* — ``aspect`` is an attribute of
        :class:`~inventory.domain.workflow.Editable` (``items``, ``book_stock``,
        ``count_entries``, …), and the phase decides. This is what makes
        "geler" a guarantee rather than a convention.

        *Is it too early?* — within a phase the steps are still ordered, and
        :mod:`inventory.domain.sequence` holds that order. ``post_journal`` is a
        pseudo-aspect: nothing is frozen by it, but it must not happen before
        the ERP stock has stopped moving.
        """
        self.require_write(campaign)

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
    "early_counts": "Les comptages avancés",
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
