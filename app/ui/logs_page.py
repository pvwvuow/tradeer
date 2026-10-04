"""Logs page (spec F3 page 12, E3): category tabs, live tail, filters, JSON detail, the full
trace view, levels, debug mode, export and the log folder.

"This session" shows the live lines kept in memory. A longer time range, and "Show only
this trace", also read the saved log files (zipped ones too) in a background thread, so a
big log folder never freezes the window; the result is merged with the live lines.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.observability.categories import LogCategory
from app.observability.controls import LogControls
from app.observability.levels import DEFAULT_DEBUG_MINUTES, LogLevel, level_number
from app.observability.log_reader import (
    EXPORT_FOLDER,
    export_entries,
    merge_entries,
    read_entries,
    timeline_text,
)
from app.ui.log_filter import LogFilter, Matcher, detail_text, make_matcher, row_values
from app.ui.pages import styled_label
from app.ui.theme import ThemeTokens

REFRESH_MS = 250  # at most 4 UI updates per second (spec D4)
LOAD_DELAY_MS = 300  # wait for the typing to stop before reading the files again
MAX_ROWS = 2000
COLUMNS: tuple[str, ...] = ("Time", "Level", "Category", "Message", "Trace")
MESSAGE_COLUMN = 3
ALL_CATEGORIES = "All"
SESSION = "This session"
TIME_RANGES: tuple[tuple[str, float | None], ...] = (
    (SESSION, None),
    ("Last 15 minutes", 15 * 60.0),
    ("Last hour", 3600.0),
    ("Last 24 hours", 86_400.0),
    ("Last 7 days", 7 * 86_400.0),
    ("Last 30 days", 30 * 86_400.0),
)
_ROOT = QModelIndex()

Loaded = tuple[int, list[dict[str, Any]], str]


class LogTableModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[dict[str, Any]] = []
        self._cells: list[tuple[str, ...]] = []
        self._tokens: ThemeTokens | None = None

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._entries):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._cells[index.row()][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and self._tokens is not None:
            level = level_number(self._entries[index.row()].get("level"))
            if level >= LogLevel.ERROR:
                return QColor(self._tokens.loss)
            if level >= LogLevel.WARNING:
                return QColor(self._tokens.warning)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def entry(self, row: int) -> dict[str, Any] | None:
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        if self._entries:
            last = self.index(len(self._entries) - 1, len(COLUMNS) - 1)
            self.dataChanged.emit(self.index(0, 0), last)

    def reset(self, entries: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._entries = entries[-MAX_ROWS:]
        self._cells = [row_values(entry) for entry in self._entries]
        self.endResetModel()

    def append(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        entries = entries[-MAX_ROWS:]
        overflow = len(self._entries) + len(entries) - MAX_ROWS
        if overflow > 0:
            self.beginRemoveRows(_ROOT, 0, overflow - 1)
            del self._entries[:overflow]
            del self._cells[:overflow]
            self.endRemoveRows()
        first = len(self._entries)
        self.beginInsertRows(_ROOT, first, first + len(entries) - 1)
        self._entries.extend(entries)
        self._cells.extend(row_values(entry) for entry in entries)
        self.endInsertRows()


class LogsPage(QWidget):
    def __init__(self, controls: LogControls, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controls = controls
        self.setObjectName("page_logs")
        self.now: Callable[[], float] = time.time
        self._last_seq = 0
        self._trace_id: str | None = None
        self._matcher: Matcher = make_matcher(LogFilter())[0]
        self._load_id = 0
        self._loaded: Loaded | None = None
        self._pending: LogFilter | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Logs", "title"))
        intro = (
            "Live log lines and the saved log files. Passwords and keys are masked before they "
            "reach this page or any file."
        )
        layout.addWidget(styled_label(intro, "muted", wrap=True))
        layout.addWidget(self._build_tabs())
        layout.addLayout(self._build_filter_row())
        layout.addLayout(self._build_control_row())
        self.model = LogTableModel(self)
        self.table = QTableView()
        self.table.setObjectName("LogTable")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(MESSAGE_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.detail = QPlainTextEdit()
        self.detail.setObjectName("LogDetail")
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a line to see its full JSON entry.")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.status_label = styled_label("", "muted", wrap=True)
        self.status_label.setObjectName("LogStatus")
        layout.addWidget(self.status_label)
        selection = self.table.selectionModel()
        selection.currentRowChanged.connect(self._on_current_row_changed)
        self.load_timer = QTimer(self)
        self.load_timer.setSingleShot(True)
        self.load_timer.setInterval(LOAD_DELAY_MS)
        self.load_timer.timeout.connect(self._start_load)
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.reload()

    def _build_tabs(self) -> QTabBar:
        self.category_tabs = QTabBar()
        self.category_tabs.setObjectName("LogCategoryTabs")
        self.category_tabs.setExpanding(False)
        self.category_tabs.setUsesScrollButtons(True)
        self.category_tabs.addTab(ALL_CATEGORIES)
        self.category_tabs.setTabData(0, None)
        for category in LogCategory:
            index = self.category_tabs.addTab(category.value)
            self.category_tabs.setTabData(index, category.value)
        self.category_tabs.currentChanged.connect(self.reload)
        return self.category_tabs

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.level_box = QComboBox()
        self.level_box.setObjectName("LogLevelFilter")
        for level in LogLevel:
            self.level_box.addItem(f"{level.name} and above", int(level))
        self.search_box = QLineEdit()
        self.search_box.setObjectName("LogSearch")
        self.search_box.setPlaceholderText("Search messages, symbols, trace ids")
        self.regex_box = QCheckBox("Regex")
        self.symbol_box = QLineEdit()
        self.symbol_box.setObjectName("LogSymbol")
        self.symbol_box.setPlaceholderText("Symbol")
        self.symbol_box.setMaximumWidth(110)
        self.strategy_box = QLineEdit()
        self.strategy_box.setObjectName("LogStrategy")
        self.strategy_box.setPlaceholderText("Strategy")
        self.strategy_box.setMaximumWidth(140)
        self.time_box = QComboBox()
        self.time_box.setObjectName("LogTimeRange")
        for label, seconds in TIME_RANGES:
            self.time_box.addItem(label, seconds)
        row.addWidget(self.level_box)
        row.addWidget(self.search_box, 1)
        row.addWidget(self.regex_box)
        row.addWidget(self.symbol_box)
        row.addWidget(self.strategy_box)
        row.addWidget(self.time_box)
        self.level_box.currentIndexChanged.connect(self.reload)
        self.search_box.textChanged.connect(self.reload)
        self.regex_box.toggled.connect(self.reload)
        self.symbol_box.textChanged.connect(self.reload)
        self.strategy_box.textChanged.connect(self.reload)
        self.time_box.currentIndexChanged.connect(self.reload)
        return row

    def _build_control_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.trace_button = QPushButton("Show only this trace")
        self.trace_button.setEnabled(False)
        self.category_level_label = styled_label("Level for this category", "muted")
        self.category_level_box = QComboBox()
        self.category_level_box.setObjectName("CategoryLevel")
        for level in LogLevel:
            self.category_level_box.addItem(level.name, int(level))
        self.debug_button = QPushButton()
        self.debug_button.setObjectName("DebugModeButton")
        self.debug_label = styled_label("", "muted")
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("LogExport")
        self.export_button.setToolTip("Save the lines shown as JSON lines and CSV (exports folder)")
        self.folder_button = QPushButton("Open log folder")
        row.addWidget(self.trace_button)
        row.addWidget(self.category_level_label)
        row.addWidget(self.category_level_box)
        row.addStretch(1)
        row.addWidget(self.debug_label)
        row.addWidget(self.debug_button)
        row.addWidget(self.export_button)
        row.addWidget(self.folder_button)
        self.trace_button.clicked.connect(self._toggle_trace)
        self.category_level_box.currentIndexChanged.connect(self._on_category_level_changed)
        self.debug_button.clicked.connect(self.toggle_debug)
        self.export_button.clicked.connect(self.export_rows)
        self.folder_button.clicked.connect(self.open_log_folder)
        return row

    def current_category(self) -> str | None:
        found = self.category_tabs.tabData(self.category_tabs.currentIndex())
        return str(found) if found else None

    def select_category(self, category: str | None) -> None:
        for index in range(self.category_tabs.count()):
            if self.category_tabs.tabData(index) == category:
                self.category_tabs.setCurrentIndex(index)
                return

    def current_filter(self) -> LogFilter:
        seconds = self.time_box.currentData()
        return LogFilter(
            category=self.current_category(),
            min_level=LogLevel(int(self.level_box.currentData())),
            text=self.search_box.text(),
            regex=self.regex_box.isChecked(),
            trace_id=self._trace_id,
            symbol=self.symbol_box.text(),
            strategy=self.strategy_box.text(),
            since=self.now() - float(seconds) if seconds is not None else None,
        )

    def reload(self) -> None:
        log_filter = self.current_filter()
        self._matcher, error = make_matcher(log_filter)
        items = self.controls.buffer.since(0)
        self._last_seq = items[-1][0] if items else 0
        self.model.reset([entry for _, entry in items if self._matcher(entry)])
        self.table.scrollToBottom()
        self.status_label.setText(error or "")
        self._sync_category_level()
        self._update_debug_controls()
        self._show_trace()
        self._load_id += 1
        self._loaded = None
        self._pending = None
        self.load_timer.stop()
        if log_filter.since is not None or log_filter.trace_id is not None:
            self._pending = log_filter
            self.load_timer.start()

    def _start_load(self) -> None:
        log_filter = self._pending
        if log_filter is None:
            return
        self._pending = None
        load_id = self._load_id
        keep = self._matcher
        root = self.controls.log_dir
        contains = log_filter.trace_id or ""
        since = log_filter.since
        self.status_label.setText("Reading the saved log files...")

        def work() -> None:
            try:
                found = read_entries(root, since=since, keep=keep, contains=contains)
            except Exception as error:
                self._loaded = (load_id, [], f"The log files could not be read: {error}")
                return
            self._loaded = (load_id, found, "")

        threading.Thread(target=work, name="log-reader", daemon=True).start()

    def _apply_loaded(self) -> None:
        done = self._loaded
        if done is None:
            return
        self._loaded = None
        load_id, found, error = done
        if load_id != self._load_id:
            return
        self.model.reset(merge_entries(found, self.model.entries(), limit=MAX_ROWS))
        self.table.scrollToBottom()
        self.status_label.setText(error or f"{len(found):,} saved line(s) from the log files.")
        self._show_trace()

    def refresh(self) -> None:
        self._update_debug_controls()
        self._apply_loaded()
        if not self.isVisible():
            return
        items = self.controls.buffer.since(self._last_seq)
        if not items:
            return
        self._last_seq = items[-1][0]
        matching = [entry for _, entry in items if self._matcher(entry)]
        scrollbar = self.table.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 2
        self.model.append(matching)
        if follow and matching:
            self.table.scrollToBottom()

    def export_rows(self) -> tuple[Path, Path] | None:
        """Save the lines shown (as filtered) to the exports folder next to the logs."""
        entries = self.model.entries()
        if not entries:
            self.status_label.setText("Nothing to export: no line matches the filters.")
            return None
        folder = self.controls.log_dir.parent / EXPORT_FOLDER
        try:
            paths = export_entries(entries, folder, self.now())
        except OSError as error:
            self.status_label.setText(f"The export failed: {error}")
            return None
        json_path, csv_path = paths
        self.status_label.setText(
            f"Exported {len(entries):,} line(s): {json_path.name} and {csv_path.name} in {folder}",
        )
        return paths

    def toggle_debug(self) -> None:
        self.controls.toggle_debug(DEFAULT_DEBUG_MINUTES)
        self._update_debug_controls()

    def open_log_folder(self) -> None:
        self.controls.log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controls.log_dir)))

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.model.set_tokens(tokens)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh()

    def _selected_entry(self) -> dict[str, Any] | None:
        return self.model.entry(self.table.currentIndex().row())

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        entry = self.model.entry(current.row())
        self.detail.setPlainText(detail_text(entry) if entry is not None else "")
        self._update_trace_button(entry)

    def _show_trace(self) -> None:
        if self._trace_id is not None:
            self.detail.setPlainText(timeline_text(self._trace_id, self.model.entries()))

    def _toggle_trace(self) -> None:
        if self._trace_id is not None:
            self._trace_id = None
        else:
            entry = self._selected_entry()
            trace_id = entry.get("trace_id") if entry is not None else None
            self._trace_id = str(trace_id) if trace_id else None
        self.reload()
        self._update_trace_button(self._selected_entry())

    def _update_trace_button(self, entry: dict[str, Any] | None) -> None:
        if self._trace_id is not None:
            self.trace_button.setText("Show all traces")
            self.trace_button.setEnabled(True)
            return
        self.trace_button.setText("Show only this trace")
        self.trace_button.setEnabled(bool(entry and entry.get("trace_id")))

    def _sync_category_level(self) -> None:
        category = self.current_category()
        enabled = bool(category)
        self.category_level_label.setEnabled(enabled)
        self.category_level_box.setEnabled(enabled)
        if not category:
            return
        level = self.controls.registry.level(LogCategory(category))
        self.category_level_box.blockSignals(True)
        self.category_level_box.setCurrentIndex(self.category_level_box.findData(int(level)))
        self.category_level_box.blockSignals(False)

    def _on_category_level_changed(self) -> None:
        category = self.current_category()
        if not category:
            return
        level = LogLevel(int(self.category_level_box.currentData()))
        self.controls.set_level(LogCategory(category), level)

    def _update_debug_controls(self) -> None:
        state = self.controls.debug_state()
        if state.active:
            minutes = max(1, round(state.remaining_seconds / 60))
            self.debug_label.setText(f"Debug mode on, {minutes} min left")
            self.debug_button.setText("Turn off debug mode")
        else:
            self.debug_label.setText("Debug mode off")
            self.debug_button.setText(f"Debug mode for {DEFAULT_DEBUG_MINUTES:.0f} min")
