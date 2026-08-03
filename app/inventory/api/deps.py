"""FastAPI dependencies: identity, service context and campaign resolution."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import Depends, Header, Request

from ..config import get_settings
from ..domain.models import Campaign
from ..services import (
    AnalysisService,
    CampaignService,
    CountingService,
    GenericService,
    ImportService,
    ReportService,
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
    "campaign_service",
    "counting_service",
    "generic_service",
    "analysis_service",
    "import_service",
    "report_service",
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

    Outside the platform (local development) there is no proxy, so the identity
    falls back to a clearly-marked local user rather than pretending to be
    someone. Every audit entry then says exactly what it is.
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
    # Deployed but no identity header: the app is reachable without the proxy,
    # which is a misconfiguration worth seeing in the logs.
    log.warning("No forwarded identity header on a non-local request")
    return "unknown@unauthenticated"


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


def analysis_service(ctx: Ctx) -> AnalysisService:
    return AnalysisService(ctx)


def import_service(ctx: Ctx) -> ImportService:
    return ImportService(ctx)


def report_service(ctx: Ctx) -> ReportService:
    return ReportService(ctx)
