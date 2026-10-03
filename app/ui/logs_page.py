"""Logs page, basic version (spec G3 phase 2, F3.12): live tail, filters, levels, debug mode.

The full page (trace timeline, time filters, export) arrives in Phase 14.
"""

from __future__ import annotations

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
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.observability.categories import LogCategory
from app.observability.controls import LogControls
from app.observability.levels import DEFAULT_DEBUG_MINUTES, LogLevel, level_number
from app.ui.log_filter import LogFilter, detail_text, make_matcher, row_values
from app.ui.pages import styled_label
from app.ui.theme import ThemeTokens

REFRESH_MS = 250  # at most 4 UI updates per second (spec D4)
MAX_ROWS = 2000
COLUMNS: tuple[str, ...] = ("Time", "Level", "Category", "Message", "Trace")
MESSAGE_COLUMN = 3
ALL_CATEGORIES = "All categories"
_ROOT = QModelIndex()


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
        self._last_seq = 0
        self._trace_id: str | None = None
        self._matcher = make_matcher(LogFilter())[0]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)
        layout.addWidget(styled_label("Logs", "title"))
        intro = "Live log lines from this session. Passwords and keys are masked before they reach "
        layout.addWidget(styled_label(f"{intro}this page or any file.", "muted", wrap=True))
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
        layout.addWidget(self.status_label)
        selection = self.table.selectionModel()
        selection.currentRowChanged.connect(self._on_current_row_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.reload()

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.category_box = QComboBox()
        self.category_box.setObjectName("LogCategoryFilter")
        self.category_box.addItem(ALL_CATEGORIES, None)
        for category in LogCategory:
            self.category_box.addItem(category.value, category.value)
        self.level_box = QComboBox()
        self.level_box.setObjectName("LogLevelFilter")
        for level in LogLevel:
            self.level_box.addItem(f"{level.name} and above", int(level))
        self.search_box = QLineEdit()
        self.search_box.setObjectName("LogSearch")
        self.search_box.setPlaceholderText("Search messages, symbols, trace ids")
        self.regex_box = QCheckBox("Regex")
        self.trace_button = QPushButton("Show only this trace")
        self.trace_button.setEnabled(False)
        row.addWidget(self.category_box)
        row.addWidget(self.level_box)
        row.addWidget(self.search_box, 1)
        row.addWidget(self.regex_box)
        row.addWidget(self.trace_button)
        self.category_box.currentIndexChanged.connect(self.reload)
        self.level_box.currentIndexChanged.connect(self.reload)
        self.search_box.textChanged.connect(self.reload)
        self.regex_box.toggled.connect(self.reload)
        self.trace_button.clicked.connect(self._toggle_trace)
        return row

    def _build_control_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.category_level_label = styled_label("Level for this category", "muted")
        self.category_level_box = QComboBox()
        self.category_level_box.setObjectName("CategoryLevel")
        for level in LogLevel:
            self.category_level_box.addItem(level.name, int(level))
        self.debug_button = QPushButton()
        self.debug_button.setObjectName("DebugModeButton")
        self.debug_label = styled_label("", "muted")
        self.folder_button = QPushButton("Open log folder")
        row.addWidget(self.category_level_label)
        row.addWidget(self.category_level_box)
        row.addStretch(1)
        row.addWidget(self.debug_label)
        row.addWidget(self.debug_button)
        row.addWidget(self.folder_button)
        self.category_level_box.currentIndexChanged.connect(self._on_category_level_changed)
        self.debug_button.clicked.connect(self.toggle_debug)
        self.folder_button.clicked.connect(self.open_log_folder)
        return row

    def current_filter(self) -> LogFilter:
        category = self.category_box.currentData()
        return LogFilter(
            category=str(category) if category else None,
            min_level=LogLevel(int(self.level_box.currentData())),
            text=self.search_box.text(),
            regex=self.regex_box.isChecked(),
            trace_id=self._trace_id,
        )

    def reload(self) -> None:
        self._matcher, error = make_matcher(self.current_filter())
        items = self.controls.buffer.since(0)
        self._last_seq = items[-1][0] if items else 0
        self.model.reset([entry for _, entry in items if self._matcher(entry)])
        self.table.scrollToBottom()
        self.status_label.setText(error or "")
        self._sync_category_level()
        self._update_debug_controls()

    def refresh(self) -> None:
        self._update_debug_controls()
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
        category = self.category_box.currentData()
        enabled = bool(category)
        self.category_level_label.setEnabled(enabled)
        self.category_level_box.setEnabled(enabled)
        if not enabled:
            return
        level = self.controls.registry.level(LogCategory(str(category)))
        self.category_level_box.blockSignals(True)
        self.category_level_box.setCurrentIndex(self.category_level_box.findData(int(level)))
        self.category_level_box.blockSignals(False)

    def _on_category_level_changed(self) -> None:
        category = self.category_box.currentData()
        if not category:
            return
        level = LogLevel(int(self.category_level_box.currentData()))
        self.controls.set_level(LogCategory(str(category)), level)

    def _update_debug_controls(self) -> None:
        state = self.controls.debug_state()
        if state.active:
            minutes = max(1, round(state.remaining_seconds / 60))
            self.debug_label.setText(f"Debug mode on, {minutes} min left")
            self.debug_button.setText("Turn off debug mode")
        else:
            self.debug_label.setText("Debug mode off")
            self.debug_button.setText(f"Debug mode for {DEFAULT_DEBUG_MINUTES:.0f} min")
