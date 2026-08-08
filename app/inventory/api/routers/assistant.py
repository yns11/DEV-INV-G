"""Asking the campaign questions in plain French.

One endpoint, deliberately read-only. It answers from a digest of *this*
campaign and writes nothing back — which is what makes a conversational surface
safe to put next to an inventory people are about to post to their ERP.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from ...ai.assistant import MAX_QUESTION_CHARS, Attachment
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
    history: list[Turn] = Field(default_factory=list, max_length=20)


@router.post("/ask", summary="Poser une question sur la campagne")
def ask(campaign: CampaignDep, service: Service, body: Question) -> dict[str, Any]:
    """Answer a question from the campaign's own figures.

    The model sees a digest assembled server-side — identity, phase, progress,
    KPIs, largest variances, controls, zones, managers — and nothing else. It
    holds no database handle and no tools, so it can only be right or wrong
    about that digest; it can never change anything.
    """
    return service.ask(
        campaign,
        question=body.question,
        history=[t.model_dump() for t in body.history],
    )


@router.post("/ask-with-files", summary="Poser une question avec des pièces jointes")
async def ask_with_files(
    campaign: CampaignDep,
    service: Service,
    question: Annotated[str, Form()],
    history: Annotated[str, Form()] = "[]",
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006 — FastAPI form default
) -> dict[str, Any]:
    """Same question, with documents attached.

    Images and PDFs are shown to the model, text files are inlined. Their
    content is treated as data to read, never as instructions to follow — an
    attached document is precisely where a prompt injection would ride in.
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
    )


@router.get("/context", summary="Ce que le modèle voit de la campagne")
def context(campaign: CampaignDep, service: Service) -> dict[str, Any]:
    """The digest itself, verbatim.

    An assistant whose inputs can be inspected is one whose answers people can
    calibrate their trust in. This endpoint is what makes that possible.
    """
    return service.context(campaign)


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
    ][-20:]
