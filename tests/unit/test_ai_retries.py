"""0.50.3 (docs/AI_AUTOPILOT.md B18): a call that fails on 429, a 5xx or a quick network
error is tried twice more; a key error or a slow timeout is not."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from app.ai.transport import RETRY_SECONDS, AiCallError, AiClient, Connection, FailureKind
from app.analytics.llm_client import LlmError

REPLY = json.dumps(
    {
        "model": "m",
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    },
).encode("utf-8")
ASK = [{"role": "user", "content": "hi"}]


class Flaky:
    """A transport that fails with the given errors first, then answers."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self, url: str, headers: Mapping[str, str], data: bytes, timeout: float) -> bytes:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return REPLY


class Clock:
    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def client(transport: Flaky, waits: list[float], step: float = 0.0) -> AiClient:
    return AiClient(
        Connection("https://api.example.com/v1", "m"),
        "key",
        transport=transport,
        clock=Clock(step),
        sleep=waits.append,
    )


def test_a_busy_service_is_tried_again_and_answers() -> None:
    waits: list[float] = []
    transport = Flaky(LlmError("HTTP 503: overloaded"), LlmError("HTTP 429: slow down"))
    answer = client(transport, waits).complete(ASK)
    assert answer.text == "hello" and transport.calls == 3
    assert waits == list(RETRY_SECONDS)


def test_three_failures_in_a_row_give_up() -> None:
    waits: list[float] = []
    transport = Flaky(*(LlmError("HTTP 502: bad gateway") for _ in range(3)))
    with pytest.raises(AiCallError) as caught:
        client(transport, waits).complete(ASK)
    assert caught.value.kind is FailureKind.SERVER and transport.calls == 3


def test_a_quick_network_error_is_tried_again_a_slow_one_is_not() -> None:
    waits: list[float] = []
    quick = Flaky(LlmError("Could not reach the endpoint: getaddrinfo failed"))
    assert client(quick, waits).complete(ASK).text == "hello"
    assert quick.calls == 2 and waits == [RETRY_SECONDS[0]]
    slow = Flaky(LlmError("Could not reach the endpoint: timed out"))
    with pytest.raises(AiCallError):
        client(slow, [], step=20.0).complete(ASK)
    assert slow.calls == 1


def test_a_refused_key_is_not_tried_again() -> None:
    waits: list[float] = []
    transport = Flaky(LlmError("HTTP 401: bad key"))
    with pytest.raises(AiCallError) as caught:
        client(transport, waits).complete(ASK)
    assert caught.value.kind is FailureKind.KEY and transport.calls == 1 and waits == []
