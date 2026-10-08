"""A table that shows a long list one page at a time (8 October 2026, the Dashboard lists).

The rows are plain text. `set_rows` keeps the page the user is on (it moves back when the
list got shorter) and redraws only what changed, so a refresh every two seconds neither
flickers nor jumps. The table is always tall enough for its rows: it never squeezes them
into a strip, and the page around it scrolls instead.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.pages import styled_label
from app.ui.tables import ROW_HEIGHT, make_table

PAGE_SIZE = 10
MIN_ROWS = 3
NOTHING = "Nothing to show"

Rows = tuple[tuple[str, ...], ...]


def page_count(total: int, size: int) -> int:
    """Pages needed for `total` rows; an empty list still has one (empty) page."""
    if size <= 0 or total <= 0:
        return 1
    return math.ceil(total / size)


def page_text(page: int, total: int, size: int) -> str:
    """For example "11-20 of 37 · page 2 of 4"."""
    if total <= 0:
        return NOTHING
    first = page * size + 1
    end = min(total, first + size - 1)
    return f"{first}-{end} of {total} \u00b7 page {page + 1} of {page_count(total, size)}"


class PagedTable(QWidget):
    def __init__(
        self,
        columns: Sequence[str],
        *,
        stretch: int = -1,
        page_size: int = PAGE_SIZE,
        empty: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_size = max(1, page_size)
        self.page = 0
        self._rows: list[tuple[str, ...]] = []
        self._tips: list[str] = []
        self._shown: tuple[Rows, tuple[str, ...]] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.table: QTableWidget = make_table(columns)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        column = stretch if 0 <= stretch < len(columns) else len(columns) - 1
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.empty = styled_label(empty, "muted", wrap=True)
        self.empty.setVisible(False)
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.info = styled_label(NOTHING, "muted")
        self.previous_button = QPushButton("\u25c0 Previous")
        self.previous_button.setAccessibleName("Previous page")
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button = QPushButton("Next \u25b6")
        self.next_button.setAccessibleName("Next page")
        self.next_button.clicked.connect(self.next_page)
        for button in (self.previous_button, self.next_button):
            button.setProperty("variant", "ghost")
        row.addWidget(self.info)
        row.addStretch(1)
        row.addWidget(self.previous_button)
        row.addWidget(self.next_button)
        layout.addLayout(row)
        self._draw()

    @property
    def total(self) -> int:
        return len(self._rows)

    @property
    def pages(self) -> int:
        return page_count(self.total, self.page_size)

    def set_rows(self, rows: Sequence[Sequence[str]], tips: Sequence[str] = ()) -> None:
        """The whole list; only the current page is drawn. `tips` are per-row tooltips."""
        self._rows = [tuple(values) for values in rows]
        self._tips = list(tips)
        self.page = min(self.page, self.pages - 1)
        self._draw()

    def show_page(self, page: int) -> None:
        self.page = max(0, min(page, self.pages - 1))
        self._draw()

    def next_page(self) -> None:
        self.show_page(self.page + 1)

    def previous_page(self) -> None:
        self.show_page(self.page - 1)

    def visible_rows(self) -> list[tuple[str, ...]]:
        start = self.page * self.page_size
        return self._rows[start : start + self.page_size]

    def _draw(self) -> None:
        start = self.page * self.page_size
        rows: Rows = tuple(self._rows[start : start + self.page_size])
        tips = tuple(self._tips[start : start + self.page_size])
        if (rows, tips) != self._shown:
            self._shown = (rows, tips)
            self._fill(rows, tips)
        self.info.setText(page_text(self.page, self.total, self.page_size))
        many = self.pages > 1
        self.previous_button.setVisible(many)
        self.next_button.setVisible(many)
        self.previous_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page < self.pages - 1)
        self.empty.setVisible(self.total == 0 and bool(self.empty.text()))
        self._fit()

    def _fill(self, rows: Rows, tips: tuple[str, ...]) -> None:
        table = self.table
        table.setRowCount(len(rows))
        columns = table.columnCount()
        for row, values in enumerate(rows):
            tip = tips[row] if row < len(tips) else ""
            for column in range(columns):
                text = values[column] if column < len(values) else ""
                item = table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, column, item)
                if item.text() != text:
                    item.setText(text)
                item.setToolTip(tip or text)

    def _fit(self) -> None:
        """Tall enough for a full page (at least `MIN_ROWS`), so no row is ever cut."""
        lines = max(MIN_ROWS, min(self.page_size, self.total))
        table = self.table
        height = table.horizontalHeader().sizeHint().height() + lines * ROW_HEIGHT
        height += 2 * table.frameWidth() + table.horizontalScrollBar().sizeHint().height()
        if table.height() != height or table.minimumHeight() != height:
            table.setFixedHeight(height)
