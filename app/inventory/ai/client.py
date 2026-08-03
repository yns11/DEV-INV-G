"""Foundation-model client for the Databricks serving endpoint.

Everything AI-related goes through this one wrapper so that timeouts, retries,
token budgets and the "AI output is a *proposal*, never a decision" rule are
enforced in a single place.

The Databricks serving endpoint exposes an OpenAI-compatible chat API, so the
same code path handles text prompts and multimodal (image) prompts.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import Settings, get_settings
from ..errors import UpstreamError

log = logging.getLogger(__name__)

__all__ = ["LlmClient", "LlmResponse", "get_llm_client"]


@dataclass(slots=True)
class LlmResponse:
    """A model answer plus what it cost, for observability and cost control."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class _TransientLlmError(RuntimeError):
    """Retryable failure (rate limit, cold start, transient 5xx)."""


class LlmClient:
    """Thin client over a Databricks model-serving endpoint.

    :param endpoint: serving endpoint name, injected from the app resource.
    :param timeout: per-call budget. Kept well under the platform's hard 120 s
        proxy limit so a slow model surfaces as a clean error rather than an
        unexplained 504 with nothing in the logs.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        settings: Settings | None = None,
        timeout: float = 90.0,
    ) -> None:
        self._settings = settings or get_settings()
        self._endpoint = endpoint or self._settings.llm_endpoint
        self._timeout = timeout
        self._client: Any | None = None

    # ------------------------------------------------------------------ setup

    def _openai(self) -> Any:
        """Lazily build the OpenAI-compatible client bound to the workspace.

        ``get_open_ai_client()`` imports the ``openai`` package and raises
        ``ImportError`` when it is absent — which is why ``openai`` is a
        declared runtime dependency even though this project never imports it
        directly. The helper is deprecated in favour of ``databricks-openai``;
        it still works, and migrating means taking on the auth plumbing it
        currently handles (host resolution and OAuth refresh), so the swap is
        deliberately deferred until that package is required for something else.
        """
        if self._client is None:
            try:
                from databricks.sdk import WorkspaceClient

                self._client = WorkspaceClient().serving_endpoints.get_open_ai_client()
            except Exception as exc:  # pragma: no cover - depends on workspace
                raise UpstreamError(
                    "Client de modèle indisponible. Vérifiez que la ressource "
                    "« serving-endpoint » est attachée à l'application.",
                    cause=str(exc),
                ) from exc
        return self._client

    @property
    def endpoint(self) -> str:
        return self._endpoint

    # ------------------------------------------------------------------- call

    @retry(
        retry=retry_if_exception_type(_TransientLlmError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=12),
        reraise=True,
    )
    def complete(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[bytes] = (),
        image_mime: str = "image/png",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        response_json: bool = False,
    ) -> LlmResponse:
        """One chat completion.

        :param images: raw image bytes appended to the user turn as data URLs.
            Used to read scanned counting sheets.
        :param temperature: defaults to 0 — transcribing a counting sheet is a
            deterministic task, and creativity there is a defect.
        :param response_json: ask the endpoint for strict JSON output when the
            model supports it.
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for blob in images:
            encoded = base64.b64encode(blob).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{encoded}"},
            })

        kwargs: dict[str, Any] = {
            "model": self._endpoint,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content if images else user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": self._timeout,
        }
        if response_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            completion = self._openai().chat.completions.create(**kwargs)
        except Exception as exc:
            if _is_transient(exc):
                log.warning("Transient LLM error, retrying: %s", exc)
                raise _TransientLlmError(str(exc)) from exc
            raise UpstreamError(
                f"Appel au modèle « {self._endpoint} » impossible.", cause=str(exc)
            ) from exc

        choice = completion.choices[0] if completion.choices else None
        text = (choice.message.content if choice and choice.message else "") or ""
        usage = getattr(completion, "usage", None)
        return LlmResponse(
            text=text,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=getattr(completion, "model", self._endpoint) or self._endpoint,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[bytes] = (),
        image_mime: str = "image/png",
        max_tokens: int = 4096,
    ) -> tuple[dict[str, Any], LlmResponse]:
        """A completion whose answer must be a JSON object.

        Models occasionally wrap JSON in a fenced code block or add a sentence
        of preamble; both are stripped before parsing rather than failing the
        whole extraction over formatting.
        """
        response = self.complete(
            system=system,
            user=user,
            images=images,
            image_mime=image_mime,
            max_tokens=max_tokens,
            response_json=not images,  # image models often reject response_format
        )
        payload = _extract_json(response.text)
        if payload is None:
            raise UpstreamError(
                "Le modèle n'a pas renvoyé de JSON exploitable.",
                sample=response.text[:400],
            )
        return payload, response


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from a model answer."""
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return None


def _is_transient(exc: BaseException) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status in (408, 429, 500, 502, 503, 504):
        return True
    return any(
        marker in text
        for marker in ("rate limit", "timeout", "timed out", "temporarily",
                       "overloaded", "503", "502", "429")
    )


_client: LlmClient | None = None


def get_llm_client(settings: Settings | None = None) -> LlmClient:
    """Process-wide client. Cheap to build, but the SDK handshake is not."""
    global _client
    if _client is None:
        _client = LlmClient(settings=settings)
    return _client
