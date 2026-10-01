from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.__version__ import __version__
from app.core.ui_prefs import ThemeName, UiPrefs, ViewMode, load_prefs
from app.ui.main_window import MainWindow
from app.ui.navigation import ADVANCED_PAGES, SIMPLE_HOME
from app.ui.theme import DARK, LIGHT


def make_window(qtbot: QtBot, tmp_path: Path, prefs: UiPrefs | None = None) -> MainWindow:
    window = MainWindow(prefs or UiPrefs(), tmp_path)
    qtbot.addWidget(window)
    window.show()
    return window


def test_launches_in_simple_paper_mode(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    assert __version__ in window.windowTitle()
    assert window.current_page_id() == SIMPLE_HOME.page_id
    assert window.sidebar.isHidden()
    assert window.mode_badge.text() == "PAPER"


def test_advanced_view_has_grouped_sidebar_and_navigation(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    qtbot.mouseClick(window.view_button, Qt.MouseButton.LeftButton)
    assert not window.sidebar.isHidden()
    assert window.current_page_id() == "dashboard"
    assert len(window.nav_buttons) == len(ADVANCED_PAGES)
    qtbot.mouseClick(window.nav_buttons["market"], Qt.MouseButton.LeftButton)
    assert window.current_page_id() == "market"
    assert load_prefs(tmp_path).view_mode is ViewMode.ADVANCED


def test_theme_switch_regenerates_and_persists(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    assert DARK.bg in window.styleSheet()
    qtbot.mouseClick(window.theme_button, Qt.MouseButton.LeftButton)
    assert LIGHT.bg in window.styleSheet()
    assert load_prefs(tmp_path).theme is ThemeName.LIGHT


def test_command_palette_filters_and_navigates(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path, UiPrefs(view_mode=ViewMode.ADVANCED))
    palette = window.open_command_palette()
    assert palette is not None
    qtbot.keyClicks(palette.search, "risk")
    assert palette.visible_titles()[0] == "Go to Risk"
    qtbot.keyClick(palette.search, Qt.Key.Key_Return)
    assert window.current_page_id() == "risk"


def test_command_palette_is_advanced_only(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    assert window.open_command_palette() is None


def test_stop_controls_are_visible_but_inactive(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    assert window.kill_switch.isVisible()
    assert not window.kill_switch.isEnabled()
    assert window.home.stop_button.isVisible()
    assert not window.home.stop_button.isEnabled()
