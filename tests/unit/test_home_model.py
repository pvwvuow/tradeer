"""The Simple view's Home screen in plain words (spec B3b, F0, G3 row 9)."""

from dataclasses import replace

from app.domain.modes import OperatingMode
from app.domain.probability import ProbabilityEstimate
from app.domain.signals import Direction, OrderType
from app.engine.execution import ExecutionSnapshot, PositionView
from app.risk.limits import RiskUsage
from app.risk.limits_state import DAILY_LIMIT, MANUAL_STOP
from app.ui.home_model import (
    MINUS,
    approve_block_text,
    approve_question,
    balance_view,
    confidence,
    home_status,
    jargon_in,
    money,
    ordered_suggestions,
    plain_reason,
    plain_title,
    price_moved,
    suggestion_view,
    trade_rows,
    week_points,
)
from tests.unit.execution_helpers import NOW, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.signal_helpers import MORNING
from tests.unit.storage_helpers import temporary_store


def usage(**changes: object) -> RiskUsage:
    values: dict[str, object] = {
        "capital": 10_000.0,
        "currency": "USD",
        "daily_loss_percent": 0.0,
        "drawdown_percent": 0.0,
        "open_risk_percent": 0.0,
        "open_trades": 0,
        "trades_today": 0,
        "exposure_percent": {},
        "margin_level": 0.0,
        "halted": "",
        "halted_reason": "",
        "day": "2026-09-30",
        "day_start_equity": 10_000.0,
        "day_start_estimated": False,
        "high_water_mark": 10_000.0,
        "balance": 10_000.0,
        "equity": 10_050.0,
    }
    values.update(changes)
    return RiskUsage(**values)  # type: ignore[arg-type]


def test_symbols_have_plain_names_next_to_the_code() -> None:
    assert plain_title("XAUUSD") == "Gold (XAUUSD)"
    assert plain_title("EURUSD.m") == "Euro vs US dollar (EURUSD.m)"
    assert plain_title("US30") == "US30"


def test_money_always_carries_a_sign_or_a_currency() -> None:
    assert money(1234.5, "USD") == "$1,234.50"
    assert money(12.3, "USD", signed=True) == "+$12.30"
    assert money(-5, "EUR") == f"{MINUS}\u20ac5.00"
    assert money(7, "CHF", signed=True) == "+7.00 CHF"


def test_a_suggestion_card_is_plain_words_and_money() -> None:
    record = replace(eurusd_record(), volume=0.05, risk_money=10.0)
    view = suggestion_view(record, "USD", NOW, total=2, strategy_title="Trend pullback")
    assert view.title == "Euro vs US dollar (EURUSD.m)"
    assert (view.action, view.arrow) == ("Buy", "\u25b2")
    assert view.reason == (
        "The Euro vs US dollar rate is trending up and just dipped back, so it may rise again."
    )
    assert view.make == "You could make about +$20.00 if it goes right"
    assert view.lose == f"You could lose about {MINUS}$10.00 if it goes wrong"
    assert view.confidence == "Confidence: not known yet"
    assert view.ends == "This suggestion ends in 30 min"
    assert view.queue == "1 of 2 suggestions"
    assert jargon_in(view.default_texts()) == []
    details = dict(view.details)
    assert details["Exit if it goes wrong (stop loss)"] == "1.08252"
    assert details["Size"].startswith("0.05 lots")
    assert details["Order"] == "Buy now at about 1.08352"
    assert "not proven" in details["Strategy"]


def test_unknown_money_and_an_ended_suggestion_say_so() -> None:
    view = suggestion_view(eurusd_record(), "USD", NOW + 3600)
    assert "not known" in view.lose and "not known" in view.make
    assert view.ends == "This suggestion has ended"


def test_breakouts_and_sells_read_plainly() -> None:
    gold = eurusd_record(
        symbol="XAUUSD",
        strategy="london_breakout",
        direction=Direction.SHORT,
        order_type=OrderType.STOP,
        entry=2380.0,
        sl=2390.0,
        tp=2365.0,
        digits=2,
    )
    text = plain_reason(gold)
    assert text.startswith("Gold stayed in a narrow range overnight. If it breaks below")
    view = suggestion_view(gold, "USD", NOW)
    assert (view.action, view.arrow) == ("Sell", "\u25bc")
    assert dict(view.details)["Order"] == "Sell when the price reaches 2,380.00"
    assert jargon_in(view.default_texts()) == []


def test_confidence_labels_follow_the_interval() -> None:
    fair = ProbabilityEstimate(0.62, 0.56, 0.68, 80, "baseline")
    middle = ProbabilityEstimate(0.52, 0.44, 0.60, 40, "baseline")
    low = ProbabilityEstimate(0.4, 0.3, 0.5, 30, "baseline")
    assert confidence(fair)[0] == "Fairly confident"
    assert confidence(middle) == (
        "Moderately confident",
        "Based on 40 earlier suggestions of this kind.",
    )
    assert confidence(low)[0] == "Low confidence"


def test_one_suggestion_at_a_time_the_most_urgent_first() -> None:
    late = eurusd_record(id="late", expires_at=float(MORNING + 3000))
    soon = eurusd_record(id="soon", expires_at=float(MORNING + 600))
    assert [r.id for r in ordered_suggestions([late, soon])] == ["soon", "late"]
    assert [r.id for r in ordered_suggestions([late, soon], skip=["soon"])] == ["late"]


def test_a_moved_price_is_noted() -> None:
    record = eurusd_record()  # entry 1.08352, 10 pips to the stop
    assert not price_moved(record, 1.08340, 1.08352, 0.25)
    assert price_moved(record, 1.08390, 1.08400, 0.25)
    assert not price_moved(record, None, None, 0.25)


def test_approval_questions_name_the_money_and_the_mode() -> None:
    view = suggestion_view(replace(eurusd_record(), risk_money=10.0), "USD", NOW)
    practice = approve_question(view, OperatingMode.PAPER)
    real = approve_question(view, OperatingMode.SEMI_AUTO)
    assert "practice money" in practice and "REAL order" in real
    assert view.lose in practice
    assert approve_block_text("", OperatingMode.PAPER) == ""
    assert "Watching only" in approve_block_text("x", OperatingMode.ANALYSIS_ONLY)


def test_the_balance_strip_shows_today_and_the_week() -> None:
    daily = [("2026-09-28", 20.0), ("2026-09-30", -5.0)]
    view = balance_view(usage(), OperatingMode.PAPER, daily, NOW)
    assert view.title == "Practice balance" and view.balance == "$10,000.00"
    assert view.today == "Today: \u25b2 +$50.00 (+0.50%)" and view.today_kind == "profit"
    assert view.week == "Last 7 days: \u25b2 +$15.00" and view.week_kind == "profit"
    assert view.points == (0.0, 0.0, 0.0, 0.0, 20.0, 20.0, 15.0)
    assert week_points([], NOW) == (0.0,) * 7
    unknown = balance_view(None, OperatingMode.SEMI_AUTO, [], NOW)
    assert unknown.title == "Balance" and unknown.today == "Today: not known yet"


def test_open_trades_show_money_and_percent_without_jargon() -> None:
    views = (
        PositionView("paper", 9, "XAUUSD", "short", 0.05, 2385.5, 2390.0, 0.0, -12.5, "x", False),
        PositionView("paper", 10, "EURUSD", "long", 0.1, 1.1, 1.09, 1.12, None, "x", True),
    )
    rows = trade_rows(ExecutionSnapshot(positions=views), 10_000.0, "USD")
    assert rows[0].title == "Gold (XAUUSD)" and rows[0].action == "\u25bc Sell"
    assert rows[0].result == f"\u25bc {MINUS}$12.50 ({MINUS}0.12%)" and rows[0].kind == "loss"
    assert rows[1].pending and rows[1].result == "Waiting for its price"
    assert jargon_in(text for row in rows for text in row.texts()) == []


def status(**changes: object) -> str:
    values: dict[str, object] = {
        "connected": True,
        "stopped": "",
        "halted": "",
        "suggestions": 0,
        "open_trades": 0,
        "market_open": True,
    }
    values.update(changes)
    return home_status(**values)  # type: ignore[arg-type]


def test_the_status_line_follows_the_same_state_as_the_advanced_view() -> None:
    assert status() == "Watching the market\u2026"
    assert "closed" in status(market_open=False)
    assert status(suggestions=1, open_trades=1) == "Found a trade for you."
    assert status(open_trades=2) == "2 trades running."
    assert "today's loss limit" in status(halted=DAILY_LIMIT, suggestions=1)
    assert status(halted=MANUAL_STOP).startswith("Paused")
    assert "Stop trading" in status(stopped="kill switch pressed", halted=DAILY_LIMIT)
    assert "Not connected" in status(connected=False, stopped="x")


def test_the_jargon_check_finds_trader_words() -> None:
    assert jargon_in(["SL at 1.1 and 2 R", "0.5 lots long"]) == ["SL", "R", "lots", "long"]
    assert jargon_in(["You could lose about $10.00 if it goes wrong"]) == []


def test_the_engine_reports_the_closed_results_of_the_last_days() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        r.engine.execute(record, NOW)
        r.engine.cycle(NOW + 1, {record.id: record})
        assert r.engine.snapshot.daily_results == ()
        fake.set_bid("EURUSD.m", 1.08560)  # through the take profit
        r.engine.cycle(NOW + 120, {record.id: record})
        [(day, net)] = r.engine.snapshot.daily_results
        assert day == "2026-09-30" and round(net, 2) == 99.5
