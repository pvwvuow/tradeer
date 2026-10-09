"""7 October 2026 polish: themed title bar, page fade, hand cursors and the chart's price axis."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from app.core.ui_prefs import UiPrefs, ViewMode
from app.ui.chart import CandleChart
from app.ui.main_window import MainWindow
from app.ui.motion import can_fade, fade_in
from app.ui.style import hand_cursors
from app.ui.theme import DARK, LIGHT
from app.ui.window_chrome import (
    DWMWA_CAPTION_COLOR,
    DWMWA_USE_IMMERSIVE_DARK_MODE,
    colorref,
    style_title_bar,
    title_bar_attributes,
)


def test_title_bar_colors_follow_the_theme() -> None:
    assert colorref("#0B0D12") == 0x120D0B
    assert colorref("#FFFFFF") == 0xFFFFFF
    dark = dict(title_bar_attributes(DARK))
    light = dict(title_bar_attributes(LIGHT))
    assert dark[DWMWA_USE_IMMERSIVE_DARK_MODE] == 1
    assert light[DWMWA_USE_IMMERSIVE_DARK_MODE] == 0
    # No Curve v2: the header is the page color, and so is the title bar above it.
    assert dark[DWMWA_CAPTION_COLOR] == colorref(DARK.bg)
    assert light[DWMWA_CAPTION_COLOR] == colorref(LIGHT.bg)


def test_title_bar_is_left_alone_off_windows(qtbot: QtBot) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    assert style_title_bar(widget, DARK) == 0


def test_a_page_fades_in_and_the_effect_goes_away(qtbot: QtBot) -> None:
    page = QWidget()
    qtbot.addWidget(page)
    QVBoxLayout(page).addWidget(QLabel("hello"))
    assert fade_in(page) is None  # hidden pages are not animated
    page.show()
    assert can_fade(page)
    assert fade_in(page) is not None
    assert page.graphicsEffect() is not None
    qtbot.waitUntil(lambda: page.graphicsEffect() is None, timeout=2000)


def test_pages_with_a_chart_do_not_fade(qtbot: QtBot) -> None:
    page = QWidget()
    qtbot.addWidget(page)
    QVBoxLayout(page).addWidget(CandleChart())
    page.show()
    assert not can_fade(page)
    assert fade_in(page) is None


def test_buttons_get_a_hand_cursor(qtbot: QtBot) -> None:
    root = QWidget()
    qtbot.addWidget(root)
    button = QPushButton("Go", root)
    assert hand_cursors(root) >= 1
    assert button.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_the_candle_chart_has_its_prices_on_the_right(qtbot: QtBot) -> None:
    chart = CandleChart()
    qtbot.addWidget(chart)
    assert chart.plot.getAxis("right").isVisible()
    assert not chart.plot.getAxis("left").isVisible()


def test_switching_pages_still_works_with_the_fade(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(view_mode=ViewMode.ADVANCED, onboarded=True), tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.show_page("risk")
    assert window.current_page_id() == "risk"
    window.show_page("logs")
    assert window.current_page_id() == "logs"
    current = window.pages.currentWidget()
    assert current is not None
    qtbot.waitUntil(lambda: current.graphicsEffect() is None, timeout=2000)
    assert window.nav_buttons["risk"].cursor().shape() == Qt.CursorShape.PointingHandCursor
