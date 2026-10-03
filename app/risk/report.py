"""Plain-language rows for the Risk page (spec F3 page 11). Pure, so it is tested without Qt."""

from __future__ import annotations

from app.risk.limits import RiskUsage
from app.risk.limits_state import DAILY_LIMIT, DD_LIMIT, MANUAL_STOP
from app.risk.settings import RiskSettings

ENABLE_WORD = "ENABLE"
HALT_TEXT = {
    DAILY_LIMIT: "Daily loss limit hit: no new entries until the next trading day",
    DD_LIMIT: "Drawdown limit hit: trading stopped until you re-enable it",
    MANUAL_STOP: "New entries stopped",
}


def status_text(usage: RiskUsage | None) -> str:
    if usage is None:
        return "Not connected: the limits are checked once MT5 sends the account."
    if not usage.halted:
        return "Trading allowed: no limit has stopped new entries."
    title = HALT_TEXT.get(usage.halted, "Trading stopped")
    return f"{title}. {usage.halted_reason}"


def usage_rows(usage: RiskUsage, settings: RiskSettings) -> list[tuple[str, str, str, str]]:
    """(limit, used now, allowed, note) for every limit."""
    cur = usage.currency
    estimate = " (estimated from the balance)" if usage.day_start_estimated else ""
    level = f"{usage.margin_level:,.0f}%" if usage.margin_level > 0 else "no margin used"
    return [
        (
            "Daily loss",
            f"{usage.daily_loss_percent:.2f}%",
            f"{settings.max_daily_loss_percent:g}%",
            f"since {usage.day} started at {usage.day_start_equity:,.2f} {cur}{estimate}",
        ),
        (
            "Drawdown",
            f"{usage.drawdown_percent:.2f}%",
            f"{settings.max_total_drawdown_percent:g}%",
            f"{settings.drawdown_mode}; equity high {usage.high_water_mark:,.2f} {cur}",
        ),
        (
            "Open risk",
            f"{usage.open_risk_percent:.2f}%",
            f"{settings.max_total_open_risk_percent:g}%",
            "what open positions lose at their stop losses",
        ),
        (
            "Open trades",
            str(usage.open_trades),
            str(settings.max_open_trades),
            "manual trades included" if settings.count_manual_trades else "bot trades only",
        ),
        ("Trades today", str(usage.trades_today), str(settings.max_trades_per_day), usage.day),
        (
            "Margin level",
            level,
            f">= {settings.margin_level_floor_percent:g}%",
            "checked again with each new trade's margin",
        ),
    ]


def exposure_rows(usage: RiskUsage) -> list[tuple[str, str]]:
    ordered = sorted(usage.exposure_percent.items(), key=lambda item: -abs(item[1]))
    return [
        (name, f"{'long' if value > 0 else 'short'} {abs(value):.2f}%")
        for name, value in ordered
        if abs(value) >= 0.005
    ]


def risk_in_words(usage: RiskUsage, settings: RiskSettings) -> str:
    """Simple Mode wording: one readable number instead of R and lot math (spec B3)."""
    money = usage.capital * settings.risk_per_trade_percent / 100.0
    return f"Each new trade can lose about {money:,.2f} {usage.currency} at most."
