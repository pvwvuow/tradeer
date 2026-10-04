"""Positions & Trades (spec F3 page 4, C5-C8): the trading mode, the bot's open positions
and pending orders with Close, the kill switch and the execution settings.

Only the bot's own positions are listed and can be closed here: manual trades are never
touched (spec G4). Switching to Semi-auto on a REAL account needs the typed word REAL. Auto
needs the typed word AUTO and, on a REAL account, the Go-Live approval of every strategy that
is on (Strategies page, Phase 13b). Every change is in the audit log. Snapshots arrive from
the analysis thread through a queued Qt signal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.execution_settings import (
    ExecutionConfig,
    ExecutionSettings,
    ExecutionSettingsSource,
)
from app.domain.management import ManagementSettings
from app.domain.modes import OperatingMode
from app.engine.execution import ExecutionEngine, ExecutionSnapshot, PositionView
from app.observability.logger import audit
from app.ui.pages import PAGE_MARGIN, styled_label
from app.ui.signals_page import ask
from app.ui.strategies_page import ParamsForm
from app.ui.trade_history import TradeHistory, TradeHistoryWidget

REAL_WORD = "REAL"
AUTO_WORD = "AUTO"
KILL_TEXT = (
    "Close every position this app opened, cancel its pending orders and stop new entries?\n\n"
    "Manual trades are not touched. New entries stay stopped until you re-enable trading on "
    "the Risk page."
)
MODES: tuple[OperatingMode, ...] = (
    OperatingMode.PAPER,
    OperatingMode.SEMI_AUTO,
    OperatingMode.AUTO,
    OperatingMode.ANALYSIS_ONLY,
)
MODE_NOTES: dict[OperatingMode, str] = {
    OperatingMode.PAPER: "Paper: approved signals are simulated on live prices. No real orders.",
    OperatingMode.SEMI_AUTO: "Semi-auto: approved signals become real orders on your account.",
    OperatingMode.AUTO: (
        "Auto: every signal that passes the filters and the risk checks is sent without "
        "asking you."
    ),
    OperatingMode.ANALYSIS_ONLY: "Analysis-only: signals are shown, nothing is ever sent.",
}
AUTO_NOTE = (
    "Auto needs the Go-Live gate: on a REAL account every strategy that is on needs its "
    "approval on the Strategies page."
)
AUTO_TEXT = (
    "Auto mode sends every signal that passes the filters and the risk checks without asking "
    "you. The kill switch stops it at any time.\nType {word} to switch to Auto mode:"
)
COLUMNS = [
    "Mode",
    "Ticket",
    "Symbol",
    "Side",
    "Lot",
    "Entry",
    "SL",
    "TP",
    "P/L",
    "MFE (R)",
    "MAE (R)",
    "Strategy",
    "Kind",
]
Confirm = Callable[[str, str], bool]
TypedAnswer = Callable[[str, str], str]


@dataclass
class TradingContext:
    engine: ExecutionEngine
    settings: ExecutionSettingsSource
    real_account: Callable[[], bool]
    history: TradeHistory | None = None
    auto_check: Callable[[], str] | None = None  # why Auto may not be switched on, "" if it may


class _Bridge(QObject):
    snapshot = Signal(object)


def _item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def position_row(view: PositionView) -> list[str]:
    side = "Buy" if view.direction == "long" else "Sell"
    profit = "-" if view.profit is None else f"{view.profit:+,.2f}"
    return [
        view.mode,
        str(view.ticket),
        view.symbol,
        side,
        f"{view.volume:g}",
        f"{view.entry:g}",
        f"{view.sl:g}" if view.sl else "none",
        f"{view.tp:g}" if view.tp else "none",
        profit,
        f"{view.best_r:+.2f}",
        f"{view.worst_r:+.2f}",
        view.strategy or "unknown",
        "pending order" if view.pending else "position",
    ]


def mode_change_word(new: OperatingMode, real: bool) -> str:
    """The word the user must type for this change, "" when a plain confirmation is enough."""
    if new is OperatingMode.AUTO:
        return AUTO_WORD
    return REAL_WORD if new.places_real_orders and real else ""


def _typed(parent: QWidget, title: str, text: str) -> str:
    answer, ok = QInputDialog.getText(parent, title, text)
    return answer if ok else ""


class PositionsPage(QWidget):
    def __init__(self, context: TradingContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_positions")
        self.context = context
        self.bridge = _Bridge()
        self.bridge.snapshot.connect(self.show_snapshot, Qt.ConnectionType.QueuedConnection)
        self.last_snapshot: ExecutionSnapshot | None = None
        self.views: list[PositionView] = []
        self.confirm: Confirm = lambda title, text: ask(self, title, text)
        self.typed: TypedAnswer = lambda title, text: _typed(self, title, text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Positions & Trades", "title"))
        layout.addWidget(self._build_mode_row())
        self.mode_note = styled_label("", "muted", wrap=True)
        layout.addWidget(self.mode_note)
        self.state_label = styled_label("Waiting for the MT5 connection.", "muted", wrap=True)
        self.state_label.setObjectName("TradingState")
        layout.addWidget(self.state_label)
        self.status = styled_label("", "muted", wrap=True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("PositionsTabs")
        self.tabs.addTab(self._build_positions(), "Open")
        self.history = TradeHistoryWidget(context.history if context is not None else None)
        self.tabs.addTab(self.history, "History")
        self.tabs.addTab(self._build_settings(), "Execution settings")
        layout.addWidget(self.tabs, 1)
        enabled = context is not None
        for widget in (self.mode, self.save_mode_button, self.kill_button, self.save_button):
            widget.setEnabled(enabled)
        self.resume_button.setEnabled(False)
        self.close_button.setEnabled(False)
        if context is not None:
            self._show_mode(context.settings.mode)
            context.engine.add_listener(self.bridge.snapshot.emit)
            self.show_snapshot(context.engine.snapshot)
        else:
            self.state_label.setText("The execution engine is not running.")

    # Layout ------------------------------------------------------------------------------
    def _build_mode_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(styled_label("Trading mode", "section"))
        self.mode = QComboBox()
        self.mode.setObjectName("TradingMode")
        for mode in MODES:
            self.mode.addItem(mode.label, mode.value)
        self.mode.setToolTip(AUTO_NOTE)
        self.save_mode_button = QPushButton("Use this mode")
        self.save_mode_button.clicked.connect(self.ask_mode_change)
        self.kill_button = QPushButton("Kill switch: close all bot trades")
        self.kill_button.setObjectName("PageKillSwitch")
        self.kill_button.setProperty("variant", "danger")
        self.kill_button.clicked.connect(self.ask_kill)
        self.resume_button = QPushButton("Allow approvals again")
        self.resume_button.clicked.connect(self.resume)
        layout.addWidget(self.mode)
        layout.addWidget(self.save_mode_button)
        layout.addStretch(1)
        layout.addWidget(self.resume_button)
        layout.addWidget(self.kill_button)
        return row

    def _build_positions(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 8, 0, 0)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("PositionsTable")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        row = QHBoxLayout()
        note = "Bot positions and orders (manual trades are not shown)"
        row.addWidget(styled_label(note, "muted"))
        row.addStretch(1)
        self.close_button = QPushButton("Close position")
        self.close_button.clicked.connect(self.close_selected)
        row.addWidget(self.close_button)
        self.messages = QPlainTextEdit()
        self.messages.setObjectName("ExecutionMessages")
        self.messages.setReadOnly(True)
        self.messages.setPlaceholderText("Order and position events appear here.")
        layout.addLayout(row)
        layout.addWidget(self.table, 2)
        layout.addWidget(styled_label("Execution events", "section"))
        layout.addWidget(self.messages, 1)
        return box

    def _build_settings(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 8, 0, 0)
        config = self.context.settings.config if self.context is not None else ExecutionConfig()
        self.form = ParamsForm(ExecutionSettings, config.settings.model_dump(mode="json"))
        layout.addWidget(styled_label("Orders and paper fills", "section"))
        layout.addWidget(self.form)
        title = "Position management (per strategy, all off at 0)"
        layout.addWidget(styled_label(title, "section"))
        self.management: dict[str, ParamsForm] = {}
        for name in ("trend_pullback", "london_breakout"):
            values = config.management_for(name).model_dump(mode="json")
            form = ParamsForm(ManagementSettings, values)
            self.management[name] = form
            layout.addWidget(styled_label(name, "muted"))
            layout.addWidget(form)
        self.save_button = QPushButton("Save execution settings")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        return scroll

    # Snapshots ---------------------------------------------------------------------------
    def show_snapshot(self, snapshot: object) -> None:
        """Slot: a new execution snapshot (from the analysis thread, queued)."""
        if not isinstance(snapshot, ExecutionSnapshot):
            return
        self.last_snapshot = snapshot
        self.views = list(snapshot.positions)
        self.table.setRowCount(len(self.views))
        for row, view in enumerate(self.views):
            for column, text in enumerate(position_row(view)):
                self.table.setItem(row, column, _item(text))
        self.messages.setPlainText("\n".join(snapshot.messages))
        opened = sum(1 for view in self.views if not view.pending)
        pending = len(self.views) - opened
        if snapshot.stopped:
            text = f"Stopped by the kill switch ({snapshot.stopped})."
        else:
            text = f"{snapshot.mode.label} mode: {opened} open, {pending} pending."
        self.state_label.setText(text)
        self.resume_button.setEnabled(self.context is not None and bool(snapshot.stopped))
        self._selection_changed()

    def selected_view(self) -> PositionView | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self.views[index] if 0 <= index < len(self.views) else None

    def _selection_changed(self) -> None:
        view = self.selected_view()
        allowed = self.context is not None and view is not None and not view.pending
        self.close_button.setEnabled(allowed)

    # Actions -----------------------------------------------------------------------------
    def close_selected(self) -> bool:
        view = self.selected_view()
        if view is None or view.pending or self.context is None:
            return False
        question = f"Close {view.symbol} position {view.ticket} ({view.mode}) at the market price?"
        if not self.confirm("Close position?", question):
            return False
        self.context.engine.request_close(view.mode, view.ticket)
        audit("position close requested", before=None, after={"ticket": view.ticket})
        self.status.setText("Closing: sent on the next analysis cycle.")
        return True

    def ask_kill(self) -> bool:
        if self.context is None or not self.confirm("Kill switch", KILL_TEXT):
            return False
        return self.kill()

    def kill(self) -> bool:
        if self.context is None:
            return False
        self.context.engine.request_kill("kill switch pressed")
        audit("kill switch pressed", before=None, after={"mode": self.context.settings.mode.value})
        self.status.setText("Kill switch: closing bot positions and cancelling bot orders now.")
        return True

    def resume(self) -> None:
        if self.context is None:
            return
        self.context.engine.resume()
        audit("approvals allowed again after the kill switch", before=None, after=None)
        self.status.setText("Approvals allowed again. Re-enable new entries on the Risk page.")
        self.resume_button.setEnabled(False)

    def auto_block(self) -> str:
        """Why Auto may not be switched on now, "" when it may."""
        if self.context is None or self.context.auto_check is None:
            return AUTO_NOTE
        return self.context.auto_check()

    def ask_mode_change(self) -> bool:
        if self.context is None:
            return False
        new = OperatingMode(str(self.mode.currentData()))
        if new is OperatingMode.AUTO:
            block = self.auto_block()
            if block:
                self.status.setText(f"Auto not switched on: {block}")
                self._show_mode(self.context.settings.mode)
                return False
        word = mode_change_word(new, self.context.real_account())
        if word:
            if new is OperatingMode.AUTO:
                title, text = "Auto mode", AUTO_TEXT.format(word=word)
            else:
                title = "Real money"
                text = (
                    f"This is a REAL account: approved signals will place real orders.\n"
                    f"Type {word} to switch to {new.label} mode:"
                )
            typed = self.typed(title, text)
            if typed.strip() != word:
                self.status.setText(f"Mode not changed: type {word} exactly.")
                self._show_mode(self.context.settings.mode)
                return False
        elif not self.confirm("Change the trading mode?", MODE_NOTES[new]):
            self._show_mode(self.context.settings.mode)
            return False
        return self.change_mode(new)

    def change_mode(self, new: OperatingMode) -> bool:
        if self.context is None:
            self.status.setText(AUTO_NOTE)
            return False
        if new is OperatingMode.AUTO:
            block = self.auto_block()
            if block:
                self.status.setText(f"Auto not switched on: {block}")
                return False
        before = self.context.settings.config
        config = before.model_copy(update={"mode": new})
        self.context.settings.save(config)
        audit("trading mode changed", before=before.mode.value, after=new.value)
        self._show_mode(new)
        self.status.setText(f"Trading mode: {new.label}.")
        return True

    def _show_mode(self, mode: OperatingMode) -> None:
        index = self.mode.findData(mode.value)
        if index >= 0:
            self.mode.setCurrentIndex(index)
        self.mode_note.setText(f"{MODE_NOTES.get(mode, '')} {AUTO_NOTE}")

    def save(self) -> bool:
        if self.context is None:
            return False
        settings, problems = self.form.validated()
        if problems or not isinstance(settings, ExecutionSettings):
            self.status.setText("Not saved. " + " ".join(problems))
            return False
        rules: dict[str, ManagementSettings] = {}
        for name, form in self.management.items():
            found, issues = form.validated()
            if issues or not isinstance(found, ManagementSettings):
                self.status.setText(f"Not saved ({name}). " + " ".join(issues))
                return False
            rules[name] = found
        before = self.context.settings.config
        config = before.model_copy(update={"settings": settings, "management": rules})
        self.context.settings.save(config)
        audit(
            "execution settings changed",
            before=before.model_dump(mode="json"),
            after=config.model_dump(mode="json"),
        )
        self.status.setText("Saved. Used for the next order and the next management check.")
        return True
