"""The optional AI request (spec C13): off by default, a compact masked summary only, the
key only in the Authorization header, tokens and cost from the answer."""

import json
import math
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.analytics.ai_export import ExportData
from app.analytics.llm_client import (
    LLM_FILE_NAME,
    SUMMARY_LIMIT,
    LlmError,
    LlmSettings,
    LlmSettingsSource,
    ask,
    build_messages,
    compact_summary,
    endpoint,
    key_name,
    limits,
    load_llm_settings,
    parse_answer,
    save_llm_settings,
    url_problem,
    usage_text,
)
from app.core.strategy_settings import load_strategy_settings
from app.domain.modes import OperatingMode
from tests.unit.test_ai_export import TRADES

KEY = "sk-unit-test-key-0123456789"
CONTENT = 'Too few trades.\n```json\n{"changes": []}\n```'


def reply(content: str = CONTENT, **extra: object) -> bytes:
    payload: dict[str, object] = {
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 2000, "completion_tokens": 500},
    }
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


class FakeTransport:
    def __init__(self, answer: bytes = b"", error: Exception | None = None) -> None:
        self.answer = answer or reply()
        self.error = error
        self.calls: list[tuple[str, dict[str, str], bytes, float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
        self.calls.append((url, dict(headers), body, timeout))
        if self.error is not None:
            raise self.error
        return self.answer


def on(**changes: object) -> LlmSettings:
    values: dict[str, object] = {"enabled": True, "input_price": 0.5, "output_price": 2.0}
    values.update(changes)
    return LlmSettings.model_validate(values)


def data(tmp_path: Path) -> ExportData:
    return ExportData(
        trades=TRADES,
        settings=load_strategy_settings(tmp_path),
        mode=OperatingMode.PAPER,
        start_balance=10_000.0,
        currency="USD",
        rejected=[("spread too wide", 4)],
        scope="all time",
    )


def test_it_is_off_by_default_and_round_trips(tmp_path: Path) -> None:
    assert load_llm_settings(tmp_path) == LlmSettings()
    assert not LlmSettings().enabled and not LlmSettings().include_login
    source = LlmSettingsSource(tmp_path)
    source.save(on(model="my-model"))
    assert load_llm_settings(tmp_path).model == "my-model"
    assert LlmSettingsSource(tmp_path).settings.enabled
    (tmp_path / LLM_FILE_NAME).write_text("{broken", encoding="utf-8")
    assert load_llm_settings(tmp_path) == LlmSettings()
    save_llm_settings(tmp_path / "new", LlmSettings())
    assert (tmp_path / "new" / LLM_FILE_NAME).is_file()
    assert key_name("Main profile").endswith("/llm-api-key")


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://api.openai.com/v1", True),
        ("https://openrouter.ai/api/v1/", True),
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:1234/v1", True),
        ("http://example.com/v1", False),
        ("ftp://example.com", False),
        ("https://user:pass@example.com/v1", False),
        ("https://example.com/v1?key=1", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_only_https_or_a_local_server(url: str, ok: bool) -> None:
    assert (url_problem(url) == "") is ok


def test_the_endpoint_is_the_chat_completions_path() -> None:
    assert endpoint("https://api.openai.com/v1/") == "https://api.openai.com/v1/chat/completions"


def test_limits_come_from_the_schema() -> None:
    schema = {
        "properties": {
            "period": {"type": "integer", "minimum": 2, "maximum": 50},
            "side": {"enum": ["buy", "sell"]},
            "flag": {"type": "boolean"},
        },
    }
    text = limits(schema)
    assert "period integer 2..50" in text and "side one of buy/sell" in text
    assert "flag boolean" in text
    assert limits({}) == ""


def test_the_summary_is_compact_and_has_no_login_unless_asked(tmp_path: Path) -> None:
    text = compact_summary(data(tmp_path), 1_790_000_000.0)
    assert text.startswith("# Trading summary")
    for part in ("## Statistics", "## Breakdowns", "## Probability calibration"):
        assert part in text
    assert "## Strategy parameters" in text and "trend_pullback" in text and "Limits:" in text
    assert "spread too wide: 4" in text
    assert "Account number" not in text
    assert len(text) <= SUMMARY_LIMIT + 40
    with_login = compact_summary(data(tmp_path), 1_790_000_000.0, login="12345678")
    assert "Account number: 12345678" in with_login


def test_the_summary_masks_secrets(tmp_path: Path) -> None:
    source = data(tmp_path)
    leaky = ExportData(
        trades=source.trades,
        settings=source.settings,
        mode=source.mode,
        scope=f"scope with {KEY} and my-own-secret-value",
    )
    text = compact_summary(leaky, 1_790_000_000.0, secrets=("my-own-secret-value",))
    assert KEY not in text and "my-own-secret-value" not in text


def test_messages_carry_the_question_or_a_default() -> None:
    messages = build_messages("SUMMARY", "Why do I lose on Fridays?")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "JSON" in messages[0]["content"] and "advice only" in messages[0]["content"]
    assert messages[1]["content"].startswith("Why do I lose on Fridays?")
    assert "SUMMARY" in messages[1]["content"]
    assert build_messages("S")[1]["content"].startswith("Review this trading system")


def test_a_request_sends_the_key_only_as_a_header() -> None:
    transport = FakeTransport()
    times = iter([10.0, 12.5])
    answer = ask(
        on(),
        KEY,
        build_messages("SUMMARY"),
        transport=transport,
        clock=lambda: next(times),
    )
    url, headers, body, timeout = transport.calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert KEY.encode() not in body
    sent = json.loads(body)
    assert sent["model"] == "gpt-4o-mini" and sent["messages"][1]["content"].endswith("SUMMARY")
    assert timeout == 90.0
    assert answer.text == CONTENT and answer.model == "test-model"
    assert answer.usage.prompt_tokens == 2000 and answer.usage.completion_tokens == 500
    assert math.isclose(answer.usage.cost, 0.002)
    assert answer.seconds == 2.5
    assert usage_text(answer) == "2000 in + 500 out tokens, about $0.0020"


def test_a_local_server_needs_no_key() -> None:
    transport = FakeTransport()
    settings = on(base_url="http://localhost:11434/v1", input_price=0.0, output_price=0.0)
    answer = ask(settings, "", build_messages("S"), transport=transport)
    assert "Authorization" not in transport.calls[0][1]
    assert math.isnan(answer.usage.cost) and "cost unknown" in usage_text(answer)


def test_refusals_and_bad_answers_raise_without_the_key() -> None:
    messages = build_messages("S")
    with pytest.raises(LlmError, match="off"):
        ask(LlmSettings(), KEY, messages, transport=FakeTransport())
    with pytest.raises(LlmError, match="No API key"):
        ask(on(), "", messages, transport=FakeTransport())
    with pytest.raises(LlmError, match="https"):
        ask(on(base_url="http://example.com/v1"), KEY, messages, transport=FakeTransport())
    failing = FakeTransport(error=LlmError(f"HTTP 401: invalid key {KEY}"))
    with pytest.raises(LlmError) as caught:
        ask(on(), KEY, messages, transport=failing)
    assert KEY not in str(caught.value) and "HTTP 401" in str(caught.value)
    broken = FakeTransport(error=RuntimeError(f"socket closed {KEY}"))
    with pytest.raises(LlmError) as caught:
        ask(on(), KEY, messages, transport=broken)
    assert KEY not in str(caught.value) and "RuntimeError" in str(caught.value)
    with pytest.raises(LlmError, match="JSON"):
        parse_answer(b"<html>", on(), 0.0)
    with pytest.raises(LlmError, match="empty"):
        parse_answer(reply(choices=[]), on(), 0.0)
    with pytest.raises(LlmError, match="empty"):
        parse_answer(reply("   "), on(), 0.0)


def test_missing_usage_counts_as_zero_tokens() -> None:
    answer = parse_answer(reply(usage=None, model=None), on(model="mine"), 1.0)
    assert answer.usage.prompt_tokens == 0 and answer.model == "mine"
    assert answer.usage.cost == 0.0
