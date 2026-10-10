"""The Signal desk's context lines (docs/SIGNAL_DESK.md 2.5, phase 21b2)."""

from dataclasses import replace

from app.calendar.models import CalendarEvent, Impact
from app.domain.signals import Direction, SignalRecord, SignalState
from app.engine.desk_context import currency_names, desk_context, news_line, trades_line
from app.engine.signal_desk import new_request
from app.signals.parse import parse
from tests.unit.strategy_helpers import analysis_at, london_days
from tests.unit.test_signal_desk import buy_text, watching


def legs() -> tuple[list[SignalRecord], float]:
    """The two waiting legs of a pasted EURUSD buy, and the moment."""
    signals, now, price = watching()
    signals.submit(new_request(parse(buy_text(price), ("EURUSD",)), now))
    signals.on_cycle(now + 1)
    return list(signals.snapshot.signals), now


def test_the_context_starts_with_the_analysis_card() -> None:
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    lines = desk_context(
        "EURUSD",
        Direction.LONG,
        analysis=analysis,
        events=(),
        records=(),
        now=now,
    )
    assert lines[0] == f"Now: {analysis.card.headline}"
    assert lines[-1] == "No open trades on EUR or USD."
    assert lines[-2].startswith(("Next high-impact news: ", "No high-impact news for EUR"))
    empty = desk_context("EURUSD", None, analysis=None, events=(), records=(), now=now)
    assert empty[0] == "No live analysis of EURUSD yet."


def test_the_next_high_impact_news_of_either_currency() -> None:
    now = 1_791_331_200.0
    events = [
        CalendarEvent(int(now) - 3600, "USD", Impact.HIGH, "NFP"),  # over
        CalendarEvent(int(now) + 600, "USD", Impact.MEDIUM, "Claims"),
        CalendarEvent(int(now) + 60, "JPY", Impact.HIGH, "BoJ"),  # another currency
        CalendarEvent(int(now) + 7200, "USD", Impact.HIGH, "CPI"),
    ]
    assert news_line(events, "EURUSD", now) == "Next high-impact news: USD CPI in 2h 00m."
    assert news_line(events[:3], "EURUSD", now).startswith("No high-impact news for EUR or USD")


def test_open_trades_on_the_same_currencies_and_the_same_bet() -> None:
    records, _now = legs()
    first, second = records[0], records[1]
    gbp = replace(first, signal=replace(first.signal, symbol="GBPUSD", state=SignalState.FILLED))
    text = trades_line("EURUSD", Direction.LONG, [gbp, second])
    trade = f"GBPUSD buy ({gbp.signal.strategy}), the same bet: short USD"
    assert text == f"Open trades on EUR or USD: {trade}."
    alone = trades_line("EURUSD", Direction.LONG, [gbp], own=[gbp.id])
    assert alone == "No open trades on EUR or USD."
    jpy = replace(gbp, signal=replace(gbp.signal, symbol="AUDJPY"))
    assert trades_line("EURUSD", Direction.SHORT, [jpy]) == "No open trades on EUR or USD."
    assert currency_names("XAUUSD") == "XAU or USD" and currency_names("US30") == "US30"
