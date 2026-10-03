"""Table helpers shared by the pages: one look for every table (0.13 design system)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

ROW_HEIGHT = 30


def make_table(columns: Sequence[str], *, select: bool = False) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    if select:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    else:
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    return table


def fill_table(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, text in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(text))


def number(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


def signed(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:+,.{digits}f}"


def heat(value: float | None, scale: float = 5.0) -> QColor:
    """Green for gains, red for losses, stronger up to `scale`."""
    if value is None or not math.isfinite(value) or value == 0:
        return QColor(0, 0, 0, 0)
    strength = min(1.0, abs(value) / scale)
    alpha = int(40 + 160 * strength)
    return QColor(34, 197, 94, alpha) if value > 0 else QColor(239, 68, 68, alpha)
