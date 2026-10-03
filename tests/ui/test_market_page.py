"""Market page (spec F3) with real analysis snapshots from FakeMT5 through the gateway."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.calendar.exporter import CalendarFileWatcher
from app.calendar.models import CalendarEvent, Impact
from app.calendar.store import CalendarStore
from app.core.clock import BrokerClock
from app.core.ui_prefs import UiPrefs
from app.core.watchlist import load_watchlist
from app.engine.market_watch import MarketSnapshot, MarketWatch
from app.mt5.gateway import MT5Gateway
from app.mt5.market_data import MarketData
from app.storage.migrate import migrate
from app.storage.repositories import Store
from app.storage.sqlite_db import Database, database_path
from app.ui.main_window import MainWindow
from app.ui.market_page import MarketContext, MarketPage
from tests.fakes.fake_mt5 import FakeMT5

START = datetime(2026, 10, 1, 10, 2, 30, tzinfo=UTC).timestamp()


@pytest.fixture
def context(tmp_path: Path) -> Iterator[MarketContext]:
    fake = FakeMT5(now=lambda: START)
    fake.initialize()
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    db = Database(database_path(tmp_path))
    migrate(db)
    calendar = CalendarStore(Store(db))
    calendar.save([CalendarEvent(int(START) + 5400, "USD", Impact.HIGH, "CPI m/m")])
    data = MarketData(gateway, BrokerClock.assumed(), utc_now=lambda: START)
    watch = MarketWatch(
        data,
        symbols=lambda: load_watchlist(tmp_path).symbols,
        connected=lambda: True,
        events=lambda now: calendar.recent_and_upcoming(now),
        utc_now=lambda: START,
    )
    try:
        yield MarketContext(
            watch=watch,
            profile_dir=tmp_path,
            calendar=calendar,
            calendar_file=CalendarFileWatcher(tmp_path / "tradeer_calendar.csv"),
            data_path=lambda: str(tmp_path / "terminal"),
        )
    finally:
        gateway.stop()
        db.close()


def test_three_cards_fill_from_a_snapshot(qtbot: QtBot, context: MarketContext) -> None:
    page = MarketPage(context)
    qtbot.addWidget(page)
    assert sorted(page.cards) == ["EURUSD", "GBPUSD", "XAUUSD"]
    snapshot = context.watch.cycle()
    page.show_snapshot(snapshot)
    for name, card in page.cards.items():
        assert card.headline.text().startswith(f"{name}: ")
        assert "not a trade signal" in card.details.text()
    assert page.matrix.rowCount() == 3
    assert page.strength.rowCount() >= 4
    assert page.correlation.rowCount() == 3
    assert page.chart.item_count > 0
    page.chart.set_timeframe("M5")
    assert page.chart.timeframe == "M5" and page.chart.item_count > 0
    page.chart.show_sessions.setChecked(False)
    assert "closed bars" in page.chart.info.text()
    assert page.next_news_text(START).startswith("Next news: USD CPI m/m in 1h 30m")


def test_cards_show_loading_and_follow_a_new_evaluation_of_the_same_bar(
    qtbot: QtBot,
    context: MarketContext,
) -> None:
    page = MarketPage(context)
    qtbot.addWidget(page)
    why = "the newest M5 bar is 2 h older than the price"
    page.show_snapshot(MarketSnapshot("running", "Analysing", loading={"XAUUSD": why}))
    gold = page.cards["XAUUSD"]
    assert gold.verdict.text() == "LOADING"
    assert gold.headline.text() == f"MT5 is still loading the newest bars ({why}). The card waits."
    first = context.watch.cycle()
    page.show_snapshot(first)
    euro = page.cards["EURUSD"]
    assert euro.analysis is first.analyses["EURUSD"] and euro.verdict.text() != "WAIT"
    assert gold.verdict.text() != "LOADING"
    # News within the hour: the same bar is judged again, and the card follows at once.
    assert context.calendar is not None
    context.calendar.save([CalendarEvent(int(START) + 1200, "USD", Impact.HIGH, "NFP")])
    context.watch.refresh()
    again = context.watch.cycle()
    page.show_snapshot(again)
    assert again.analyses["EURUSD"] is not first.analyses["EURUSD"]
    assert euro.analysis is again.analyses["EURUSD"]
    assert euro.verdict.text() == "WAIT"


def test_the_watchlist_is_saved_and_the_cards_follow(qtbot: QtBot, context: MarketContext) -> None:
    page = MarketPage(context)
    qtbot.addWidget(page)
    page.watchlist_edit.setText("eurusd, usdjpy; EURUSD")
    qtbot.mouseClick(page.watchlist_button, Qt.MouseButton.LeftButton)
    assert load_watchlist(context.profile_dir).symbols == ["EURUSD", "USDJPY"]
    assert sorted(page.cards) == ["EURUSD", "USDJPY"]
    snapshot = context.watch.cycle()
    page.show_snapshot(snapshot)
    assert page.cards["USDJPY"].headline.text().startswith("USDJPY: ")


def test_calendar_events_can_be_added_and_the_exporter_installed(
    qtbot: QtBot,
    context: MarketContext,
) -> None:
    page = MarketPage(context)
    qtbot.addWidget(page)
    page.event_currency.setText("eur")
    page.event_title.setText("ECB rate decision")
    qtbot.mouseClick(page.add_event_button, Qt.MouseButton.LeftButton)
    assert "Added: EUR ECB rate decision" in page.calendar_status.text()
    page.install_button.click()
    installed = context.profile_dir / "terminal" / "MQL5" / "Services" / "CalendarExporter.mq5"
    assert installed.is_file()
    csv_file = context.profile_dir / "events.csv"
    csv_file.write_text("time,currency,impact,title\n2030-01-02 13:30,USD,high,NFP\n", "utf-8")
    page.import_csv(csv_file)
    done = "events.csv: 1 events read"
    qtbot.waitUntil(lambda: done in page.calendar_status.text(), timeout=5000)


def test_the_status_bar_shows_the_session_clock(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(), tmp_path)
    qtbot.addWidget(window)
    window.update_session_clock(START)
    clock = window.session_clock_label.text()
    assert clock.startswith("London session \u00b7 New York opens in 1h 57m")
    assert window.news_label.text() == "Next news: none in 48h"
    window.show_page("market")
    assert window.current_page_id() == "market"
