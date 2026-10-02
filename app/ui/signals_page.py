"""The Signals page (spec F3): live feed with rejected signals and their reasons, the
decision trace of the selected signal, and the opportunity scanner.

Signal only until Phase 8: Approve stays off; Dismiss rejects a pending signal. Snapshots
arrive from the analysis thread through a queued Qt signal, so the page never waits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.strategy_settings import StrategySettingsSource
from app.domain.signals import Direction, SignalRecord, SignalState
from app.engine.signal_pipeline import SignalPipeline, SignalsSnapshot
from app.observability.logger import audit
from app.strategies.base import EXAMPLE_NOTE
from app.ui.pages import PAGE_MARGIN, styled_label

SIGNAL_ONLY_NOTE = (
    "Signal only: nothing is sent to MT5. Every signal is sized and checked by the risk "
    f"limits; orders arrive in Phase 8, so Approve stays off. {EXAMPLE_NOTE}"
)
FEED_COLUMNS = [
    "Time (local)",
    "Symbol",
    "Strategy",
    "Side",
    "Entry",
    "SL",
    "TP",
    "R:R",
    "Probability",
    "EV (R)",
    "Lot",
    "Risk",
    "State",
    "Reason",
]
SCANNER_COLUMNS = ["#", "Symbol", "Strategy", "Setup", "Rules passed", "Side", "Note"]
STATE_FILTERS: dict[str, tuple[SignalState, ...]] = {
    "All signals": (),
    "Waiting for approval": (SignalState.PENDING_APPROVAL,),
    "Filtered out": (SignalState.FILTERED_OUT, SignalState.RISK_REJECTED),
    "Expired or dismissed": (SignalState.EXPIRED, SignalState.USER_REJECTED),
}
STATE_TEXT = {
    SignalState.PENDING_APPROVAL: "waiting for approval",
    SignalState.FILTERED_OUT: "filtered out",
    SignalState.RISK_REJECTED: "rejected by risk",
    SignalState.EXPIRED: "expired",
    SignalState.USER_REJECTED: "dismissed",
}


@dataclass
class SignalsContext:
    pipeline: SignalPipeline
    settings: StrategySettingsSource


class _Bridge(QObject):
    snapshot = Signal(object)


def _item(text: str, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def state_text(state: SignalState) -> str:
    return STATE_TEXT.get(state, state.value.lower().replace("_", " "))


def feed_row(record: SignalRecord) -> list[str]:
    """The table cells of one signal, in plain words."""
    signal = record.signal
    side = "Buy" if signal.direction is Direction.LONG else "Sell"
    if signal.order_type.value != "market":
        side = f"{side} {signal.order_type.value}"
    moment = datetime.fromtimestamp(signal.created_at).strftime("%Y-%m-%d %H:%M")
    estimate = record.probability
    probability = "unknown" if estimate.value is None else f"{estimate.value * 100:.0f}%"
    ev = "-" if record.expected_value is None else f"{record.expected_value:+.2f}"
    reason = record.reject_reason or signal.reason
    lot = "-" if record.volume is None else f"{record.volume:g}"
    risk = "-" if record.risk_money is None else f"{record.risk_money:,.2f}"
    return [
        moment,
        signal.symbol,
        signal.strategy,
        side,
        signal.price(signal.entry),
        signal.price(signal.sl),
        signal.price(signal.tp),
        f"{signal.rr:.1f}",
        probability,
        ev,
        lot,
        risk,
        state_text(signal.state),
        reason,
    ]


class SignalsPage(QWidget):
    def __init__(self, context: SignalsContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_signals")
        self.context = context
        self.bridge = _Bridge()
        self.bridge.snapshot.connect(self.show_snapshot, Qt.ConnectionType.QueuedConnection)
        self.last_snapshot: SignalsSnapshot | None = None
        self.records: list[SignalRecord] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Signals", "title"))
        layout.addWidget(styled_label(SIGNAL_ONLY_NOTE, "muted", wrap=True))
        self.status = styled_label("Waiting for the first closed bar.", "muted", wrap=True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SignalsTabs")
        self.tabs.addTab(self._build_feed(), "Feed")
        self.scanner = _table(SCANNER_COLUMNS)
        self.scanner.setObjectName("ScannerTable")
        self.tabs.addTab(self.scanner, "Scanner")
        layout.addWidget(self.tabs, 1)
        if context is not None:
            context.pipeline.add_listener(self.bridge.snapshot.emit)
            self.show_snapshot(context.pipeline.snapshot)

    def _build_feed(self) -> QWidget:
        feed = QWidget()
        layout = QVBoxLayout(feed)
        layout.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.state_filter = QComboBox()
        self.state_filter.setObjectName("SignalStateFilter")
        self.state_filter.addItems(list(STATE_FILTERS))
        self.state_filter.currentTextChanged.connect(self._refill)
        self.approve_button = QPushButton("Approve")
        self.approve_button.setEnabled(False)
        self.approve_button.setToolTip("Orders arrive in Phase 8. This build is signal only.")
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.setEnabled(False)
        self.dismiss_button.clicked.connect(self.dismiss_selected)
        row.addWidget(styled_label("Show", "section"))
        row.addWidget(self.state_filter)
        row.addStretch(1)
        row.addWidget(self.approve_button)
        row.addWidget(self.dismiss_button)
        layout.addLayout(row)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.feed = _table(FEED_COLUMNS)
        self.feed.setObjectName("SignalsTable")
        self.feed.itemSelectionChanged.connect(self._selection_changed)
        self.trace = QPlainTextEdit()
        self.trace.setObjectName("DecisionTrace")
        self.trace.setReadOnly(True)
        self.trace.setPlaceholderText("Select a signal to see its decision trace.")
        splitter.addWidget(self.feed)
        splitter.addWidget(self.trace)
        layout.addWidget(splitter, 1)
        return feed

    # Snapshots -------------------------------------------------------------------------
    def show_snapshot(self, snapshot: object) -> None:
        """Slot: a new signals snapshot (from the analysis thread, queued)."""
        if not isinstance(snapshot, SignalsSnapshot):
            return
        self.last_snapshot = snapshot
        pending = len(snapshot.pending())
        names = ", ".join(snapshot.strategies) or "none (turn one on in Strategies)"
        self.status.setText(f"{snapshot.message}. Strategies on: {names}. Waiting: {pending}.")
        self._refill()
        self._fill_scanner(snapshot)

    def visible_records(self) -> list[SignalRecord]:
        snapshot = self.last_snapshot
        if snapshot is None:
            return []
        states = STATE_FILTERS.get(self.state_filter.currentText(), ())
        return [r for r in snapshot.signals if not states or r.signal.state in states]

    def _refill(self, *_: object) -> None:
        selected = self.selected_record()
        self.records = self.visible_records()
        self.feed.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            cells = feed_row(record)
            for column, text in enumerate(cells):
                tooltip = record.probability.text() if column == 8 else ""
                self.feed.setItem(row, column, _item(text, tooltip or text))
        if selected is not None:
            for row, record in enumerate(self.records):
                if record.id == selected.id:
                    self.feed.selectRow(row)
                    break
        self._selection_changed()

    def _fill_scanner(self, snapshot: SignalsSnapshot) -> None:
        self.scanner.setRowCount(len(snapshot.scanner))
        for row, entry in enumerate(snapshot.scanner):
            side = {"long": "Buy", "short": "Sell"}.get(entry.direction, "")
            cells = [
                str(row + 1),
                entry.symbol,
                entry.strategy,
                entry.state,
                f"{entry.passed} of {entry.total}",
                side,
                entry.note,
            ]
            for column, text in enumerate(cells):
                self.scanner.setItem(row, column, _item(text))

    # Selection and actions -------------------------------------------------------------
    def selected_record(self) -> SignalRecord | None:
        rows = self.feed.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self.records[index] if 0 <= index < len(self.records) else None

    def _selection_changed(self) -> None:
        record = self.selected_record()
        if record is None:
            self.trace.setPlainText("")
            self.dismiss_button.setEnabled(False)
            return
        signal = record.signal
        head = [signal.summary(), f"State: {state_text(signal.state)}", ""]
        changes = [change.text() for change in signal.history]
        lines = [*head, *record.trace.lines(), "", *changes]
        self.trace.setPlainText("\n".join(lines))
        self.dismiss_button.setEnabled(signal.state is SignalState.PENDING_APPROVAL)

    def dismiss_selected(self) -> None:
        record = self.selected_record()
        if record is None or self.context is None:
            return
        if record.signal.state is not SignalState.PENDING_APPROVAL:
            return
        self.context.pipeline.dismiss(record.id)
        audit("signal dismissed", before=record.signal.state.value, after=record.id)
        self.dismiss_button.setEnabled(False)
        self.status.setText("Dismissed. The signal closes on the next analysis cycle.")
