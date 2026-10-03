"""Closed-trade history with filters and a detail view (spec F3 page 4), used by Positions &
Trades. The detail shows the story, the signal's reasoning, features and decision trace, the
events timeline and the journal notes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.analytics.trades import TradeFilter, TradeRecord
from app.ui.pages import styled_label
from app.ui.tables import fill_table, make_table, number, signed

ALL = "All"
COLUMNS = (
    "Closed (UTC)",
    "Symbol",
    "Side",
    "Lots",
    "Net",
    "R",
    "Strategy",
    "Mode",
    "Source",
    "Exit",
)


@dataclass
class TradeHistory:
    trades: Callable[[], Sequence[TradeRecord]]
    detail: Callable[[TradeRecord], str]


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M")


def history_row(trade: TradeRecord) -> list[str]:
    side = "Buy" if trade.direction == "buy" else "Sell"
    return [
        _utc(trade.close_time),
        trade.symbol,
        side,
        f"{trade.volume:g}",
        signed(trade.net_profit),
        number(trade.r_multiple),
        trade.strategy,
        trade.mode,
        trade.source,
        trade.exit_reason,
    ]


class TradeHistoryWidget(QWidget):
    def __init__(self, history: TradeHistory | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history = history
        self.shown: list[TradeRecord] = []
        self._all: list[TradeRecord] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.symbol = QComboBox()
        self.mode = QComboBox()
        self.source = QComboBox()
        self.source.addItems([ALL, "bot", "manual"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search strategy or exit reason")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        for widget in (self.symbol, self.mode, self.source, self.search, self.refresh_button):
            row.addWidget(widget)
        layout.addLayout(row)
        self.count = styled_label("", "muted")
        layout.addWidget(self.count)
        splitter = QSplitter()
        self.table = make_table(COLUMNS, select=True)
        self.table.setObjectName("TradeHistory")
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setObjectName("TradeDetail")
        self.detail.setPlaceholderText("Select a trade to see its story, reasoning and events.")
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        layout.addWidget(splitter, 1)
        for combo in (self.symbol, self.mode, self.source):
            combo.currentTextChanged.connect(lambda _: self.apply())
        self.search.textChanged.connect(lambda _: self.apply())
        self.refresh_button.setEnabled(history is not None)
        if history is None:
            self.count.setText("No trade history: storage is not running.")
        else:
            self.refresh()

    def refresh(self) -> None:
        if self.history is None:
            return
        self._all = sorted(self.history.trades(), key=lambda t: t.close_time, reverse=True)
        for combo, values in (
            (self.symbol, sorted({t.symbol for t in self._all})),
            (self.mode, sorted({t.mode for t in self._all})),
        ):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([ALL, *values])
            combo.setCurrentText(current if current in values else ALL)
            combo.blockSignals(False)
        self.apply()

    def apply(self) -> None:
        wanted = TradeFilter(
            symbol="" if self.symbol.currentText() in ("", ALL) else self.symbol.currentText(),
            mode="" if self.mode.currentText() in ("", ALL) else self.mode.currentText(),
            source="" if self.source.currentText() == ALL else self.source.currentText(),
        )
        text = self.search.text().strip().lower()
        self.shown = [
            t
            for t in wanted.apply(self._all)
            if not text or text in t.strategy.lower() or text in t.exit_reason.lower()
        ]
        fill_table(self.table, [history_row(t) for t in self.shown])
        net = sum(t.net_profit for t in self.shown)
        self.count.setText(f"{len(self.shown)} closed trade(s), net {net:+,.2f}")
        self.detail.setPlainText("")

    def show_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.history is None:
            return
        index = rows[0].row()
        if 0 <= index < len(self.shown):
            self.detail.setPlainText(self.history.detail(self.shown[index]))
