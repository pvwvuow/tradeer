"""The AI Lab agent's tools, part 2 (docs/AI_LAB_AGENT.md section 3, Phase 18c): signals
with the checks that failed, one decision trace, and the saved logs, masked."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.ai.lab_tools import LabData, lab_tools, private
from app.core.strategy_settings import StrategySettings
from app.domain.signals import Direction, SignalRecord, SignalState, signal_id
from tests.unit.signal_helpers import MORNING, make_record, make_signal
from tests.unit.test_ai_export import TRADES

NOW = float(MORNING + 3600)


def filtered() -> SignalRecord:
    """A channel breakout sell that the spread filter stopped."""
    signal = make_signal(
        id=signal_id("channel_breakout", "hash", "EURUSD", "M15", MORNING),
        strategy="channel_breakout",
        direction=Direction.SHORT,
        sl=1.101,
        tp=1.098,
    )
    record = make_record(signal)
    record.trace.add("filter", "spread vs ATR", False, value=0.4, threshold=0.25)
    moved = signal.with_state(SignalState.FILTERED_OUT, float(MORNING), "spread vs ATR")
    return SignalRecord(
        moved,
        record.trace,
        record.probability,
        reject_reason="spread vs ATR",
    )


def pending() -> SignalRecord:
    signal = make_signal()
    moved = signal.with_state(SignalState.PENDING_APPROVAL, float(MORNING), "all filters passed")
    return make_record(moved)


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat()


LOGS: list[dict[str, Any]] = [
    {"time": stamp(NOW - 7200), "level": "ERROR", "category": "mt5", "message": "old error"},
    {"time": stamp(NOW - 600), "level": "INFO", "category": "app", "message": "started"},
    {
        "time": stamp(NOW - 500),
        "level": "WARNING",
        "category": "mt5",
        "message": "Order 123456789 refused for account 87654321, api_key=sk-abcdefghijklmnop12",
    },
    {
        "time": stamp(NOW - 400),
        "level": "WARNING",
        "category": "mt5",
        "message": "Order 123456790 refused for account 87654321, api_key=sk-abcdefghijklmnop12",
    },
    {"time": stamp(NOW - 300), "level": "ERROR", "category": "ai", "message": "AI timed out"},
]


def entries(since: float) -> Sequence[Mapping[str, Any]]:
    return LOGS


def run(name: str, **args: object) -> str:
    data = LabData(
        trades=lambda: TRADES,
        settings=StrategySettings,
        now=lambda: NOW,
        signals=lambda: [pending(), filtered()],
        log_entries=entries,
    )
    tools = {tool.name: tool for tool in lab_tools(data)}
    return tools[name].run(args)


def test_signals_show_the_state_and_the_failed_checks() -> None:
    text = run("signals", days=0)
    assert text.startswith("2 signals: ")
    assert "FILTERED_OUT 1" in text and "PENDING_APPROVAL 1" in text
    assert "Failed checks (how often): spread vs ATR 1." in text
    rows = [line for line in text.splitlines() if " | channel_breakout | " in line]
    assert len(rows) == 1 and rows[0].endswith(" | spread vs ATR")
    only = run("signals", days=0, state="filtered_out")
    assert only.startswith("1 signals: FILTERED_OUT 1")
    assert run("signals", days=0, strategy="nope") == "No signals match."


def test_one_signal_shows_its_whole_trace() -> None:
    record = filtered()
    text = run("signal", id=record.signal.id[:8])
    assert text.startswith("Sell EURUSD at 1.10000")
    assert "Not traded because: spread vs ATR" in text
    assert "\u2717 filter: spread vs ATR = 0.4 (limit 0.25)" in text
    assert "NEW -> FILTERED_OUT" in text
    assert run("signal", id="ab").startswith("Give at least the first 4 characters")
    assert run("signal", id="zzzzzzzz").startswith("No signal with an id starting")


def test_logs_are_masked_and_limited_to_the_hours() -> None:
    text = run("logs", hours=1)
    assert text.startswith("3 lines at WARNING or above in the last 1 h")
    assert "old error" not in text and "started" not in text
    assert "87654321" not in text and "123456789" not in text and "<number>" in text
    assert "sk-abcdefghijklmnop12" not in text
    assert text.splitlines()[-1].endswith("ai: AI timed out")
    assert run("logs", hours=1, level="ERROR").count("\n") == 1
    assert run("logs", hours=1, contains="timed").startswith("1 lines")
    assert run("logs", level="LOUD").startswith("level must be one of")


def test_the_log_summary_counts_and_groups_repeats() -> None:
    text = run("log_summary", hours=1)
    assert text.startswith("Last 1 h: 4 log lines (WARNING 2, INFO 1, ERROR 1).")
    assert "  2 x Order <number> refused for account <number>" in text
    assert "  1 x AI timed out" in text


def test_private_hides_long_numbers_and_secrets() -> None:
    assert private("ticket 1234567 at 1.10500") == "ticket <number> at 1.10500"
    assert "sk-abcdefghijklmnop12" not in private("key sk-abcdefghijklmnop12")
