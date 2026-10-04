"""Persian and right to left in the Simple view (spec A, F1, G3 phase 16)."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from pytestqt.qtbot import QtBot

from app.core.ui_prefs import Language, UiPrefs, ViewMode, load_prefs
from app.ui.i18n import PERSIAN_FAMILIES, RESTART_TEXT, persian_font
from app.ui.i18n_fa import PERSIAN
from app.ui.main_window import MainWindow

RTL = Qt.LayoutDirection.RightToLeft
LTR = Qt.LayoutDirection.LeftToRight


def make_window(qtbot: QtBot, tmp_path: Path, prefs: UiPrefs) -> MainWindow:
    window = MainWindow(prefs, tmp_path)
    qtbot.addWidget(window)
    window.show()
    return window


def test_persian_home_runs_right_to_left(qtbot: QtBot, tmp_path: Path) -> None:
    prefs = UiPrefs(language=Language.FA, onboarded=True)
    window = make_window(qtbot, tmp_path, prefs)
    assert window.layoutDirection() == RTL and window.home.layoutDirection() == RTL
    assert window.dashboard_page.layoutDirection() == LTR  # Advanced pages stay English
    assert window.statusBar().layoutDirection() == LTR
    home = window.home
    assert home.empty_title.text() == PERSIAN["No trade suggestions right now"]
    assert home.stop_button.text() == PERSIAN["Stop trading now"]
    not_connected = "Not connected to MetaTrader 5, so the app is not watching the market."
    assert home.state_line.text() == PERSIAN[not_connected]
    assert home.balance_title.text() == PERSIAN["PRACTICE BALANCE"]
    assert window.settings_button.text() == PERSIAN["Settings"]
    assert window.view_button.text() == PERSIAN["Switch to Advanced"]
    assert window.mode_badge.text() == "PAPER"  # the status bar stays English
    assert window.language_button.text() == "English"
    home.details_button.setChecked(True)
    assert home.details_button.text() == PERSIAN["Hide details"]
    qtbot.mouseClick(window.view_button, Qt.MouseButton.LeftButton)
    assert window.view_button.text() == PERSIAN["Switch to Simple"]


def test_the_first_start_in_persian(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path, UiPrefs(language=Language.FA))
    assert window.home.welcome.isVisible()
    assert window.home.practice_label.text() == PERSIAN["This is practice money, not real"]
    assert window.home.new_button.text() == PERSIAN["I'm new to trading: keep it simple"]


def test_the_language_is_saved_for_the_next_start(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path, UiPrefs(onboarded=True))
    notes: list[str] = []
    window.notify_language = notes.append
    assert window.layoutDirection() == LTR
    assert window.language_button.text() == "فارسی"
    assert window.home.stop_button.text() == "Stop trading now"
    assert window.toggle_language() is Language.FA
    assert load_prefs(tmp_path).language is Language.FA
    assert notes == [RESTART_TEXT] and window.language_button.text() == "English"
    window.toggle_theme()  # a later change keeps the saved language
    assert load_prefs(tmp_path).language is Language.FA
    window.set_view_mode(ViewMode.ADVANCED)
    assert window.toggle_language() is Language.EN
    assert load_prefs(tmp_path).language is Language.EN


def test_the_persian_font_falls_back_to_windows_fonts() -> None:
    font = persian_font(QFont())
    assert tuple(font.families()) == PERSIAN_FAMILIES
