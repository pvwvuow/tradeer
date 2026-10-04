"""Keyboard, screen reader names, High-DPI and never by color only (spec F1, Phase 16b)."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QShortcut
from PySide6.QtWidgets import QAbstractButton, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from app.core.ui_prefs import ThemeName, UiPrefs, ViewMode
from app.ui.analytics_page import EXCURSION_KEY, AnalyticsPage
from app.ui.dashboard_page import DOWN, UP, DashboardPage
from app.ui.main_window import KILL_SHORTCUT, SETTINGS_SHORTCUT, MainWindow
from app.ui.style import (
    GLYPH_NAMES,
    PIXEL_RATIO,
    accessible_name,
    chart_tokens,
    name_controls,
    pixel_ratio,
    unnamed_controls,
)
from app.ui.theme import DARK, LIGHT


def make_window(qtbot: QtBot, tmp_path: Path, mode: ViewMode = ViewMode.SIMPLE) -> MainWindow:
    window = MainWindow(UiPrefs(view_mode=mode, onboarded=True), tmp_path)
    qtbot.addWidget(window)
    window.show()
    return window


def test_the_simple_view_and_navigation_have_names(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path, ViewMode.ADVANCED)
    for area in (window.top_bar, window.sidebar, window.home, window.journal_page):
        assert unnamed_controls(area) == [], area.objectName()
    assert accessible_name(window.theme_button).startswith("Switch to the")


def test_arrow_and_icon_buttons_get_their_meaning(qtbot: QtBot) -> None:
    root = QWidget()
    qtbot.addWidget(root)
    previous = QPushButton("\u2190", root)
    icon_only = QPushButton("", root)
    icon_only.setToolTip("Refresh")
    plain = QPushButton("Save", root)
    assert unnamed_controls(root) == ["QPushButton"]
    assert name_controls(root) == 2
    assert previous.accessibleName() == GLYPH_NAMES["\u2190"] == "Previous"
    assert icon_only.accessibleName() == "Refresh"
    assert plain.accessibleName() == "" and accessible_name(plain) == "Save"
    assert unnamed_controls(root) == []


def test_every_visible_button_takes_keyboard_focus(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisible() and b.isEnabled()]
    assert len(buttons) >= 4
    for button in buttons:
        assert button.focusPolicy().value & Qt.FocusPolicy.TabFocus.value, button.objectName()


def test_ctrl_comma_opens_settings_in_both_views(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in window.findChildren(QShortcut)}
    assert SETTINGS_SHORTCUT in shortcuts and KILL_SHORTCUT in shortcuts
    shortcuts[SETTINGS_SHORTCUT].activated.emit()
    assert window.current_page_id() == "settings"
    window.set_view_mode(ViewMode.ADVANCED)
    assert window.current_page_id() == "dashboard"
    shortcuts[SETTINGS_SHORTCUT].activated.emit()
    assert window.current_page_id() == "settings"


def test_high_dpi_scaling_and_sharp_icons(qtbot: QtBot) -> None:
    policy = QGuiApplication.highDpiScaleFactorRoundingPolicy()
    assert policy == Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    assert pixel_ratio() >= PIXEL_RATIO


def test_charts_take_the_theme_colors(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    window.apply_theme(ThemeName.LIGHT)
    assert chart_tokens() is LIGHT
    window.apply_theme(ThemeName.DARK)
    assert chart_tokens() is DARK


def test_results_carry_an_arrow_and_wins_a_shape(qtbot: QtBot) -> None:
    page = DashboardPage(None)
    qtbot.addWidget(page)
    page.timer.stop()
    kpi = page.kpis["Today"]
    kpi.set_text("+5.00", 5.0)
    assert kpi.value.text() == f"{UP} +5.00" and kpi.value.property("role") == "kpi_profit"
    kpi.set_text("-2.00", -2.0)
    assert kpi.value.text() == f"{DOWN} -2.00" and kpi.value.property("role") == "kpi_loss"
    kpi.set_text("0.00", 0.0)
    assert kpi.value.text() == "0.00" and kpi.value.property("role") == "kpi"
    analytics = AnalyticsPage(None)
    qtbot.addWidget(analytics)
    assert analytics.excursion_plot.getPlotItem().titleLabel.text == EXCURSION_KEY
