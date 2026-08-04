"""Negotiating away the parameters a serving endpoint refuses.

Serving endpoints do not advertise which optional parameters a model accepts:
they answer a plain 400 naming the offender. Claude Opus 4.8 refuses
``temperature``; others refuse ``max_tokens`` under that name. Getting this
wrong makes every AI feature unavailable while the endpoint, the resource and
the permissions are all perfectly fine — which is exactly how it presented in
production, and why it is pinned here.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from inventory.ai.client import LlmClient, _provider_message, _unsupported_parameter
from inventory.errors import UpstreamError


class _RefusalError(Exception):
    """Shaped like the SDK's BadRequestError: the body carries the message."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Error code: 400 - {{'message': '{message}'}}")
        self.body = {"error_code": "BAD_REQUEST", "message": message}


class _Completions:
    def __init__(self, refuses: set[str]) -> None:
        self._refuses = refuses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        for name in sorted(self._refuses):
            if name in kwargs:
                raise _RefusalError(
                    f"BAD_REQUEST: Model eu.anthropic.x does not support "
                    f"the {name} parameter."
                )

        class _Message:
            content = "ok"

        class _Choice:
            message = _Message()

        class _Completion:
            choices: ClassVar[list[Any]] = [_Choice()]
            usage = None
            model = "x"

        return _Completion()


def _client(refuses: set[str]) -> tuple[LlmClient, _Completions]:
    client = LlmClient(endpoint="test-endpoint")
    completions = _Completions(refuses)
    client._client = type("_Api", (), {"chat": type("_Chat", (), {"completions": completions})()})()
    return client, completions


def test_drops_a_refused_temperature_and_succeeds() -> None:
    client, api = _client({"temperature"})
    assert client.complete(system="s", user="u").text == "ok"
    assert "temperature" not in api.calls[-1]
    assert len(api.calls) == 2, "un seul aller-retour perdu"


def test_remembers_the_refusal_for_later_calls() -> None:
    client, api = _client({"temperature"})
    client.complete(system="s", user="u")
    before = len(api.calls)
    client.complete(system="s", user="u")
    assert len(api.calls) == before + 1, "la leçon doit être retenue"


def test_renames_max_tokens_rather_than_losing_the_ceiling() -> None:
    """Dropping it outright would leave a completion with no bound at all."""
    client, api = _client({"max_tokens"})
    client.complete(system="s", user="u", max_tokens=1234)
    assert api.calls[-1]["max_completion_tokens"] == 1234
    assert "max_tokens" not in api.calls[-1]


def test_negotiates_several_refusals_in_one_call() -> None:
    client, api = _client({"temperature", "response_format"})
    assert client.complete(system="s", user="u", response_json=True).text == "ok"
    assert "temperature" not in api.calls[-1]
    assert "response_format" not in api.calls[-1]


def test_an_unrelated_400_is_not_retried_away() -> None:
    """A malformed prompt must surface as itself, not as a parameter dance."""
    client = LlmClient(endpoint="test-endpoint")

    class _Boom:
        def create(self, **_: Any) -> Any:
            raise _RefusalError("BAD_REQUEST: image exceeds the maximum size")

    client._client = type("_Api", (), {"chat": type("_Chat", (), {"completions": _Boom()})()})()
    with pytest.raises(UpstreamError) as excinfo:
        client.complete(system="s", user="u")
    assert "image exceeds the maximum size" in str(excinfo.value)


def test_the_error_carries_the_provider_wording() -> None:
    """The message the user and the log see must name the real cause."""
    assert _provider_message(_RefusalError("BAD_REQUEST: nope")) == "BAD_REQUEST: nope"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Model x does not support the temperature parameter.", "temperature"),
        ("Model x doesn't support max_tokens", "max_tokens"),
        ("unsupported parameter: response_format", "response_format"),
        ("Model x does not support the max tokens parameter", "max_tokens"),
        ("rate limit exceeded", None),
        ("temperature must be between 0 and 1", None),
    ],
)
def test_refusal_detection(message: str, expected: str | None) -> None:
    assert _unsupported_parameter(_RefusalError(message)) == expected
