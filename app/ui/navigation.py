"""Page registry for the Advanced sidebar and the Simple view (spec F2 and F3)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    page_id: str
    title: str
    group: str
    phase: int
    summary: str


ADVANCED_GROUPS: tuple[str, ...] = ("Trade", "Analyze", "System")

ADVANCED_PAGES: tuple[PageSpec, ...] = (
    PageSpec("dashboard", "Dashboard", "Trade", 12, "KPIs, equity, drawdown and limit usage."),
    PageSpec("market", "Market", "Trade", 5, "Watchlist, analysis cards and the chart."),
    PageSpec("signals", "Signals", "Trade", 6, "Live signals with full decision traces."),
    PageSpec("positions", "Positions & Trades", "Trade", 8, "Open positions and history."),
    PageSpec("analytics", "Analytics", "Analyze", 12, "Performance and behavior analytics."),
    PageSpec("journal", "Journal", "Analyze", 12, "Journal, P/L calendar and reports."),
    PageSpec("backtest", "Backtest", "Analyze", 10, "Backtests, walk-forward, Monte-Carlo."),
    PageSpec("model", "Model", "Analyze", 11, "Probability model, calibration, drift."),
    PageSpec("ai_lab", "AI Lab", "Analyze", 13, "AI export, suggestions and comparison."),
    PageSpec("strategies", "Strategies", "System", 6, "Strategy cards and parameters."),
    PageSpec("risk", "Risk", "System", 7, "Limits, usage, exposure and profiles."),
    PageSpec("logs", "Logs", "System", 2, "Structured logs, filters and traces."),
    PageSpec("health", "Health", "System", 14, "Health checks, metrics, debug bundle."),
    PageSpec("settings", "Settings", "System", 3, "Accounts, connections and appearance."),
)

SIMPLE_HOME = PageSpec(
    page_id="home",
    title="Home",
    group="Simple",
    phase=9,
    summary="Plain-language home with one trade suggestion at a time.",
)


def pages_in_group(group: str) -> tuple[PageSpec, ...]:
    return tuple(page for page in ADVANCED_PAGES if page.group == group)


def page_by_id(page_id: str) -> PageSpec:
    for page in (SIMPLE_HOME, *ADVANCED_PAGES):
        if page.page_id == page_id:
            return page
    raise KeyError(page_id)
