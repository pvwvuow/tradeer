from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.controls import LogControls
from app.observability.levels import LevelRegistry, LogLevel
from app.ui.logs_page import MESSAGE_COLUMN, LogsPage
from app.ui.qt_logging import qt_level


def entry(
    message: str,
    category: str = "app",
    level: str = "INFO",
    **fields: Any,
) -> dict[str, Any]:
    return {
        "time": "2026-10-01T09:30:00.000Z",
        "level": level,
        "category": category,
        "message": message,
        **fields,
    }


def make_page(qtbot: QtBot, tmp_path: Path) -> tuple[LogsPage, LogControls, list[str]]:
    actions: list[str] = []
    controls = LogControls(
        RecentLogBuffer(),
        LevelRegistry(),
        tmp_path / "logs",
        tmp_path / "crash_reports",
        audit=lambda action, before, after: actions.append(action),
    )
    page = LogsPage(controls)
    qtbot.addWidget(page)
    page.show()
    return page, controls, actions


def messages(page: LogsPage) -> list[str]:
    model = page.model
    return [model.data(model.index(row, MESSAGE_COLUMN)) for row in range(model.rowCount())]


def test_the_tail_shows_new_lines(qtbot: QtBot, tmp_path: Path) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    controls.buffer.append(entry("first"))
    controls.buffer.append(entry("second", level="ERROR"))
    page.refresh()
    assert messages(page) == ["first", "second"]
    controls.buffer.append(entry("third"))
    qtbot.waitUntil(lambda: page.model.rowCount() == 3, timeout=2000)


def test_category_level_and_text_filters(qtbot: QtBot, tmp_path: Path) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    controls.buffer.append(entry("Connected", category="mt5", symbol="EURUSD"))
    controls.buffer.append(entry("Spread high", category="risk", level="WARNING"))
    controls.buffer.append(entry("tick", category="mt5", level="DEBUG"))
    page.reload()
    page.category_box.setCurrentIndex(page.category_box.findData("mt5"))
    assert messages(page) == ["Connected", "tick"]
    page.category_box.setCurrentIndex(0)
    page.level_box.setCurrentIndex(page.level_box.findData(int(LogLevel.WARNING)))
    assert messages(page) == ["Spread high"]
    page.level_box.setCurrentIndex(0)
    page.search_box.setText("eurusd")
    assert messages(page) == ["Connected"]
    page.regex_box.setChecked(True)
    page.search_box.setText("(")
    assert page.status_label.text().startswith("Invalid pattern")


def test_levels_and_debug_mode_can_be_changed_and_are_audited(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, controls, actions = make_page(qtbot, tmp_path)
    assert not page.category_level_box.isEnabled()
    page.category_box.setCurrentIndex(page.category_box.findData("mt5"))
    assert page.category_level_box.isEnabled()
    page.category_level_box.setCurrentIndex(page.category_level_box.findData(int(LogLevel.DEBUG)))
    assert controls.registry.level(LogCategory.MT5) is LogLevel.DEBUG
    qtbot.mouseClick(page.debug_button, Qt.MouseButton.LeftButton)
    assert controls.debug_state().active
    assert "min left" in page.debug_label.text()
    qtbot.mouseClick(page.debug_button, Qt.MouseButton.LeftButton)
    assert not controls.debug_state().active
    assert actions == ["log_level_changed", "debug_mode_enabled", "debug_mode_disabled"]


def test_selecting_a_line_shows_its_json_and_can_isolate_its_trace(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    controls.buffer.append(entry("signal found", trace_id="trace-a"))
    controls.buffer.append(entry("other work", trace_id="trace-b"))
    controls.buffer.append(entry("order sent", trace_id="trace-a"))
    page.reload()
    page.table.setCurrentIndex(page.model.index(0, 0))
    assert '"trace_id": "trace-a"' in page.detail.toPlainText()
    assert page.trace_button.isEnabled()
    qtbot.mouseClick(page.trace_button, Qt.MouseButton.LeftButton)
    assert messages(page) == ["signal found", "order sent"]
    qtbot.mouseClick(page.trace_button, Qt.MouseButton.LeftButton)
    assert len(messages(page)) == 3


def test_qt_message_types_map_to_log_levels() -> None:
    assert qt_level("QtWarningMsg") is LogLevel.WARNING
    assert qt_level("QtCriticalMsg") is LogLevel.ERROR
    assert qt_level("QtFatalMsg") is LogLevel.CRITICAL
    assert qt_level("SomethingNew") is LogLevel.WARNING
