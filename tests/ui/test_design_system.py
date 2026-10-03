from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot

from app.core.ui_prefs import UiPrefs, ViewMode
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.mt5.models import AccountKind, AccountSnapshot, MarginMode
from app.ui.dashboard_page import limit_tone
from app.ui.main_window import MainWindow, connection_chip, mode_tone, page_crumb
from app.ui.pages import PageHeader, empty_state, page_header_for
from app.ui.style import chip, icons_available, set_chip, style_plots, style_tables
from app.ui.tables import make_table
from app.ui.theme import DARK, LIGHT, build_qss


def demo_account() -> AccountSnapshot:
    return AccountSnapshot(
        login=5012345,
        name="Demo",
        server="Broker-Demo",
        company="Broker Ltd",
        currency="USD",
        balance=10_000.0,
        equity=10_000.0,
        margin_free=10_000.0,
        leverage=100,
        kind=AccountKind.DEMO,
        margin_mode=MarginMode.HEDGING,
        trade_allowed=True,
        trade_expert=True,
        stopout_percent=True,
        margin_call=100.0,
        stop_out=50.0,
    )


def test_the_stylesheet_parses_without_qt_warnings(qtbot: QtBot) -> None:
    messages: list[str] = []

    def handler(kind: QtMsgType, _context: object, text: str) -> None:
        messages.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        for tokens in (DARK, LIGHT):
            root = QWidget()
            qtbot.addWidget(root)
            layout = QVBoxLayout(root)
            for widget in (
                QPushButton("ok"),
                QLineEdit(),
                QComboBox(),
                QSpinBox(),
                QCheckBox("box"),
                QProgressBar(),
                QTabWidget(),
                make_table(("A", "B")),
                chip("chip", "accent"),
            ):
                layout.addWidget(widget)
            root.setStyleSheet(build_qss(tokens))
            root.show()
            root.ensurePolished()
            for child in root.findChildren(QWidget):
                child.ensurePolished()
    finally:
        qInstallMessageHandler(previous)
    assert not [text for text in messages if "Could not parse" in text]


def test_chips_change_tone_and_reject_unknown_tones(qtbot: QtBot) -> None:
    label = chip("MT5", "neutral")
    qtbot.addWidget(label)
    set_chip(label, "Demo", "profit")
    assert label.text() == "Demo" and label.property("chip") == "profit"
    with pytest.raises(ValueError):
        set_chip(label, "x", "purple")


def test_connection_chip_and_mode_tone() -> None:
    account = demo_account()
    connected = ConnectionStatus(ConnectionState.CONNECTED, "Connected", account)
    text, tone = connection_chip(connected)
    assert text == "Demo \u00b7 5012345" and tone == "profit"
    real_account = replace(account, kind=AccountKind.REAL)
    real = ConnectionStatus(ConnectionState.CONNECTED, "ok", real_account)
    assert connection_chip(real) == (f"REAL \u00b7 {account.login}", "loss")
    assert connection_chip(ConnectionStatus()) == ("MT5 not connected", "neutral")
    assert connection_chip(ConnectionStatus(ConnectionState.FAILED))[1] == "loss"
    assert connection_chip(ConnectionStatus(ConnectionState.RECONNECTING))[1] == "warning"
    assert mode_tone("PAPER") == "accent"
    assert mode_tone("ANALYSIS-ONLY") == "warning"
    assert mode_tone("AUTO") == "loss" and mode_tone("SEMI-AUTO") == "loss"


def test_limit_bars_turn_amber_then_red() -> None:
    assert limit_tone(10.0) == "accent"
    assert limit_tone(80.0) == "warning"
    assert limit_tone(100.0) == "loss"


def test_headers_and_empty_states_say_what_the_page_is(qtbot: QtBot) -> None:
    header = page_header_for("risk")
    qtbot.addWidget(header)
    assert header.title.text() == "Risk"
    assert header.crumb.text() == "SYSTEM"
    assert "Limits" in header.subtitle.text()
    empty = empty_state("Nothing yet", "Connect MT5 first.")
    qtbot.addWidget(empty)
    assert empty.property("role") == "empty"


def test_main_window_top_bar_follows_the_page_mode_and_connection(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = MainWindow(UiPrefs(view_mode=ViewMode.ADVANCED, onboarded=True), tmp_path)
    qtbot.addWidget(window)
    window.show()
    assert window.page_crumb.text() == page_crumb("dashboard") == "TRADE / DASHBOARD"
    window.show_page("risk")
    assert window.page_crumb.text() == "SYSTEM / RISK"
    assert window.mode_chip.text() == "PAPER" and window.mode_chip.property("chip") == "accent"
    assert window.search_button.isVisible()
    status = ConnectionStatus(ConnectionState.CONNECTED, "Connected", demo_account())
    window.set_connection_status(status)
    assert window.connection_chip.property("chip") == "profit"
    assert window.connection_chip.toolTip() == status.status_bar_text()
    for button in window.nav_buttons.values():
        assert button.icon().isNull() is not icons_available()
    window.toggle_view_mode()
    assert not window.search_button.isVisible()


def test_charts_and_tables_follow_the_theme(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(view_mode=ViewMode.ADVANCED, onboarded=True), tmp_path)
    qtbot.addWidget(window)
    assert style_plots(window, LIGHT) >= 3
    assert style_tables(window) >= 3
    window.toggle_theme()
    assert LIGHT.card in window.styleSheet()


def test_every_advanced_page_gets_the_full_header(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(view_mode=ViewMode.ADVANCED, onboarded=True), tmp_path)
    qtbot.addWidget(window)
    for page_id in ("dashboard", "market", "risk", "backtest", "logs"):
        window.show_page(page_id)
        headers = window.pages.currentWidget().findChildren(PageHeader)
        assert len(headers) == 1, page_id
        assert headers[0].subtitle.text(), page_id
