"""Log categories (spec E3). Each category has its own files and its own runtime level."""

from __future__ import annotations

from enum import StrEnum


class LogCategory(StrEnum):
    APP = "app"
    MT5 = "mt5"
    MARKET_DATA = "market_data"
    ANALYSIS = "analysis"
    STRATEGY = "strategy"
    ML = "ml"
    RISK = "risk"
    EXECUTION = "execution"
    POSITION = "position"
    SYNC = "sync"
    BACKTEST = "backtest"
    UI = "ui"
    NOTIFY = "notify"
    LLM = "llm"
    AUDIT = "audit"
    PERF = "perf"
    # Not in the E3 list, but required by spec J4 for the in-app updater.
    UPDATE = "update"


DEFAULT_CATEGORY = LogCategory.APP


def parse_category(value: object) -> LogCategory:
    """Map any value to a known category. Unknown values fall back to `app`."""
    try:
        return LogCategory(str(value))
    except ValueError:
        return DEFAULT_CATEGORY
