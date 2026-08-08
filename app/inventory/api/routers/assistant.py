"""Asking the campaign questions in plain French.

Read-only, whatever the profile. How the assistant is framed — what it is told,
how much of the campaign it sees, how long it may answer — is a setting served
by ``/profiles`` and chosen per request, never a property of these handlers.
What no profile changes is that nothing here writes back to the campaign, which
is what makes a conversational surface safe to put next to an inventory people
are about to post to their ERP.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from ...ai.assistant import (
    MAX_QUESTION_CHARS,
    PROFILES,
    Attachment,
    profile_for,
)
from ...errors import ValidationError
from ...services.assistant_service import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    AssistantService,
)
from ..deps import CampaignDep, Ctx

log = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns/{campaign_id}/assistant", tags=["assistant"])


def assistant_service(ctx: Ctx) -> AssistantService:
    return AssistantService(ctx)


Service = Annotated[AssistantService, Depends(assistant_service)]


class Turn(BaseModel):
    """One previous exchange, so a follow-up question means something."""

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=MAX_QUESTION_CHARS)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[Turn] = Field(default_factory=list, max_length=50)
    #: Framing to answer under. Unknown or absent falls back to the configured
    #: default rather than failing — the profile is a setting, not a contract.
    profile: str | None = None


@router.post("/ask", summary="Poser une question sur la campagne")
def ask(campaign: CampaignDep, service: Service, body: Question) -> dict[str, Any]:
    """Answer a question, under the requested profile.

    Whatever the profile ships as context, the model holds no database handle
    and no tools: it can only be right or wrong about what it was given, and can
    never change anything.
    """
    return service.ask(
        campaign,
        question=body.question,
        history=[t.model_dump() for t in body.history],
        profile=body.profile,
    )


@router.post("/ask-with-files", summary="Poser une question avec des pièces jointes")
async def ask_with_files(
    campaign: CampaignDep,
    service: Service,
    question: Annotated[str, Form()],
    history: Annotated[str, Form()] = "[]",
    profile: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006 — FastAPI form default
) -> dict[str, Any]:
    """Same question, with documents attached.

    Images and PDFs are shown to the model, text files are inlined. In the
    grounded profiles their content is explicitly framed as data to read rather
    than instructions to follow — an attached document is precisely where a
    prompt injection would ride in. The open profile drops that frame, which is
    part of what "open" means.
    """
    if len(files) > MAX_ATTACHMENTS:
        raise ValidationError(
            f"{MAX_ATTACHMENTS} pièces jointes au maximum par question."
        )

    attachments: list[Attachment] = []
    for upload in files:
        payload = await upload.read()
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(
                f"« {upload.filename} » dépasse "
                f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} Mo.",
                filename=upload.filename,
            )
        if payload:
            attachments.append(Attachment(
                filename=upload.filename or "pièce jointe",
                content_type=upload.content_type or "application/octet-stream",
                payload=payload,
            ))

    return service.ask(
        campaign,
        question=question,
        history=_parse_history(history),
        attachments=attachments,
        profile=profile,
    )


@router.get("/context", summary="Ce que le modèle voit de la campagne")
def context(
    campaign: CampaignDep, service: Service, profile: str | None = None
) -> dict[str, Any]:
    """The context itself, verbatim, as the given profile would ship it.

    An assistant whose inputs can be inspected is one whose answers people can
    calibrate their trust in. This endpoint is what makes that possible — and in
    the open profile it returns ``{}``, which is the honest answer.
    """
    return service.context(campaign, profile=profile_for(profile))


@router.get("/profiles", summary="Les cadrages disponibles pour l'assistant")
def profiles() -> dict[str, Any]:
    """What framings exist, and which one answers by default.

    Exposed so the screen can offer the choice instead of hard-coding a list
    that drifts from the server's.
    """
    active = profile_for(None)
    return {
        "active": active.key,
        "profiles": [
            {
                "key": p.key,
                "label": p.label,
                "description": p.description,
                "scopeNote": p.scope_note,
                "context": p.context,
                "maxQuestionChars": p.max_question_chars,
                "maxAnswerTokens": p.max_answer_tokens,
                "temperature": p.temperature,
            }
            for p in PROFILES.values()
        ],
    }


def _parse_history(raw: str) -> list[dict[str, str]]:
    """Prior turns arrive as a JSON string — multipart has no nested types."""
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [
        {"role": str(t.get("role", "user")), "content": str(t.get("content", ""))}
        for t in parsed
        if isinstance(t, dict)
    ][-50:]
