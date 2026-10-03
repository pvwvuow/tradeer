"""Closed trades for the analytics tests (hand-checked numbers)."""

from __future__ import annotations

from dataclasses import replace

from app.analytics.trades import TradeRecord

DAY = 86_400.0
HOUR = 3_600.0
START = 1_790_812_800.0  # 2026-10-01 00:00 UTC, a Thursday

BASE = TradeRecord(
    id="t0",
    account="acc",
    mode="live",
    source="bot",
    symbol="EURUSD",
    direction="buy",
    volume=0.1,
    open_time=START,
    close_time=START + HOUR,
    open_price=1.1,
    close_price=1.102,
    profit=0.0,
    commission=0.0,
    swap=0.0,
    fee=0.0,
    net_profit=0.0,
    r_multiple=None,
    risk_money=50.0,
    mfe_r=None,
    mae_r=None,
    probability=None,
    session="London",
    strategy="trend_pullback",
    config="cfg1",
    signal_id="",
    exit_reason="take profit",
)


def trade(index: int, net: float, *, r: float | None = None, **changes: object) -> TradeRecord:
    """Trade `index` opens `index` hours after START and closes 30 minutes later."""
    opened = START + index * HOUR
    values: dict[str, object] = {
        "id": f"t{index}",
        "open_time": opened,
        "close_time": opened + 1800.0,
        "profit": net,
        "net_profit": net,
        "r_multiple": r if r is not None else net / 50.0,
    }
    values.update(changes)
    return replace(BASE, **values)  # type: ignore[arg-type]


def sample() -> list[TradeRecord]:
    """+100, -50, -50, +150, -25: net +125 on a 1,000 start balance."""
    return [trade(0, 100), trade(1, -50), trade(2, -50), trade(3, 150), trade(4, -25)]
