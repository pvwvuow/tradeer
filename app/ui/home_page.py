"""The Simple view's Home screen (spec B3b, F0): one calm screen for a beginner.

A balance strip, one Trade Suggestion Card at a time (Approve or Skip), a Details expander
with the exact numbers, the open trades with "Close now", a plain-language status line and
"Stop trading now", which is the same kill switch as the Advanced view. On the first start a
panel explains practice money before anything else and asks whether the user is new to
trading (Simple view) or not (Advanced view).

The page reads the same snapshots as the Advanced pages (signals, execution, risk) through
queued Qt signals and acts through the same calls (approve, dismiss, close, kill switch): a
plain-language view over one engine, never a second logic path. Its texts come from
`app.ui.home_model`.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.analysis.sessions import market_open
from app.domain.modes import OperatingMode
from app.domain.signals import SignalRecord
from app.engine.execution import ExecutionSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.models import Quote
from app.observability.logger import audit
from app.risk.risk_manager import RiskSnapshot
from app.strategies.registry import STRATEGIES
from app.ui.home_model import (
    KNOWLEDGE_QUESTION,
    MODE_LINES,
    PRACTICE_TEXT,
    PRACTICE_TITLE,
    SuggestionView,
    TradeRow,
    approve_block_text,
    approve_question,
    balance_view,
    close_question,
    home_status,
    ordered_suggestions,
    price_moved,
    suggestion_view,
    trade_rows,
)
from app.ui.navigation import SIMPLE_HOME
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.positions_page import TradingContext
from app.ui.risk_page import RiskContext
from app.ui.signals_page import SignalsContext, ask
from app.ui.theme import DARK, ThemeTokens

DEFAULT_TOLERANCE_R = 0.25
EMPTY_CONNECTED = (
    "The app tells you here as soon as it finds one. Many hours have no good trade, and that "
    "is normal."
)
EMPTY_DISCONNECTED = "Connect MetaTrader 5 in Settings (top right) so the app can watch the market."
Confirm = Callable[[str, str], bool]


class _Bridge(QObject):
    signals = Signal(object)
    trading = Signal(object)
    risk = Signal(object)


def set_role(label: QLabel, role: str) -> None:
    """Change a label's style role (profit, loss, muted) and restyle it."""
    if label.property("role") == role:
        return
    label.setProperty("role", role)
    label.style().unpolish(label)
    label.style().polish(label)


class Sparkline(QWidget):
    """A tiny line of the last 7 days' running result (spec F0). Drawn, never animated."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WeekSparkline")
        self.setMinimumSize(140, 36)
        self.points: tuple[float, ...] = ()
        self.color = QColor(DARK.accent)

    def set_points(self, points: tuple[float, ...]) -> None:
        self.points = points
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if len(self.points) < 2:
            return
        low, high = min(self.points), max(self.points)
        span = high - low or 1.0
        width = max(1.0, self.width() - 4.0)
        height = max(1.0, self.height() - 4.0)
        step = width / (len(self.points) - 1)
        corners = [
            QPointF(2.0 + index * step, 2.0 + height - (value - low) / span * height)
            for index, value in enumerate(self.points)
        ]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.color)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF(corners))
        painter.end()


class HomePage(QWidget):
    def __init__(
        self,
        signals: SignalsContext | None = None,
        risk: RiskContext | None = None,
        trading: TradingContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(f"page_{SIMPLE_HOME.page_id}")
        self.signals_context = signals
        self.risk_context = risk
        self.trading = trading
        self.confirm: Confirm = lambda title, text: ask(self, title, text)
        self.stop: Callable[[], bool] | None = None
        self.on_onboarded: Callable[[bool], None] | None = None
        self.on_status_bar: Callable[[bool], None] | None = None
        self.quote: Callable[[str], Quote | None] = lambda symbol: None
        self.now: Callable[[], float] = time.time
        self.connected = False
        self.last_signals: SignalsSnapshot | None = None
        self.last_trading: ExecutionSnapshot | None = None
        self.last_risk: RiskSnapshot | None = None
        self.suggestion: SignalRecord | None = None
        self.view: SuggestionView | None = None
        self.rows: list[TradeRow] = []
        self.close_buttons: list[QPushButton] = []
        self._row_keys: list[tuple[str, int, bool]] = []
        self._row_results: list[QLabel] = []
        self._acted: set[str] = set()
        self._asking = False
        self.bridge = _Bridge()
        self.bridge.signals.connect(self.show_signals, Qt.ConnectionType.QueuedConnection)
        self.bridge.trading.connect(self.show_trading, Qt.ConnectionType.QueuedConnection)
        self.bridge.risk.connect(self.show_risk, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(16)
        layout.addWidget(self._build_welcome())
        self.scroll = QScrollArea()
        self.scroll.setObjectName("HomeScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self._build_content())
        layout.addWidget(self.scroll, 1)
        layout.addLayout(self._build_bottom())
        if signals is not None:
            signals.pipeline.add_listener(self.bridge.signals.emit)
            self.last_signals = signals.pipeline.snapshot
        if risk is not None:
            risk.manager.add_listener(self.bridge.risk.emit)
            self.last_risk = risk.manager.snapshot
        if trading is not None:
            trading.engine.add_listener(self.bridge.trading.emit)
            self.last_trading = trading.engine.snapshot
        self.refresh()

    # Building ------------------------------------------------------------------------------
    def _build_welcome(self) -> QFrame:
        self.welcome, layout = card_frame()
        self.welcome.setObjectName("WelcomeCard")
        layout.addWidget(styled_label("Welcome", "title"))
        self.practice_label = styled_label(PRACTICE_TITLE, "brand")
        layout.addWidget(self.practice_label)
        layout.addWidget(styled_label(PRACTICE_TEXT, "muted", wrap=True))
        layout.addWidget(styled_label(KNOWLEDGE_QUESTION, "brand"))
        row = QHBoxLayout()
        self.new_button = QPushButton("I'm new to trading: keep it simple")
        self.new_button.setObjectName("NewToTradingButton")
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(self._welcome_slot(False))
        self.trader_button = QPushButton("I already trade: show the Advanced view")
        self.trader_button.setObjectName("TraderButton")
        self.trader_button.clicked.connect(self._welcome_slot(True))
        row.addWidget(self.new_button)
        row.addWidget(self.trader_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.welcome.setVisible(False)
        return self.welcome

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(styled_label("Home", "title"))
        self.mode_line = styled_label("", "muted", wrap=True)
        self.mode_line.setObjectName("HomeModeLine")
        layout.addWidget(self.mode_line)
        layout.addWidget(self._build_balance())
        layout.addWidget(self._build_card())
        layout.addWidget(self._build_trades())
        layout.addStretch(1)
        return content

    def _build_balance(self) -> QFrame:
        card, layout = card_frame()
        card.setObjectName("BalanceStrip")
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.balance_title = styled_label("Balance", "section")
        self.balance_value = styled_label("not known yet", "title")
        self.balance_value.setObjectName("BalanceValue")
        left.addWidget(self.balance_title)
        left.addWidget(self.balance_value)
        right = QVBoxLayout()
        self.today_label = styled_label("Today: not known yet", "muted")
        self.today_label.setObjectName("TodayResult")
        self.week_label = styled_label("", "muted")
        self.week_label.setObjectName("WeekResult")
        right.addWidget(self.today_label)
        right.addWidget(self.week_label)
        self.sparkline = Sparkline()
        row.addLayout(left)
        row.addStretch(1)
        row.addLayout(right)
        row.addWidget(self.sparkline)
        layout.addLayout(row)
        return card

    def _build_card(self) -> QFrame:
        card, layout = card_frame()
        card.setObjectName("SuggestionCard")
        self.queue_label = styled_label("", "muted")
        self.card_title = styled_label("", "brand")
        self.card_title.setObjectName("SuggestionTitle")
        self.action_label = styled_label("", "title")
        self.action_label.setObjectName("SuggestionAction")
        self.reason_label = styled_label("", "muted", wrap=True)
        self.reason_label.setObjectName("SuggestionReason")
        self.make_label = styled_label("", "profit")
        self.make_label.setObjectName("SuggestionMake")
        self.lose_label = styled_label("", "loss")
        self.lose_label.setObjectName("SuggestionLose")
        self.confidence_label = styled_label("", "brand")
        self.confidence_note = styled_label("", "muted", wrap=True)
        self.ends_label = styled_label("", "muted")
        self.moved_label = styled_label("", "warning", wrap=True)
        self.block_label = styled_label("", "muted", wrap=True)
        buttons = QHBoxLayout()
        self.approve_button = QPushButton("Approve")
        self.approve_button.setObjectName("SimpleApproveButton")
        self.approve_button.setProperty("variant", "primary")
        self.approve_button.clicked.connect(self.approve)
        self.skip_button = QPushButton("Skip")
        self.skip_button.setObjectName("SimpleSkipButton")
        self.skip_button.clicked.connect(self.skip)
        buttons.addWidget(self.approve_button)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.details_button = QPushButton("Show details")
        self.details_button.setObjectName("DetailsButton")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        self.details_label = styled_label("", "muted", wrap=True)
        self.details_label.setObjectName("SuggestionDetails")
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details_label.setVisible(False)
        self.empty_title = styled_label("No trade suggestions right now", "brand")
        self.empty_title.setObjectName("NoSuggestion")
        self.empty_note = styled_label(EMPTY_DISCONNECTED, "muted", wrap=True)
        self._card_labels = [
            self.queue_label,
            self.card_title,
            self.action_label,
            self.reason_label,
            self.make_label,
            self.lose_label,
            self.confidence_label,
            self.confidence_note,
            self.ends_label,
            self.moved_label,
            self.block_label,
        ]
        for label in self._card_labels:
            layout.addWidget(label)
        layout.addLayout(buttons)
        layout.addWidget(self.details_button)
        layout.addWidget(self.details_label)
        layout.addWidget(self.empty_title)
        layout.addWidget(self.empty_note)
        return card

    def _build_trades(self) -> QFrame:
        card, layout = card_frame()
        card.setObjectName("OpenTradesCard")
        layout.addWidget(styled_label("Your open trades", "brand"))
        self.trades_box = QVBoxLayout()
        self.trades_box.setSpacing(0)
        layout.addLayout(self.trades_box)
        self.no_trades = styled_label("No open trades.", "muted")
        layout.addWidget(self.no_trades)
        return card

    def _build_bottom(self) -> QVBoxLayout:
        bottom = QVBoxLayout()
        bottom.setSpacing(8)
        self.state_line = styled_label("", "brand", wrap=True)
        self.state_line.setObjectName("HomeStatus")
        self.status_line = styled_label("Status: not connected", "muted", wrap=True)
        row = QHBoxLayout()
        self.stop_button = QPushButton("Stop trading now")
        self.stop_button.setObjectName("SimpleStopButton")
        self.stop_button.setProperty("variant", "danger")
        self.stop_button.setEnabled(self.trading is not None)
        tip = "Closes the app's trades, cancels its orders and stops new ones (asks first)."
        self.stop_button.setToolTip(tip if self.trading is not None else "Nothing is running yet.")
        self.stop_button.clicked.connect(self.stop_trading)
        self.status_bar_button = QPushButton("Show status bar")
        self.status_bar_button.setObjectName("StatusBarToggle")
        self.status_bar_button.setCheckable(True)
        self.status_bar_button.toggled.connect(self._toggle_status_bar)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        row.addWidget(self.status_bar_button)
        bottom.addWidget(self.state_line)
        bottom.addWidget(self.status_line)
        bottom.addLayout(row)
        return bottom

    # Inputs ---------------------------------------------------------------------------------
    def show_welcome(self, visible: bool) -> None:
        """The first-run panel: practice money first, then Simple or Advanced (spec F0, F2)."""
        self.welcome.setVisible(visible)
        self.scroll.setVisible(not visible)

    def set_connection(self, connected: bool, text: str) -> None:
        self.connected = connected
        self.status_line.setText(text)
        self.refresh()

    def show_signals(self, snapshot: object) -> None:
        if isinstance(snapshot, SignalsSnapshot):
            self.last_signals = snapshot
            waiting = {record.id for record in snapshot.pending()}
            self._acted &= waiting
            self.refresh()

    def show_trading(self, snapshot: object) -> None:
        if isinstance(snapshot, ExecutionSnapshot):
            self.last_trading = snapshot
            self.refresh()

    def show_risk(self, snapshot: object) -> None:
        if isinstance(snapshot, RiskSnapshot):
            self.last_risk = snapshot
            self.refresh()

    def tick(self) -> None:
        """Every second from the window clock: the countdown and the price note."""
        self.refresh()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.sparkline.color = QColor(tokens.accent)
        self.sparkline.update()

    # Showing --------------------------------------------------------------------------------
    def mode(self) -> OperatingMode:
        if self.trading is not None:
            return self.trading.settings.mode
        if self.last_trading is not None:
            return self.last_trading.mode
        return OperatingMode.PAPER

    def refresh(self) -> None:
        if self._asking:
            return  # a question is open: change nothing under it
        now = self.now()
        mode = self.mode()
        self.mode_line.setText(MODE_LINES.get(mode, ""))
        usage = self.last_risk.usage if self.last_risk is not None else None
        currency = usage.currency if usage is not None else ""
        trading = self.last_trading or ExecutionSnapshot(mode=mode)
        balance = balance_view(usage, mode, trading.daily_results, now)
        self.balance_title.setText(balance.title.upper())
        self.balance_value.setText(balance.balance)
        self.today_label.setText(balance.today)
        set_role(self.today_label, balance.today_kind)
        self.week_label.setText(balance.week)
        set_role(self.week_label, balance.week_kind)
        self.sparkline.set_points(balance.points)
        waiting = self._show_suggestion(now, mode, currency)
        capital = usage.capital if usage is not None else math.nan
        self._show_trades(trade_rows(trading, capital, currency))
        open_trades = sum(1 for row in self.rows if not row.pending)
        self.state_line.setText(
            home_status(
                connected=self.connected,
                stopped=trading.stopped,
                halted=usage.halted if usage is not None else "",
                suggestions=waiting,
                open_trades=open_trades,
                market_open=market_open(now),
            ),
        )

    def _show_suggestion(self, now: float, mode: OperatingMode, currency: str) -> int:
        snapshot = self.last_signals
        records = snapshot.pending() if snapshot is not None else []
        ordered = ordered_suggestions(records, self._acted)
        record = ordered[0] if ordered else None
        self.suggestion = record
        shown = record is not None
        for label in self._card_labels:
            label.setVisible(shown)
        self.approve_button.setVisible(shown)
        self.skip_button.setVisible(shown)
        self.details_button.setVisible(shown)
        self.details_label.setVisible(shown and self.details_button.isChecked())
        self.empty_title.setVisible(not shown)
        self.empty_note.setVisible(not shown)
        self.empty_note.setText(EMPTY_CONNECTED if self.connected else EMPTY_DISCONNECTED)
        if record is None or snapshot is None:
            self.view = None
            return 0
        quote = self.quote(record.signal.symbol)
        moved = price_moved(
            record,
            quote.bid if quote is not None else None,
            quote.ask if quote is not None else None,
            self._tolerance(),
        )
        kind = STRATEGIES.get(record.signal.strategy)
        view = suggestion_view(
            record,
            currency,
            now,
            total=len(ordered),
            moved=moved,
            strategy_title=kind.title if kind is not None else "",
        )
        self.view = view
        self.queue_label.setText(view.queue)
        self.queue_label.setVisible(bool(view.queue))
        self.card_title.setText(view.title)
        self.action_label.setText(f"{view.arrow} {view.action}")
        self.reason_label.setText(view.reason)
        self.make_label.setText(view.make)
        self.lose_label.setText(view.lose)
        self.confidence_label.setText(view.confidence)
        self.confidence_note.setText(view.confidence_note)
        self.ends_label.setText(view.ends)
        self.moved_label.setText(view.moved)
        self.moved_label.setVisible(bool(view.moved))
        block = approve_block_text(snapshot.approval_block, mode)
        if self.signals_context is None:
            block = "Approve is not available: the signals are not running."
        self.block_label.setText(block)
        self.block_label.setVisible(bool(block))
        self.approve_button.setEnabled(not block and record.signal.expires_at > now)
        self.skip_button.setEnabled(self.signals_context is not None)
        self.details_label.setText("\n".join(f"{name}: {text}" for name, text in view.details))
        return len(ordered)

    def _show_trades(self, rows: list[TradeRow]) -> None:
        keys = [(row.mode, row.ticket, row.pending) for row in rows]
        self.rows = rows
        if keys == self._row_keys:
            for label, row in zip(self._row_results, rows, strict=True):
                label.setText(row.result)
                set_role(label, row.kind)
            return
        while self.trades_box.count():
            item = self.trades_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._row_keys = keys
        self._row_results = []
        self.close_buttons = []
        for row in rows:
            frame = QFrame()
            frame.setProperty("role", "row")
            line = QHBoxLayout(frame)
            line.setContentsMargins(0, 8, 0, 8)
            line.addWidget(styled_label(row.title, "brand"))
            line.addWidget(styled_label(row.action, "muted"))
            line.addStretch(1)
            result = styled_label(row.result, row.kind)
            line.addWidget(result)
            self._row_results.append(result)
            if not row.pending:
                button = QPushButton("Close now")
                button.setEnabled(self.trading is not None)
                button.clicked.connect(self._close_slot(row.mode, row.ticket))
                line.addWidget(button)
                self.close_buttons.append(button)
            self.trades_box.addWidget(frame)
        self.no_trades.setVisible(not rows)

    def _tolerance(self) -> float:
        if self.trading is None:
            return DEFAULT_TOLERANCE_R
        return self.trading.settings.config.settings.max_entry_move_r

    def default_texts(self) -> list[str]:
        """What the screen shows before Details is opened (the zero-jargon check)."""
        texts = [self.mode_line.text(), self.balance_value.text(), self.today_label.text()]
        texts += [self.week_label.text(), self.state_line.text()]
        if self.view is not None:
            texts += self.view.default_texts()
        else:
            texts += [self.empty_title.text(), self.empty_note.text()]
        for row in self.rows:
            texts += row.texts()
        return [text for text in texts if text]

    # Actions --------------------------------------------------------------------------------
    def _ask(self, title: str, text: str) -> bool:
        self._asking = True
        try:
            return self.confirm(title, text)
        finally:
            self._asking = False

    def approve(self) -> bool:
        """Ask, then hand the suggestion to the same approval as the Signals page."""
        record, view = self.suggestion, self.view
        if record is None or view is None or self.signals_context is None:
            return False
        snapshot = self.last_signals
        if snapshot is None or snapshot.approval_block or record.signal.expires_at <= self.now():
            return False
        if not self._ask("Approve this trade?", approve_question(view, self.mode())):
            return False
        self.signals_context.pipeline.approve(record.id)
        audit("signal approved", before=record.signal.state.value, after=record.id)
        self._acted.add(record.id)
        self.refresh()
        self.state_line.setText("Approved: it is checked again and placed in a few seconds.")
        return True

    def skip(self) -> bool:
        record = self.suggestion
        if record is None or self.signals_context is None:
            return False
        self.signals_context.pipeline.dismiss(record.id)
        audit("signal dismissed", before=record.signal.state.value, after=record.id)
        self._acted.add(record.id)
        self.refresh()
        return True

    def close_trade(self, mode: str, ticket: int) -> bool:
        row = next((r for r in self.rows if r.mode == mode and r.ticket == ticket), None)
        if row is None or row.pending or self.trading is None:
            return False
        if not self._ask("Close this trade?", close_question(row)):
            return False
        self.trading.engine.request_close(mode, ticket)
        audit("position close requested", before=None, after={"ticket": ticket})
        self.state_line.setText("Closing: done in a few seconds.")
        return True

    def stop_trading(self) -> bool:
        """The kill switch, exactly as in the Advanced view (it asks first)."""
        if self.stop is None:
            return False
        self._asking = True
        try:
            stopped = self.stop()
        finally:
            self._asking = False
        self.refresh()
        return stopped

    def answer_welcome(self, advanced: bool) -> None:
        self.show_welcome(False)
        audit("first-run screen answered", before=None, after={"advanced": advanced})
        if self.on_onboarded is not None:
            self.on_onboarded(advanced)

    def _welcome_slot(self, advanced: bool) -> Callable[[], None]:
        def slot() -> None:
            self.answer_welcome(advanced)

        return slot

    def _close_slot(self, mode: str, ticket: int) -> Callable[[], None]:
        def slot() -> None:
            self.close_trade(mode, ticket)

        return slot

    def _toggle_details(self, shown: bool) -> None:
        self.details_button.setText("Hide details" if shown else "Show details")
        self.details_label.setVisible(shown and self.suggestion is not None)

    def _toggle_status_bar(self, shown: bool) -> None:
        self.status_bar_button.setText("Hide status bar" if shown else "Show status bar")
        if self.on_status_bar is not None:
            self.on_status_bar(shown)
