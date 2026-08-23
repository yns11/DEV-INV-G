"""FastAPI dependencies: identity, service context and campaign resolution."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import Depends, Header, Request

from ..config import get_settings
from ..domain.models import Campaign
from ..errors import UnauthenticatedError
from ..services import (
    AnalysisService,
    BoardService,
    CampaignService,
    ConsolidationService,
    CountingService,
    EvidenceService,
    GenericService,
    ImportService,
    ManagerService,
    Perimeter,
    ReferentialService,
    ReportService,
    ScanJobService,
    ServiceContext,
)

log = logging.getLogger(__name__)

__all__ = [
    "CurrentUser",
    "Ctx",
    "CampaignDep",
    "get_current_user",
    "get_context",
    "get_campaign",
    "board_service",
    "campaign_service",
    "counting_service",
    "evidence_service",
    "generic_service",
    "analysis_service",
    "import_service",
    "manager_service",
    "referential_service",
    "report_service",
    "scan_job_service",
    "resolve_perimeter",
]


def get_current_user(
    request: Request,
    x_forwarded_email: Annotated[str | None, Header()] = None,
    x_forwarded_preferred_username: Annotated[str | None, Header()] = None,
    x_forwarded_user: Annotated[str | None, Header()] = None,
) -> str:
    """Identify the signed-in user from the platform's forwarded headers.

    Databricks Apps terminates authentication at the proxy and forwards the
    caller's identity. Those headers are the *only* trustworthy source: never
    read a user id from the request body, where a client could put anything.

    **Sans identité, on refuse.** Le comportement précédent retombait sur
    ``unknown@unauthenticated`` et laissait la requête écrire sous ce nom : une
    application jointe directement, ou un proxy mal configuré, créait et
    modifiait des données sous une identité générique que le journal d'audit
    enregistrait comme n'importe quelle autre. Une campagne d'inventaire est un
    dossier opposable ; « on ne sait pas qui » n'y est pas une identité
    acceptable, et l'erreur doit tomber à la porte plutôt que six couches plus
    bas dans une ligne d'audit.

    Hors plateforme (développement local) il n'y a pas de proxy, donc l'identité
    est un utilisateur local clairement nommé — jamais quelqu'un d'autre. Ce
    repli est **conditionné à ``INV_ENV=local``** : en déployé, il n'existe pas.
    """
    identity = (
        x_forwarded_email
        or x_forwarded_preferred_username
        or x_forwarded_user
    )
    if identity:
        return identity.strip().lower()
    if get_settings().env == "local":
        return "local@dev"
    log.error(
        "Requête sans en-tête d'identité en environnement déployé : "
        "l'application est joignable hors du proxy d'authentification.",
        extra={"path": request.url.path},
    )
    raise UnauthenticatedError(
        "Identité absente. Cette application doit être atteinte via le proxy "
        "d'authentification Databricks ; un accès direct est refusé."
    )


CurrentUser = Annotated[str, Depends(get_current_user)]


def get_context(request: Request, actor: CurrentUser) -> ServiceContext:
    """Build the per-request service context."""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    return ServiceContext(actor=actor, request_id=request_id)


Ctx = Annotated[ServiceContext, Depends(get_context)]


def get_campaign(campaign_id: str, ctx: Ctx) -> Campaign:
    """Resolve and validate the campaign in the path.

    Every campaign-scoped route depends on this, so a request for a campaign
    that does not exist fails once, here, with a clean 404 — never halfway
    through a use case.
    """
    return ctx.campaigns.get(campaign_id)


CampaignDep = Annotated[Campaign, Depends(get_campaign)]


# --------------------------------------------------------------------------- #
# Service factories
# --------------------------------------------------------------------------- #

def campaign_service(ctx: Ctx) -> CampaignService:
    return CampaignService(ctx)


def counting_service(ctx: Ctx) -> CountingService:
    return CountingService(ctx)


def generic_service(ctx: Ctx) -> GenericService:
    return GenericService(ctx)


def consolidation_service(ctx: Ctx) -> ConsolidationService:
    return ConsolidationService(ctx)


def analysis_service(ctx: Ctx) -> AnalysisService:
    return AnalysisService(ctx)


def import_service(ctx: Ctx) -> ImportService:
    return ImportService(ctx)


def manager_service(ctx: Ctx) -> ManagerService:
    return ManagerService(ctx)


def board_service(ctx: Ctx) -> BoardService:
    return BoardService(ctx)


def referential_service(ctx: Ctx) -> ReferentialService:
    return ReferentialService(ctx)


def evidence_service(ctx: Ctx) -> EvidenceService:
    return EvidenceService(ctx)


def resolve_perimeter(
    campaign: Campaign, ctx: ServiceContext, *, focus: bool, manager: str | None
) -> Perimeter | None:
    """The perimeter a focused read should filter with, or ``None``.

    ``None`` means "no filtering at all", which is what every read does by
    default. An *unresolved* perimeter — the signed-in user is not registered as
    a manager — is deliberately **not** ``None``: it filters everything out, so
    the interface can say "aucun objet ne vous est affecté" instead of showing a
    full list that silently ignored the switch.
    """
    if not focus:
        return None
    return ManagerService(ctx).perimeter(campaign, manager_code=manager)


def report_service(ctx: Ctx) -> ReportService:
    return ReportService(ctx)


def scan_job_service(ctx: Ctx) -> ScanJobService:
    return ScanJobService(ctx)
