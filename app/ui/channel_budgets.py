"""The money given to the Telegram channels on the main pages (phase 22g, 0.48.0).

The Dashboard gets a side block under the risk limits, the Risk page a table under the limit
usage: each channel with a budget, what it is worth now, what it made today, the risk of one
trade and its stop when a daily or drawdown limit was reached (`app.channels.overview`). A
channel is shown while it is on and has a budget; with none, the block is hidden. Nothing
here changes a setting: budgets are given in the AI Lab chat or the Telegram channels page.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.channels.overview import ChannelLine, Overview
from app.ui.pages import styled_label
from app.ui.v2 import InfoRow, Section, v2_label

MINUS = "\u2212"
DOT = " \u00b7 "
REFRESH_MS = 30_000
ALL = "All channels"
RISK_COLUMNS = ("Channel", "Mode", "Budget", "Worth now", "Today", "Per trade", "Stop")
RISK_NOTE = (
    "Each channel trades with its own budget; the account limits above apply on top of them."
)


def signed(value: float, currency: str = "") -> str:
    """A result with its sign and a true minus: "+4.20 USD", "\u22126.00 USD"."""
    sign = "+" if value > 0 else MINUS if value < 0 else ""
    text = f"{sign}{abs(value):,.2f}"
    return f"{text} {currency}".strip()


@dataclass(frozen=True)
class BudgetRow:
    name: str
    equity: float
    budget: float
    today: float
    stop: str = ""
    tip: str = ""


def budget_rows(found: Overview) -> list[BudgetRow]:
    """One row per channel, and a total first when there are several."""
    rows = [
        BudgetRow(
            line.title,
            line.equity,
            line.budget,
            line.today,
            line.stop,
            tip(line),
        )
        for line in found.lines
    ]
    if len(rows) > 1:
        total = BudgetRow(ALL, found.equity, found.budget, found.today)
        rows.insert(0, total)
    return rows


def tip(line: ChannelLine) -> str:
    mode = "Live" if line.live else "Paper trial"
    return (
        f"{mode}: budget {line.budget:,.2f}, closed {line.closed:+,.2f}, "
        f"one trade risks {line.risk_money:,.2f} ({line.risk_percent:g}%)"
    )


def value_text(row: BudgetRow, currency: str, words: Callable[[str], str]) -> str:
    text = f"{row.equity:,.2f} / {row.budget:,.2f}{DOT}{signed(row.today, currency)}"
    return f"{text}{DOT}{words(row.stop)}" if row.stop else text


def risk_rows(found: Overview, currency: str) -> list[tuple[str, ...]]:
    """The Risk page's table rows."""
    rows: list[tuple[str, ...]] = []
    for line in found.lines:
        rows.append(
            (
                line.title,
                "Live" if line.live else "Paper trial",
                f"{line.budget:,.2f}",
                f"{line.equity:,.2f}",
                signed(line.today, currency),
                f"{line.risk_money:,.2f} ({line.risk_percent:g}%)",
                line.stop or "-",
            ),
        )
    return rows


class DashboardBudgets(QWidget):
    """The Dashboard's block: a section row, then a line per channel."""

    def __init__(self, words: Callable[[str], str]) -> None:
        super().__init__()
        self.words = words
        self.setObjectName("DashboardChannels")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.section = Section(words("Telegram channels"), link=words("Open the AI Lab"))
        outer.addWidget(self.section)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        outer.addLayout(self.rows)
        self.shown: tuple[Overview, str] | None = None
        self.setVisible(False)

    def set_overview(self, found: Overview, currency: str) -> None:
        self.setVisible(bool(found.lines))
        if self.shown == (found, currency):
            return
        self.shown = (found, currency)
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for row in budget_rows(found):
            value = v2_label(value_text(row, currency, self.words), "row")
            tone = "profit" if row.today > 0 else "loss" if row.today < 0 else ""
            value.setProperty("tone", "loss" if row.stop else tone)
            line = InfoRow(self.words(row.name), value)
            line.setToolTip(row.tip)
            self.rows.addWidget(line)


class RiskBudgets(QWidget):
    """The Risk page's table of channel budgets, read again every 30 seconds."""

    def __init__(
        self,
        source: Callable[[], Overview],
        currency: Callable[[], str] = lambda: "",
    ) -> None:
        super().__init__()
        self.source = source
        self.currency = currency
        self.setObjectName("RiskChannels")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(styled_label("Telegram channel budgets", "section"))
        self.table = QTableWidget(0, len(RISK_COLUMNS))
        self.table.setObjectName("RiskChannelBudgets")
        self.table.setHorizontalHeaderLabels(list(RISK_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        layout.addWidget(styled_label(RISK_NOTE, "muted", wrap=True))
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.setVisible(False)
        self.refresh()

    def refresh(self) -> int:
        """Read the channels again; the number of rows shown (the block hides at none)."""
        try:
            found = self.source()
        except Exception:
            found = Overview()
        rows = risk_rows(found, self.currency())
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.setFixedHeight(34 + 30 * max(1, len(rows)))
        self.setVisible(bool(rows))
        return len(rows)
