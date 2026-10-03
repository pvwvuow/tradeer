"""Journal and reports (spec C12): the trade story, notes and ratings, and the daily and
weekly reports with hand-checked numbers."""

from datetime import date
from pathlib import Path

import pytest

from app.analytics.trades import load_trades
from app.journal.narrative import TradeEvent, narrative
from app.journal.reports import ReportRepository, build_report, day_period, due_reports, week_period
from app.journal.scheduler import ReportService
from app.journal.store import JournalEntry, JournalError, JournalRepository, parse_tags
from app.notify.events import Notice
from app.storage.signal_store import iso_time
from app.strategies.registry import MAGIC_NUMBERS, strategy_for_magic
from tests.unit.analytics_helpers import HOUR, START, sample, trade
from tests.unit.storage_helpers import temporary_store

OFFSET = 3 * HOUR  # a UTC+3 broker


def test_the_story_of_a_trade_in_plain_words() -> None:
    record = trade(
        0,
        100,
        r=2.0,
        reason="H1 uptrend, pullback to EMA20",
        sl=1.099,
        tp=1.102,
        probability=0.62,
        mfe_r=2.4,
        mae_r=-0.3,
        commission=-1.5,
    )
    event = TradeEvent(START + 600, "modify_sl", "1.099", "1.1", "break-even at 1 R")
    story = narrative(record, [event])
    assert story.startswith("Bought 0.1 lots of EURUSD at 1.1 on 2026-10-01 00:00 UTC")
    assert "Why: H1 uptrend, pullback to EMA20." in story
    assert "Stop loss 1.099, take profit 1.102, risking 50.00." in story
    assert "chance to win was 62%" in story
    assert "moved the stop loss from 1.099 to 1.1 (break-even at 1 R)" in story
    assert "after 30 min and won +100.00 (+2.00 R)" in story
    manual = narrative(trade(1, -20, source="manual", direction="sell"))
    assert manual.startswith("Sold") and "You (a manual trade) took it" in manual
    assert "lost -20.00" in manual


def test_journal_entries_round_trip_and_check_their_values() -> None:
    with temporary_store() as store:
        journal = JournalRepository(store, account=lambda: "acc")
        tags = parse_tags("A+, news; a+")
        entry = JournalEntry("t1", "story", "waited for the close", tags, 4, "calm")
        assert entry.tags == ("a+", "news")
        assert journal.save(entry)
        assert not journal.save(entry)  # unchanged: nothing to upload
        again = journal.entry("t1")
        assert again == entry
        assert journal.tagged() == {"t1": ("a+", "news")}
        with pytest.raises(JournalError):
            journal.save(JournalEntry("t2", rating=6))
        with pytest.raises(JournalError):
            journal.save(JournalEntry("t2", emotion="angry"))
        assert store.count("journal") == 1


def save_trade(store: object, record_id: str, net: float, close: float, **extra: object) -> None:
    row = {
        "id": record_id,
        "account_id": "acc",
        "mode": "live",
        "source": "bot",
        "symbol": "EURUSD",
        "direction": "buy",
        "volume": 0.1,
        "open_time": iso_time(close - 1800),
        "close_time": iso_time(close),
        "profit": net,
        "commission": 0.0,
        "swap": 0.0,
        "net_profit": net,
        "magic": MAGIC_NUMBERS["london_breakout"],
    }
    row.update(extra)
    store.upsert("trades", row)  # type: ignore[attr-defined]


def test_trades_load_with_their_strategy() -> None:
    with temporary_store() as store:
        save_trade(store, "a", 10.0, START + HOUR, slippage=1.5)
        save_trade(store, "b", -5.0, START + 2 * HOUR, source="manual", magic=0)
        store.upsert("trades", {"id": "open", "account_id": "acc", "symbol": "EURUSD"})
        loaded = load_trades(store.db, strategy_for_magic)
        assert [t.id for t in loaded] == ["a", "b"]
        assert loaded[0].strategy == "london_breakout" and loaded[0].slippage == 1.5
        assert loaded[1].strategy == "manual"
        assert loaded[0].close_time == pytest.approx(START + HOUR)


def test_a_daily_report_by_hand() -> None:
    period = day_period(date(2026, 10, 1), OFFSET)
    assert period.start == START - OFFSET
    report = build_report(
        period,
        "acc",
        [*sample(), trade(30, 999)],  # the last one closes the next day
        rejected=[("spread too wide", 3), ("news blackout", 1)],
        log_counts=[("ERROR", 1), ("WARNING", 4)],
        requotes=6,
        currency="USD",
    )
    summary = report.summary
    assert summary["net_profit"] == 125.0 and summary["trades"] == 5
    assert summary["win_rate"] == pytest.approx(0.4)
    assert summary["best_trade"].startswith("EURUSD buy +150.00")
    assert summary["worst_trade"].startswith("EURUSD buy -50.00")
    assert summary["rejected"] == {"spread too wide": 3, "news blackout": 1}
    assert summary["anomalies"] == ["6 requotes or price changes from the broker"]
    assert "- Net result: +125.00 USD from 5 closed trade(s), win rate 40%" in report.text
    assert "spread too wide (3)" in report.text


def test_reports_are_due_once_per_day_and_week() -> None:
    saturday = START + 2 * 86_400 + 10 * HOUR  # 2026-10-03 10:00 UTC
    done: set[tuple[str, date]] = set()
    found = due_reports(saturday, OFFSET, lambda kind, first: (kind, first) in done)
    assert [(p.kind, p.first) for p in found] == [
        ("daily", date(2026, 10, 2)),
        ("weekly", date(2026, 9, 21)),
    ]
    done.update((p.kind, p.first) for p in found)
    assert due_reports(saturday, OFFSET, lambda kind, first: (kind, first) in done) == []
    sunday = saturday + 86_400  # yesterday was a Saturday: no daily report
    assert [p.kind for p in due_reports(sunday, OFFSET, lambda k, f: k == "weekly")] == []
    assert week_period(date(2026, 9, 28)).end - week_period(date(2026, 9, 28)).start == 7 * 86_400


def test_the_service_saves_and_sends_the_missing_reports(tmp_path: Path) -> None:
    with temporary_store() as store:
        save_trade(store, "a", 40.0, START + 10 * HOUR)
        store.upsert(
            "signals",
            {
                "id": "s1",
                "bar_time": iso_time(START + 9 * HOUR),
                "symbol": "EURUSD",
                "state": "FILTERED_OUT",
                "reject_reason": "news blackout",
            },
        )
        sent: list[Notice] = []
        service = ReportService(
            ReportRepository(store, tmp_path / "reports"),
            lambda: load_trades(store.db, strategy_for_magic),
            account=lambda: "acc",
            offset=lambda: 0.0,
            notify=sent.append,
            clock=lambda: START + 86_400 + HOUR,  # Friday 2 October, 01:00 UTC
        )
        made = service.run_due()
        daily = next(r for r in made if r.period.kind == "daily")
        assert daily.summary["net_profit"] == 40.0
        assert daily.summary["rejected"] == {"news blackout": 1}
        assert (tmp_path / "reports" / "daily_2026-10-01.md").exists()
        assert service.run_due() == []
        assert len(sent) == len(made) and sent[0].text.startswith("# Day 2026-10-01")
        recent = ReportRepository(store).recent()
        assert {r["kind"] for r in recent} == {"daily", "weekly"}
