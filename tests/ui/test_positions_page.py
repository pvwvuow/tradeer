"""Positions & Trades, the kill switch and the trading mode (spec F3 page 4, C6) with a real
execution engine on a FakeMT5."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.execution_settings import ExecutionSettingsSource
from app.core.ui_prefs import UiPrefs
from app.domain.modes import OperatingMode
from app.engine.execution import PositionView
from app.ui.main_window import MainWindow
from app.ui.positions_page import PositionsPage, TradingContext, mode_change_word, position_row
from tests.unit.execution_helpers import NOW, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store


def test_without_an_engine_the_page_is_an_honest_shell(qtbot: QtBot) -> None:
    page = PositionsPage(None)
    qtbot.addWidget(page)
    assert "not running" in page.state_label.text()
    assert not page.kill_button.isEnabled() and not page.close_button.isEnabled()
    assert not page.ask_kill() and not page.close_selected()


def test_positions_close_and_the_kill_switch(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    with temporary_store() as store, rig(fake, store) as r:
        r.engine.execute(eurusd_record(), NOW)
        r.engine.cycle(NOW + 1)
        source = ExecutionSettingsSource(tmp_path)
        page = PositionsPage(TradingContext(r.engine, source, lambda: False))
        qtbot.addWidget(page)
        page.show_snapshot(r.engine.snapshot)
        assert page.table.rowCount() == 1
        assert page.table.item(0, 2).text() == "EURUSD.m"
        page.table.selectRow(0)
        assert page.close_button.isEnabled()
        page.confirm = lambda title, text: False
        assert not page.close_selected()
        page.confirm = lambda title, text: True
        assert page.close_selected()
        r.engine.cycle(NOW + 2)
        r.engine.cycle(NOW + 3)
        page.show_snapshot(r.engine.snapshot)
        assert fake.positions == [] and page.table.rowCount() == 0
        r.engine.execute(eurusd_record(id="second"), NOW + 4)
        assert page.ask_kill()
        r.engine.cycle(NOW + 5)
        page.show_snapshot(r.engine.snapshot)
        assert fake.positions == [] and "kill switch" in page.state_label.text()
        assert page.resume_button.isEnabled()


def test_semi_auto_on_a_real_account_needs_the_typed_word(qtbot: QtBot, tmp_path: Path) -> None:
    assert mode_change_word(OperatingMode.SEMI_AUTO, True) == "REAL"
    assert mode_change_word(OperatingMode.SEMI_AUTO, False) == ""
    assert mode_change_word(OperatingMode.PAPER, True) == ""
    fake = connected()
    with temporary_store() as store, rig(fake, store) as r:
        source = ExecutionSettingsSource(tmp_path)
        page = PositionsPage(TradingContext(r.engine, source, lambda: True))
        qtbot.addWidget(page)
        page.mode.setCurrentIndex(page.mode.findData(OperatingMode.SEMI_AUTO.value))
        page.typed = lambda title, text: "real"
        assert not page.ask_mode_change() and source.mode is OperatingMode.PAPER
        page.mode.setCurrentIndex(page.mode.findData(OperatingMode.SEMI_AUTO.value))
        page.typed = lambda title, text: "REAL"
        assert page.ask_mode_change() and source.mode is OperatingMode.SEMI_AUTO
        assert not page.change_mode(OperatingMode.AUTO)
        assert page.save() and source.config.settings.max_retries == 3


def test_the_status_bar_kill_switch_works_with_a_confirmation(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    fake = connected()
    with temporary_store() as store, rig(fake, store) as r:
        r.engine.execute(eurusd_record(), NOW)
        source = ExecutionSettingsSource(tmp_path)
        trading = TradingContext(r.engine, source, lambda: False)
        window = MainWindow(UiPrefs(), tmp_path, trading=trading)
        qtbot.addWidget(window)
        assert window.kill_switch.isEnabled()
        window.positions_page.confirm = lambda title, text: False
        assert not window.ask_kill()
        window.positions_page.confirm = lambda title, text: True
        assert window.ask_kill()
        r.engine.cycle(NOW + 5)
        window.set_execution_status(r.engine.snapshot)
        assert window.bot_state_label.text() == "Bot: stopped (kill switch)"
        assert fake.positions == []


def test_position_rows_are_plain_words() -> None:
    view = PositionView("paper", 9, "XAUUSD", "short", 0.05, 2385.5, 2390.0, 0.0, -12.5, "x", False)
    row = position_row(view)
    assert row[3] == "Sell" and row[7] == "none" and row[8] == "-12.50" and row[-1] == "position"
