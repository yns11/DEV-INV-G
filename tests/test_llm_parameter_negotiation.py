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


class _Truncating:
    """Un endpoint qui coupe la réponse au plafond, comme le vrai le fait."""

    def __init__(self, text: str, finish_reason: str) -> None:
        self.text = text
        self.finish_reason = finish_reason

    def create(self, **_: Any) -> Any:
        message = type("_M", (), {"content": self.text})()
        choice = type(
            "_C", (), {"message": message, "finish_reason": self.finish_reason}
        )()
        return type("_R", (), {"choices": [choice], "usage": None, "model": "x"})()


def _json_client(text: str, finish_reason: str) -> LlmClient:
    client = LlmClient(endpoint="test-endpoint")
    api = _Truncating(text, finish_reason)
    client._client = type(
        "_Api", (), {"chat": type("_Chat", (), {"completions": api})()}
    )()
    return client


class TestATruncatedAnswerSaysSo:
    """« JSON inexploitable » et « réponse coupée » ne se corrigent pas pareil.

    En production, six lots de routage sur sept sont revenus tronqués — le
    plafond était trop bas pour ce qu'on demandait au modèle d'écrire. Le
    rapport affichait « Le modèle n'a pas renvoyé de JSON exploitable » sur
    soixante-douze pages, ce qui envoie chercher un défaut de prompt là où il
    n'y a qu'un budget trop court. La cause est dans `finish_reason` : la taire
    coûtait une campagne de scan.
    """

    def test_the_ceiling_is_named_when_the_answer_was_cut(self) -> None:
        client = _json_client('{"pages":[{"page":1,"sheet":"aaaa"', "length")
        with pytest.raises(UpstreamError) as excinfo:
            client.complete_json(system="s", user="u", max_tokens=1280)
        message = str(excinfo.value)
        assert "coupée" in message
        assert "1280" in message, "le plafond en cause doit être dans le message"

    def test_a_genuinely_malformed_answer_is_not_blamed_on_the_ceiling(self) -> None:
        client = _json_client("je ne peux pas lire ces images", "stop")
        with pytest.raises(UpstreamError) as excinfo:
            client.complete_json(system="s", user="u", max_tokens=1280)
        assert "coupée" not in str(excinfo.value)

    def test_the_finish_reason_travels_with_the_response(self) -> None:
        client = _json_client('{"ok":1}', "stop")
        _, response = client.complete_json(system="s", user="u")
        assert response.finish_reason == "stop"
        assert response.truncated is False
