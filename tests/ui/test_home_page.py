"""Phase 9 acceptance (spec G3 row 9): a fresh Paper-mode signal is an approvable card in
plain words with money-based results, and the Stop button is the Advanced kill switch."""

from dataclasses import replace
from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.execution_settings import ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource
from app.core.ui_prefs import UiPrefs
from app.domain.modes import OperatingMode
from app.domain.signals import SignalState
from app.engine.filters import FilterSettings
from app.engine.signal_pipeline import SignalPipeline
from app.ui.home_model import jargon_in
from app.ui.home_page import HomePage
from app.ui.main_window import MainWindow
from app.ui.positions_page import TradingContext
from app.ui.signals_page import SignalsContext
from tests.unit.execution_helpers import NOW, Rig, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store


def signals_for(r: Rig) -> SignalPipeline:
    return SignalPipeline(
        lambda: [],
        FilterSettings,
        executor=r.engine,
        utc_now=lambda: r.clock[0],
    )


def test_without_the_engine_home_is_an_honest_empty_screen(qtbot: QtBot) -> None:
    page = HomePage()
    qtbot.addWidget(page)
    page.show()
    assert page.empty_title.isVisible() and not page.approve_button.isVisible()
    assert not page.stop_button.isEnabled() and not page.stop_trading()
    assert not page.approve() and not page.skip()
    assert page.state_line.text().startswith("Not connected")
    assert jargon_in(page.default_texts()) == []


def test_a_fresh_paper_signal_is_an_approvable_plain_card(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    record = replace(eurusd_record(), volume=0.5, risk_money=50.0)
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        signals = signals_for(r)
        signals._remember(record)  # a pending signal, as `on_analysis` leaves it
        signals.on_cycle(NOW)
        source = ExecutionSettingsSource(tmp_path)  # Paper, the default
        page = HomePage(
            SignalsContext(signals, StrategySettingsSource(tmp_path)),
            None,
            TradingContext(r.engine, source, lambda: False),
        )
        qtbot.addWidget(page)
        page.now = lambda: NOW
        page.show()
        page.set_connection(True, "Status: connected to the demo account.")
        page.show_signals(signals.snapshot)
        assert page.card_title.text() == "Euro vs US dollar (EURUSD.m)"
        assert page.action_label.text() == "\u25b2 Buy"
        assert "trending up" in page.reason_label.text()
        assert page.make_label.text() == "You could make about +100.00 if it goes right"
        assert page.lose_label.text() == "You could lose about \u221250.00 if it goes wrong"
        assert page.ends_label.text() == "This suggestion ends in 30 min"
        assert page.approve_button.isEnabled() and page.skip_button.isEnabled()
        assert page.state_line.text() == "Found a trade for you."
        assert "Practice money" in page.mode_line.text()
        assert jargon_in(page.default_texts()) == []
        assert not page.details_label.isVisible()
        page.details_button.click()
        assert page.details_label.isVisible() and "stop loss" in page.details_label.text()
        page.confirm = lambda title, text: False
        assert not page.approve()
        questions: list[str] = []

        def yes(title: str, text: str) -> bool:
            questions.append(text)
            return True

        page.confirm = yes
        assert page.approve()
        assert "practice money" in questions[0]
        signals.on_cycle(NOW + 2)
        found = {item.id: item for item in signals.snapshot.signals}[record.id]
        assert found.signal.state is SignalState.MANAGED
        page.show_signals(signals.snapshot)
        page.show_trading(r.engine.snapshot)
        assert page.empty_title.isVisible() and not page.approve_button.isVisible()
        assert [row.title for row in page.rows] == ["Euro vs US dollar (EURUSD.m)"]
        assert len(page.close_buttons) == 1 and fake.trading_calls == []
        assert page.state_line.text() == "Trade running."
        assert jargon_in(page.default_texts()) == []
        row = page.rows[0]
        assert page.close_trade(row.mode, row.ticket)
        assert "Close Euro vs US dollar (EURUSD.m) now" in questions[-1]
        r.engine.cycle(NOW + 3)
        page.show_trading(r.engine.snapshot)
        assert page.rows == [] and page.no_trades.isVisible()


def test_skip_dismisses_the_suggestion(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        signals = signals_for(r)
        signals._remember(record)
        signals.on_cycle(NOW)
        page = HomePage(SignalsContext(signals, StrategySettingsSource(tmp_path)))
        qtbot.addWidget(page)
        page.now = lambda: NOW
        page.show_signals(signals.snapshot)
        assert page.suggestion is not None
        assert page.skip()
        assert page.suggestion is None
        signals.on_cycle(NOW + 2)
        found = {item.id: item for item in signals.snapshot.signals}[record.id]
        assert found.signal.state is SignalState.USER_REJECTED


def test_the_stop_button_is_the_advanced_kill_switch(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        r.engine.execute(eurusd_record(), NOW)
        r.engine.cycle(NOW + 1)
        source = ExecutionSettingsSource(tmp_path)
        trading = TradingContext(r.engine, source, lambda: False)
        window = MainWindow(UiPrefs(onboarded=True), tmp_path, trading=trading)
        qtbot.addWidget(window)
        window.show()
        home = window.home
        assert home.stop_button.isVisible() and home.stop_button.isEnabled()
        titles: list[str] = []

        def no(title: str, text: str) -> bool:
            titles.append(title)
            return False

        window.positions_page.confirm = no
        assert not home.stop_trading()
        assert titles == ["Kill switch"]  # the very same question as the Advanced view
        window.positions_page.confirm = lambda title, text: True
        assert home.stop_trading()
        r.engine.cycle(NOW + 5)
        home.set_connection(True, "Status: connected to the demo account.")
        home.show_trading(r.engine.snapshot)
        window.set_execution_status(r.engine.snapshot)
        assert r.engine.snapshot.positions == ()
        assert window.bot_state_label.text() == "Bot: stopped (kill switch)"
        assert home.state_line.text().startswith("Stopped: you pressed Stop trading")
