import threading
from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.__version__ import __version__
from app.core.ui_prefs import ThemeName, UiPrefs, ViewMode, load_prefs
from app.observability.buffer import RecentLogBuffer
from app.observability.controls import LogControls
from app.observability.crash_handler import CrashInfo, CrashTestError
from app.observability.levels import LevelRegistry
from app.ui.crash_dialog import CrashNotifier
from app.ui.logs_page import LogsPage
from app.ui.main_window import MainWindow
from app.ui.navigation import ADVANCED_PAGES, SIMPLE_HOME
from app.ui.pages import PlaceholderPage
from app.ui.theme import DARK, LIGHT


def make_window(
    qtbot: QtBot,
    tmp_path: Path,
    prefs: UiPrefs | None = None,
    with_logs: bool = False,
) -> MainWindow:
    controls = None
    if with_logs:
        controls = LogControls(
            RecentLogBuffer(),
            LevelRegistry(),
            tmp_path / "logs",
            tmp_path / "crash_reports",
        )
    window = MainWindow(prefs or UiPrefs(), tmp_path, controls)
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


def test_logs_page_is_real_when_logging_is_wired(qtbot: QtBot, tmp_path: Path) -> None:
    plain = make_window(qtbot, tmp_path)
    plain.show_page("logs")
    assert isinstance(plain.pages.currentWidget(), PlaceholderPage)
    wired = make_window(qtbot, tmp_path, with_logs=True)
    wired.show_page("logs")
    assert isinstance(wired.pages.currentWidget(), LogsPage)
    assert wired.crash_state()["page"] == "logs"


def test_crash_state_is_plain_data_that_follows_the_ui(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    qtbot.mouseClick(window.theme_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.view_button, Qt.MouseButton.LeftButton)
    assert window.crash_state() == {
        "page": "dashboard",
        "view_mode": "advanced",
        "theme": "light",
        "operating_mode": "paper",
    }


def test_the_crash_test_command_raises_inside_a_qt_slot(qtbot: QtBot, tmp_path: Path) -> None:
    prefs = UiPrefs(view_mode=ViewMode.ADVANCED)
    window = make_window(qtbot, tmp_path, prefs, with_logs=True)
    palette = window.open_command_palette()
    assert palette is not None
    qtbot.keyClicks(palette.search, "crash reporter")
    assert palette.visible_titles()[0] == "Test the crash reporter"
    with qtbot.capture_exceptions() as exceptions:
        qtbot.keyClick(palette.search, Qt.Key.Key_Return)
    assert any(isinstance(error, CrashTestError) for _, error, _ in exceptions)


def test_crash_notifications_from_any_thread_open_one_dialog(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = make_window(qtbot, tmp_path, with_logs=True)
    notifier = CrashNotifier()
    notifier.crashed.connect(window.show_crash_dialog, type=Qt.ConnectionType.QueuedConnection)
    report = tmp_path / "crash_reports" / "crash_test.json"
    info = CrashInfo(report, "test", "worker", "ValueError: boom", None, True)
    with qtbot.waitSignal(notifier.crashed, timeout=2000):
        threading.Thread(target=notifier.notify, args=(info,)).start()
    qtbot.waitUntil(lambda: window.crash_dialog is not None, timeout=2000)
    dialog = window.crash_dialog
    assert dialog is not None
    assert dialog.summary_box.toPlainText() == "ValueError: boom"
    window.show_crash_dialog(str(report), "second crash")
    assert window.crash_dialog is dialog
