"""The No Curve v2 frame pieces (docs/NOCURVE_V2.md): ticker, numbered sidebar, segment, texts."""

import math
from pathlib import Path

from PySide6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from app.core.ui_prefs import Language, UiPrefs, ViewMode
from app.ui.i18n import Translator
from app.ui.main_window import MainWindow
from app.ui.shell import (
    FRAME_FA,
    GROUP_RULE_HEIGHT,
    NAV_HEIGHT,
    SIDEBAR_WIDTH,
    TICK_CYCLE_MS,
    TICK_MS,
    VIEW_CAPTION,
    DayOpens,
    GroupRule,
    KbdButton,
    Led,
    LogoMark,
    NavButton,
    Painted,
    Segmented,
    TickerStrip,
    frame_qss,
    persian_digits,
    price_text,
    ticker_symbol,
)
from app.ui.theme import DARK, LIGHT, build_qss


def test_prices_and_the_day_change_read_like_the_design() -> None:
    assert price_text(1.08543) == "1.0854"
    assert price_text(154.471) == "154.47"
    assert price_text(2314.9) == "2314.90"
    assert price_text(float("nan")) == "\u2014"
    opens = DayOpens()
    first = opens.items({"EURUSD": 1.0, "BAD": float("nan")}, 86_400.0 * 10 + 60)
    assert [item.symbol for item in first] == ["EURUSD"] and first[0].change == 0.0
    later = opens.items({"EURUSD": 1.01}, 86_400.0 * 10 + 3600)
    assert math.isclose(later[0].change, 1.0) and later[0].change_text() == "\u25b2 1.00%"
    next_day = opens.items({"EURUSD": 0.99}, 86_400.0 * 11 + 60)
    assert next_day[0].change == 0.0  # a new UTC day starts from its own first price


def test_ticker_symbols_are_written_like_the_design() -> None:
    assert ticker_symbol("EURUSD") == "EUR/USD"
    assert ticker_symbol("XAUUSD.m") == "XAU/USD.m"
    assert ticker_symbol("US30") == "US30"
    assert ticker_symbol("BTCUSDT") == "BTCUSDT"
    assert ticker_symbol("eurusd") == "eurusd"


def test_status_bar_texts_in_persian() -> None:
    frame = Translator(Language.FA, FRAME_FA)
    assert frame.text("Bot: running \u00b7 2 open") == "ربات فعال \u00b7 2 معامله‌ی باز"
    assert frame.text("Cloud: off \u00b7 3 waiting") == "همگام‌سازی ابری: خاموش \u00b7 3 در صف"
    text = frame.text("London session \u00b7 London closes in 2h 14m")
    assert text == "لندن باز \u00b7 بسته شدن لندن تا 2 س 14 د"
    assert persian_digits("2 open") == "\u06f2 open"
    assert Translator(Language.EN, FRAME_FA).text("Cloud: off") == "Cloud: off"


def test_every_frame_piece_paints_in_both_themes_and_directions(qtbot: QtBot) -> None:
    ticker = TickerStrip()
    ticker.set_items(DayOpens().items({"EURUSD": 1.0854, "XAUUSD": 2314.9}, 1e9), True)
    nav = NavButton(3, "Signals")
    nav.set_badge("2")
    nav.set_dot("warning")
    kbd = KbdButton("Search", "Ctrl K")
    danger = KbdButton("Stop trading", "Ctrl Shift K", "danger", 9.0, 24)
    pieces: list[Painted | NavButton | KbdButton] = [
        LogoMark(),
        Led(),
        GroupRule("Trade"),
        nav,
        kbd,
        danger,
        ticker,
    ]
    for widget in pieces:
        qtbot.addWidget(widget)
        widget.resize(max(widget.sizeHint().width(), 200), widget.sizeHint().height() or 30)
    for tokens in (DARK, LIGHT):
        for direction in (Qt.LayoutDirection.LeftToRight, Qt.LayoutDirection.RightToLeft):
            for widget in pieces:
                widget.setLayoutDirection(direction)
                widget.apply_tokens(tokens)
                assert not widget.grab().isNull()
    nav.setChecked(True)
    assert not nav.grab().isNull()
    assert kbd.sizeHint().width() > 60 and danger.height() == 24
    assert nav.height() == NAV_HEIGHT == 38 and GroupRule("x").height() == GROUP_RULE_HEIGHT
    ticker.step()
    assert ticker.offset == 0.0  # a hidden strip does not move


def test_the_ticker_goes_round_once_every_48_seconds(qtbot: QtBot) -> None:
    ticker = TickerStrip()
    qtbot.addWidget(ticker)
    ticker.resize(900, 30)
    ticker.set_items(DayOpens().items({"EURUSD": 1.0854, "XAUUSD": 2314.9}, 1e9), True)
    ticker.show()
    assert not ticker.grab().isNull()  # painting measures one set of quotes
    ticker.timer.stop()
    before = ticker.offset
    for _ in range(TICK_CYCLE_MS // TICK_MS):
        ticker.step()
    assert math.isclose(ticker.offset - before, ticker.cycle, rel_tol=1e-6)


def test_the_frame_stylesheet_parses(qtbot: QtBot) -> None:
    messages: list[str] = []

    def handler(kind: QtMsgType, _context: object, text: str) -> None:
        messages.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        for tokens in (DARK, LIGHT):
            for rtl in (False, True):
                root = QWidget()
                qtbot.addWidget(root)
                segment = Segmented(["Simple", "Advanced"])
                segment.setParent(root)
                root.setStyleSheet(build_qss(tokens) + frame_qss(tokens, rtl))
                root.show()
                for child in [root, *root.findChildren(QWidget)]:
                    child.ensurePolished()
    finally:
        qInstallMessageHandler(previous)
    assert not [text for text in messages if "Could not parse" in text]
    assert "border-left: 1px solid" in frame_qss(DARK, True)
    assert "border-right: 1px solid" in frame_qss(DARK, False)
    # Tags: a strong 1 px outline (the neutral one) and the design's 1 px 7 px padding.
    neutral = frame_qss(LIGHT).split('QLabel[chip="neutral"] {', 1)[1].split("}", 1)[0]
    assert f"border: 1px solid {LIGHT.border_strong};" in neutral
    assert "padding: 1px 7px;" in neutral


def test_the_window_has_the_designs_frame(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(view_mode=ViewMode.ADVANCED, onboarded=True), tmp_path)
    qtbot.addWidget(window)
    window.show()
    assert window.ticker.isVisible() and window.top_bar.height() == 52
    assert window.sidebar.width() == SIDEBAR_WIDTH == 232
    assert window.view_segment.buttons[1].isChecked()
    assert window.view_caption.text() == VIEW_CAPTION == "ADVANCED VIEW"
    assert window.view_caption.isVisible()
    margins = window.top_bar.layout().contentsMargins()
    assert (margins.left(), margins.right()) == (20, 20)
    window.set_nav_badge("signals", "2")
    window.set_nav_dot("health", "warning")
    signals = window.nav_buttons["signals"]
    assert isinstance(signals, NavButton) and signals.badge == "2"
    assert signals.height() == 38
    assert not window.grab().isNull()
    qtbot.mouseClick(window.view_segment.buttons[0], Qt.MouseButton.LeftButton)
    assert window.prefs.view_mode is ViewMode.SIMPLE and not window.ticker.isVisible()
    assert not window.view_caption.isVisible()  # the Simple header has no caption
