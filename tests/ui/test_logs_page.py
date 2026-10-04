import json
import time
from datetime import UTC, datetime
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


def iso(seconds: float) -> str:
    moment = datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds")
    return moment.replace("+00:00", "Z")


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


def test_category_tabs_level_and_text_filters(qtbot: QtBot, tmp_path: Path) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    assert page.category_tabs.count() == len(LogCategory) + 1
    controls.buffer.append(entry("Connected", category="mt5", symbol="EURUSD"))
    controls.buffer.append(entry("Spread high", category="risk", level="WARNING"))
    controls.buffer.append(entry("tick", category="mt5", level="DEBUG"))
    page.reload()
    page.select_category("mt5")
    assert page.current_category() == "mt5"
    assert messages(page) == ["Connected", "tick"]
    page.select_category(None)
    page.level_box.setCurrentIndex(page.level_box.findData(int(LogLevel.WARNING)))
    assert messages(page) == ["Spread high"]
    page.level_box.setCurrentIndex(0)
    page.search_box.setText("eurusd")
    assert messages(page) == ["Connected"]
    page.regex_box.setChecked(True)
    page.search_box.setText("(")
    assert page.status_label.text().startswith("Invalid pattern")


def test_symbol_and_strategy_filters(qtbot: QtBot, tmp_path: Path) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    controls.buffer.append(entry("a", symbol="EURUSD.m", strategy="trend_pullback"))
    controls.buffer.append(entry("b", symbol="GBPUSD", strategy="london_breakout"))
    page.reload()
    page.symbol_box.setText("eur")
    assert messages(page) == ["a"]
    page.symbol_box.setText("")
    page.strategy_box.setText("london")
    assert messages(page) == ["b"]


def test_a_time_range_also_reads_the_saved_files(qtbot: QtBot, tmp_path: Path) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    now = time.time()
    folder = tmp_path / "logs" / "app"
    folder.mkdir(parents=True)
    saved = [
        {"time": iso(now - 600), "level": "INFO", "category": "app", "message": "from a file"},
        {"time": iso(now - 7200), "level": "INFO", "category": "app", "message": "too old"},
    ]
    text = "".join(json.dumps(item) + "\n" for item in saved)
    (folder / "2026-10-01.jsonl").write_bytes(text.encode("utf-8"))
    controls.buffer.append(entry("old session line"))
    page.reload()
    assert messages(page) == ["old session line"]
    page.time_box.setCurrentIndex(page.time_box.findText("Last hour"))
    qtbot.waitUntil(lambda: messages(page) == ["from a file"], timeout=5000)
    assert "1 saved line(s)" in page.status_label.text()
    page.time_box.setCurrentIndex(0)
    assert messages(page) == ["old session line"]


def test_levels_and_debug_mode_can_be_changed_and_are_audited(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, controls, actions = make_page(qtbot, tmp_path)
    assert not page.category_level_box.isEnabled()
    page.select_category("mt5")
    assert page.category_level_box.isEnabled()
    page.category_level_box.setCurrentIndex(page.category_level_box.findData(int(LogLevel.DEBUG)))
    assert controls.registry.level(LogCategory.MT5) is LogLevel.DEBUG
    qtbot.mouseClick(page.debug_button, Qt.MouseButton.LeftButton)
    assert controls.debug_state().active
    assert "min left" in page.debug_label.text()
    qtbot.mouseClick(page.debug_button, Qt.MouseButton.LeftButton)
    assert not controls.debug_state().active
    assert actions == ["log_level_changed", "debug_mode_enabled", "debug_mode_disabled"]


def test_selecting_a_line_shows_its_json_and_the_trace_timeline(
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
    assert page.detail.toPlainText().startswith("Trace trace-a: 2 lines")
    qtbot.waitUntil(lambda: "saved line(s)" in page.status_label.text(), timeout=5000)
    assert messages(page) == ["signal found", "order sent"]
    qtbot.mouseClick(page.trace_button, Qt.MouseButton.LeftButton)
    assert len(messages(page)) == 3


def test_the_export_saves_the_lines_shown(qtbot: QtBot, tmp_path: Path) -> None:
    page, controls, _ = make_page(qtbot, tmp_path)
    assert page.export_rows() is None
    assert page.status_label.text().startswith("Nothing to export")
    controls.buffer.append(entry("kept", category="risk"))
    controls.buffer.append(entry("hidden", category="mt5"))
    page.select_category("risk")
    paths = page.export_rows()
    assert paths is not None
    json_path, csv_path = paths
    assert json_path.parent == tmp_path / "exports" and csv_path.exists()
    lines = json_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(item)["message"] for item in lines] == ["kept"]
    assert "Exported 1 line(s)" in page.status_label.text()


def test_qt_message_types_map_to_log_levels() -> None:
    assert qt_level("QtWarningMsg") is LogLevel.WARNING
    assert qt_level("QtCriticalMsg") is LogLevel.ERROR
    assert qt_level("QtFatalMsg") is LogLevel.CRITICAL
    assert qt_level("SomethingNew") is LogLevel.WARNING
