from datetime import UTC, datetime
from typing import Any

from app.analysis.bars import TF_SECONDS, Bars
from app.analysis.card import NOT_A_SIGNAL, Verdict
from app.analysis.symbol import Tick, analyze_symbol
from app.calendar.models import CalendarEvent, Impact
from app.core.clock import BrokerClock, DstScheme
from tests.unit.analysis_helpers import bars_from_closes, trending

NOW = int(datetime(2026, 10, 1, 10, 2, tzinfo=UTC).timestamp())
CLOCK = BrokerClock(2.0, DstScheme.US, measured=True)


def market(drift: float) -> dict[str, Bars]:
    found = {}
    for index, (timeframe, count) in enumerate(
        (("M5", 600), ("M15", 600), ("H1", 600), ("H4", 400), ("D1", 300)),
    ):
        step = TF_SECONDS[timeframe]
        end = NOW // step * step
        closes = trending(count, drift * step / 3600, seed=index + 1)
        bars = bars_from_closes(closes, timeframe, start=end - step * count, wick=0.0003)
        found[timeframe] = bars
    return found


def analyse(drift: float, events: tuple[CalendarEvent, ...] = (), tick_age: float = 1.0) -> Any:
    bars = market(drift)
    price = bars["M5"].last_close
    tick = Tick(price, price + 0.0001, NOW - tick_age, 0.00001, 5)
    return analyze_symbol(
        "EURUSD",
        bars,
        now=NOW,
        clock=CLOCK,
        tick=tick,
        digits=5,
        events=events,
    )


def test_an_uptrend_card_says_watch_and_never_signals() -> None:
    analysis = analyse(0.0004)
    card = analysis.card
    assert analysis.trend.bias > 25
    assert card.verdict is Verdict.WATCH
    assert card.headline.startswith("EURUSD: H4 uptrend")
    assert "London + New York overlap" not in card.headline
    assert "London session" in card.headline
    assert card.text().endswith(NOT_A_SIGNAL)
    assert any(line.startswith("Trend: M5") for line in card.lines)
    assert analysis.bar_time == NOW // 300 * 300 - 300


def test_high_impact_news_soon_means_wait() -> None:
    cpi = CalendarEvent(NOW + 40 * 60, "USD", Impact.HIGH, "CPI m/m")
    card = analyse(0.0004, (cpi,)).card
    assert card.verdict is Verdict.WAIT
    assert "USD CPI m/m in 40 min" in card.headline
    assert card.headline.endswith("\u2192 wait")
    other = CalendarEvent(NOW + 40 * 60, "JPY", Impact.HIGH, "BoJ rate")
    assert analyse(0.0004, (other,)).card.verdict is Verdict.WATCH


def test_bad_data_skips_the_evaluation() -> None:
    card = analyse(0.0004, tick_age=1200).card
    assert card.verdict is Verdict.DATA_PROBLEM
    assert "stale" in card.reason


def test_a_flat_market_has_no_clear_direction() -> None:
    assert analyse(0.0).card.verdict in (Verdict.UNCLEAR, Verdict.WATCH)
    assert analyse(0.0).card.reason
