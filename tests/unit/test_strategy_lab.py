"""The strategy lab review (7 October 2026): every strategy's numbers side by side with its
strong and weak spots, in the daily and weekly reports."""

from __future__ import annotations

from datetime import date

from app.analytics.lab import VERDICT_TRADES, review_lines, review_strategies
from app.analytics.trades import TradeRecord
from app.journal.reports import build_report, day_period

START = 1_790_812_800.0  # Thursday 2026-10-01 00:00 UTC


def made(
    number: int,
    strategy: str,
    net: float,
    *,
    symbol: str = "EURUSD",
    session: str = "London",
    exit_reason: str = "take profit",
    mfe_r: float | None = None,
    mae_r: float | None = None,
) -> TradeRecord:
    close = START + 3600 + number * 600
    return TradeRecord(
        id=f"t{number}",
        account="demo",
        mode="live",
        source="bot",
        symbol=symbol,
        direction="buy",
        volume=0.01,
        open_time=close - 1800,
        close_time=close,
        open_price=1.1,
        close_price=1.1,
        profit=net,
        commission=0.0,
        swap=0.0,
        fee=0.0,
        net_profit=net,
        r_multiple=net / 10.0,
        risk_money=10.0,
        mfe_r=mfe_r,
        mae_r=mae_r,
        probability=None,
        session=session,
        strategy=strategy,
        config="",
        signal_id="",
        exit_reason=exit_reason,
    )


def lab() -> list[TradeRecord]:
    trades = [made(n, "ema_momentum", 15.0, mae_r=-0.2) for n in range(6)]
    trades += [
        made(10 + n, "ema_momentum", -10.0, symbol="GBPUSD", session="New York", mfe_r=1.2)
        for n in range(4)
    ]
    trades += [made(20 + n, "range_reversion", -8.0, exit_reason="stop loss") for n in range(5)]
    trades.append(made(30, "manual", 5.0))
    return trades


def test_each_strategy_gets_its_numbers_strengths_and_weaknesses() -> None:
    reviews = review_strategies(lab())
    assert [item.strategy for item in reviews] == ["ema_momentum", "range_reversion"]
    best, worst = reviews
    assert best.trades == 10 and best.wins == 6 and best.net_profit == 50.0
    assert best.profit_factor == 2.25 and best.expectancy_r == 0.5
    assert any(text.startswith("best symbol: EURUSD") for text in best.strengths)
    assert any(text.startswith("worst session: New York") for text in best.weaknesses)
    assert any("4 of 4 losers were 1 R or more in profit" in text for text in best.weaknesses)
    assert any("clean entries" in text for text in best.strengths)
    assert worst.weaknesses[0] == "100% of the trades end at the stop loss"
    assert not best.judged and f"too few to judge (10/{VERDICT_TRADES})" in best.line()


def test_the_review_is_in_the_report() -> None:
    period = day_period(date(2026, 10, 1))
    report = build_report(period, "demo", lab(), currency="USD")
    assert "- By strategy (best expectancy first; + strong, - weak):" in report.text
    assert "  - ema_momentum: 10 trade(s), win rate 60%, net +50.00 USD" in report.text
    assert "    - - 100% of the trades end at the stop loss" in report.text
    names = [row["strategy"] for row in report.summary["strategies"]]
    assert names == ["ema_momentum", "range_reversion"]
    assert review_lines([]) == []
    empty = build_report(period, "demo", [], currency="USD")
    assert "By strategy" not in empty.text and empty.summary["strategies"] == []
