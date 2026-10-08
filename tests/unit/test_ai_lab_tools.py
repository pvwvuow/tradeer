"""The AI Lab agent's read-only tools, part 1 (docs/AI_LAB_AGENT.md section 3)."""

from __future__ import annotations

from app.ai.agent import Tool
from app.ai.lab_tools import LabData, lab_tools
from app.core.strategy_settings import StrategySettings
from tests.unit.test_ai_export import NOW, TRADES


def tools() -> dict[str, Tool]:
    data = LabData(
        trades=lambda: TRADES,
        settings=StrategySettings,
        mode=lambda: "Paper",
        balance=lambda: 10_000.0,
        currency=lambda: "USD",
        backtests=lambda: [("EURUSD trend_pullback", {"trades": 80, "expectancy_r": 0.21})],
        now=lambda: NOW,
    )
    return {tool.name: tool for tool in lab_tools(data)}


def run(name: str, **args: object) -> str:
    tool = tools()[name]
    return tool.run(args)


def test_every_tool_has_a_name_a_description_and_a_step_title() -> None:
    found = tools()
    assert list(found) == [
        "overview",
        "trades",
        "stats",
        "strategy_settings",
        "filter_settings",
        "backtests",
        "signals",
        "signal",
        "logs",
        "log_summary",
    ]
    for tool in found.values():
        assert tool.description and tool.title


def test_the_overview_and_the_trades() -> None:
    text = run("overview")
    assert "Mode: Paper." in text and "Balance: 10,000.00 USD" in text
    assert "All time: 12 trades, win rate 67%, net +111.60" in text
    assert "(too few to judge)" in text
    rows = run("trades", days=0, result="loss").splitlines()
    assert rows[0].startswith("4 trades, win rate 0%") and len(rows) == 6
    assert all("acc" not in row.split(" | ") for row in rows[2:])
    assert " | sl | " in rows[2] and "-1.07R" in rows[2]
    assert run("trades", days=0, limit=3).count("\n") == 4


def test_stats_by_group_and_a_wrong_group() -> None:
    text = run("stats", group_by="exit_reason", days=0)
    assert "- tp: 8 trades, win rate 100%" in text and "- sl: 4 trades, win rate 0%" in text
    assert run("stats", group_by="colour").startswith("group_by must be one of")


def test_settings_show_values_limits_and_meaning() -> None:
    text = run("strategy_settings", strategy="channel_breakout")
    assert text.startswith("channel_breakout 1.1.0 (off):")
    assert "  min_break_atr = 0.3 (default 0.3, 0 to 5) Smallest close past the edge" in text
    assert run("strategy_settings", strategy="nope").startswith("Unknown strategy nope")
    assert "max_spread_atr = 0.25" in run("filter_settings")
    assert "EURUSD trend_pullback" in run("backtests")


def test_without_signals_or_logs_the_tools_say_so() -> None:
    assert run("signals") == "No signals match."
    assert run("logs").startswith("No log lines at WARNING or above")
    assert run("log_summary").startswith("No log lines in the last 24 h")
