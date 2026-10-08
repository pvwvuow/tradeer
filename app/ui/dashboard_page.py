"""The Dashboard (spec F3 page 1): balance, equity, today's result, open risk and the last
30 days at a glance; the equity and drawdown curve; open positions; the latest signals; how
much of each risk limit is used; the market bias of every watched symbol; and the Go-Live
readiness of the strategies that are on (Phase 13b).

A gain or loss is never shown by color alone (spec F1): results carry an up or down arrow and
a sign, and a limit bar says in words when it is near or at its limit.

The page only reads snapshots (it polls them every two seconds) and the closed trades and the
readiness (every minute); it never changes anything.

8 October 2026: the page scrolls instead of squeezing the lists into thin strips; open
positions and signals are full-width lists of ten rows a page with Previous and Next; every
signal is listed (not only the newest eight) with its strategy, side, prices and why it was
or was not traded, and a filter shows all, traded, waiting or not traded signals.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.analytics.stats import compute_stats, equity_curve
from app.analytics.trades import TradeRecord
from app.domain.signals import Direction, OrderType, SignalRecord, SignalState
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.market_watch import MarketSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.risk.risk_manager import RiskSnapshot
from app.strategies.registry import STRATEGIES
from app.ui.paged_table import PagedTable
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.style import chart_pen, chart_tokens, repolish
from app.ui.tables import number, signed

POLL_MS = 2_000
TRADES_SECONDS = 60.0
DAY = 86_400.0
LATEST_SIGNALS = 8  # before 0.25.2 only this many were shown; now every signal, paged
UP = "\u25b2"
DOWN = "\u25bc"
LIMIT_WORDS = {"warning": " (near the limit)", "loss": " (limit reached)"}
GO_LIVE_NOTE = (
    "Go-Live readiness: the checklist before Auto trades real money is on the Strategies page. "
    "Until a strategy is approved, use Paper, Semi-auto or a demo account."
)
POSITION_COLUMNS = ("Symbol", "Strategy", "Side", "Lots", "Entry", "SL", "TP", "P/L", "Mode")
SIGNAL_COLUMNS = ("Time (UTC)", "Strategy", "Symbol", "Side", "Entry", "SL", "TP", "State")
NO_POSITIONS = "No open positions or pending orders of the bot."
NO_SIGNALS = "No signals yet. They appear here when a strategy finds a setup on a closed bar."
TRADED = frozenset(
    {
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FILLED,
        SignalState.MANAGED,
        SignalState.CLOSED,
    },
)
WAITING = frozenset({SignalState.NEW, SignalState.PENDING_APPROVAL})
SIGNAL_VIEWS = ("All signals", "Traded", "Waiting for approval", "Not traded")


@dataclass
class DashboardContext:
    execution: Callable[[], ExecutionSnapshot] | None = None
    signals: Callable[[], SignalsSnapshot] | None = None
    risk: Callable[[], RiskSnapshot] | None = None
    market: Callable[[], MarketSnapshot] | None = None
    trades: Callable[[], Sequence[TradeRecord]] | None = None
    go_live: Callable[[], str] | None = None


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%m-%d %H:%M")


def today_result(trades: Sequence[TradeRecord], now: float) -> tuple[float, int]:
    """Net result and count of the trades closed today (UTC)."""
    start = math.floor(now / DAY) * DAY
    closed = [t for t in trades if t.close_time >= start]
    return sum(t.net_profit for t in closed), len(closed)


def last_days(trades: Sequence[TradeRecord], now: float, days: int = 30) -> list[TradeRecord]:
    return [t for t in trades if t.close_time >= now - days * DAY]


def limit_rows(snapshot: RiskSnapshot) -> list[tuple[str, float, float, str]]:
    """(name, used, limit, unit) for the progress bars."""
    usage = snapshot.usage
    limits = snapshot.config.settings
    if usage is None:
        return []
    return [
        ("Daily loss", usage.daily_loss_percent, limits.max_daily_loss_percent, "%"),
        ("Drawdown", usage.drawdown_percent, limits.max_total_drawdown_percent, "%"),
        ("Open risk", usage.open_risk_percent, limits.max_total_open_risk_percent, "%"),
        ("Open trades", float(usage.open_trades), float(limits.max_open_trades), ""),
    ]


def bias_text(snapshot: MarketSnapshot) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for symbol, analysis in sorted(snapshot.analyses.items()):
        trend = analysis.trend
        found.append((symbol, f"{trend.direction.value} ({trend.bias:+.0f})"))
    return found


def strategy_title(name: str) -> str:
    """The strategy's display name, e.g. "Trend pullback"; "-" for an unknown position."""
    kind = STRATEGIES.get(name)
    if kind is not None:
        return str(kind.title)
    return name.replace("_", " ").capitalize() or "-"


def price_text(value: float) -> str:
    """A price as MT5 shows it; "-" when there is none (no SL or TP is 0 in MT5)."""
    if not math.isfinite(value) or value <= 0:
        return "-"
    return f"{value:g}"


def position_row(view: PositionView) -> list[str]:
    side = "Buy" if view.direction in ("buy", "long") else "Sell"
    return [
        view.symbol,
        strategy_title(view.strategy),
        side + (" (pending order)" if view.pending else ""),
        f"{view.volume:g}",
        price_text(view.entry),
        price_text(view.sl),
        price_text(view.tp),
        "-" if view.pending else signed(view.profit),
        view.mode,
    ]


def ordered_positions(views: Sequence[PositionView]) -> list[PositionView]:
    """Open positions first, then pending orders; each by symbol and ticket."""
    return sorted(views, key=lambda view: (view.pending, view.symbol, view.ticket))


def signal_view(state: SignalState) -> str:
    """Which filter group a state belongs to (one of `SIGNAL_VIEWS` after the first)."""
    if state in TRADED:
        return "Traded"
    if state in WAITING:
        return "Waiting for approval"
    return "Not traded"


def shows(view: str, state: SignalState) -> bool:
    return view == SIGNAL_VIEWS[0] or signal_view(state) == view


def state_text(record: SignalRecord) -> str:
    """The state in words with its reason, e.g. "filtered out: trading session"."""
    signal = record.signal
    words = signal.state.value.replace("_", " ").lower()
    if signal.state in (SignalState.FILTERED_OUT, SignalState.RISK_REJECTED):
        reason = record.reject_reason
    else:
        reason = signal.history[-1].reason if signal.history else ""
    return f"{words}: {reason}" if reason else words


def signal_row(record: SignalRecord) -> list[str]:
    signal = record.signal
    side = "Buy" if signal.direction is Direction.LONG else "Sell"
    if signal.order_type is not OrderType.MARKET:
        side += f" {signal.order_type.value}"
    return [
        _utc(signal.created_at),
        strategy_title(signal.strategy),
        signal.symbol,
        side,
        signal.price(signal.entry),
        signal.price(signal.sl),
        signal.price(signal.tp),
        state_text(record),
    ]


def signal_tip(record: SignalRecord) -> str:
    """The full story on hover: the signal, why it came and what became of it."""
    signal = record.signal
    lines = [f"{signal.summary()} [{strategy_title(signal.strategy)}]", signal.reason]
    lines.append(f"State: {state_text(record)}")
    return "\n".join(line for line in lines if line)


class _Kpi(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setProperty("role", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        layout.addWidget(styled_label(title, "crumb"))
        self.value = styled_label("n/a", "kpi")
        layout.addWidget(self.value)

    def set_text(self, text: str, tone: float | None = None) -> None:
        """Show the value; a signed `tone` adds an up or down arrow and colors it.

        The arrow keeps the meaning when the color cannot be seen (spec F1).
        """
        role = "kpi"
        if tone is not None and math.isfinite(tone) and tone != 0:
            role = "kpi_profit" if tone > 0 else "kpi_loss"
            text = f"{UP if tone > 0 else DOWN} {text}"
        self.value.setText(text)
        if self.value.property("role") != role:
            self.value.setProperty("role", role)
            repolish(self.value)


def limit_tone(share: float) -> str:
    """The bar color of a risk limit: calm, then amber from 75% used and red at the limit."""
    if share >= 100.0:
        return "loss"
    if share >= 75.0:
        return "warning"
    return "accent"


class DashboardPage(QWidget):
    def __init__(self, context: DashboardContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_dashboard")
        self.context = context or DashboardContext()
        self.trades: list[TradeRecord] = []
        self._trades_at = -math.inf
        self._go_live_at = -math.inf
        self._signals_snapshot: SignalsSnapshot | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("DashboardScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll_area)
        body = QWidget()
        body.setObjectName("DashboardBody")
        self.scroll_area.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Dashboard", "title"))
        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        self.kpis: dict[str, _Kpi] = {}
        for name in ("Balance", "Equity", "Today", "Open risk", "Last 30 days"):
            card = _Kpi(name)
            card.setObjectName(f"Kpi{name.replace(' ', '')}")
            self.kpis[name] = card
            kpis.addWidget(card)
        layout.addLayout(kpis)
        middle = QHBoxLayout()
        middle.setSpacing(12)
        equity_card, equity_layout = card_frame()
        equity_layout.addWidget(styled_label("EQUITY (CLOSED TRADES)", "crumb"))
        self.equity_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.equity_plot.setMinimumHeight(220)
        equity_layout.addWidget(self.equity_plot, 1)
        middle.addWidget(equity_card, 3)
        limits = QFrame()
        limits.setProperty("role", "card")
        self.limits_layout = QGridLayout(limits)
        self.limits_layout.setContentsMargins(18, 14, 18, 14)
        self.limits_layout.setVerticalSpacing(6)
        self.limits_layout.addWidget(styled_label("RISK LIMITS USED", "crumb"), 0, 0)
        self.bars: dict[str, QProgressBar] = {}
        self.bar_labels: dict[str, QLabel] = {}
        for row, name in enumerate(("Daily loss", "Drawdown", "Open risk", "Open trades")):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setProperty("slim", True)
            bar.setProperty("tone", "accent")
            bar.setObjectName(f"Limit{name.replace(' ', '')}")
            text = styled_label(f"{name}: n/a", "muted")
            self.bars[name] = bar
            self.bar_labels[name] = text
            self.limits_layout.addWidget(text, row * 2 + 1, 0)
            self.limits_layout.addWidget(bar, row * 2 + 2, 0)
        self.limits_layout.setRowStretch(9, 1)
        middle.addWidget(limits, 1)
        layout.addLayout(middle)

        positions_card, positions_layout = card_frame()
        self.positions_title = styled_label("OPEN POSITIONS", "crumb")
        positions_layout.addWidget(self.positions_title)
        self.positions_list = PagedTable(POSITION_COLUMNS, stretch=1, empty=NO_POSITIONS)
        self.positions_list.setObjectName("DashboardPositionsList")
        self.positions = self.positions_list.table
        self.positions.setObjectName("DashboardPositions")
        positions_layout.addWidget(self.positions_list)
        layout.addWidget(positions_card)

        signals_card, signals_layout = card_frame()
        top = QHBoxLayout()
        self.signals_title = styled_label("LATEST SIGNALS", "crumb")
        top.addWidget(self.signals_title)
        top.addStretch(1)
        self.signal_filter = QComboBox()
        self.signal_filter.setObjectName("DashboardSignalFilter")
        self.signal_filter.setAccessibleName("Show signals")
        self.signal_filter.addItems(list(SIGNAL_VIEWS))
        self.signal_filter.currentTextChanged.connect(self._filter_changed)
        top.addWidget(self.signal_filter)
        signals_layout.addLayout(top)
        self.signals_list = PagedTable(SIGNAL_COLUMNS, empty=NO_SIGNALS)
        self.signals_list.setObjectName("DashboardSignalsList")
        self.signals = self.signals_list.table
        self.signals.setObjectName("DashboardSignals")
        signals_layout.addWidget(self.signals_list)
        layout.addWidget(signals_card)

        self.bias = styled_label("Market bias: waiting for the analysis.", "muted", wrap=True)
        self.bias.setObjectName("DashboardBias")
        layout.addWidget(self.bias)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("DashboardStatus")
        layout.addWidget(self.status)
        self.go_live_label = styled_label(GO_LIVE_NOTE, "muted", wrap=True)
        self.go_live_label.setObjectName("DashboardGoLive")
        layout.addWidget(self.go_live_label)
        layout.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        context = self.context
        if context.trades is not None and abs(moment - self._trades_at) >= TRADES_SECONDS:
            self._trades_at = moment
            try:
                self.trades = list(context.trades())
            except Exception as error:
                self.status.setText(f"Trades could not be read: {type(error).__name__}")
            self._show_trades(moment)
        if context.go_live is not None and abs(moment - self._go_live_at) >= TRADES_SECONDS:
            self._go_live_at = moment
            self._show_go_live(context.go_live)
        risk = context.risk() if context.risk is not None else None
        self._show_account(risk, moment)
        if context.execution is not None:
            self._show_positions(context.execution())
        if context.signals is not None:
            self._show_signals(context.signals())
        if context.market is not None:
            pairs = bias_text(context.market())
            if pairs:
                self.bias.setText("Market bias: " + "   ".join(f"{s} {t}" for s, t in pairs))

    def _show_go_live(self, readiness: Callable[[], str]) -> None:
        try:
            text = readiness()
        except Exception as error:
            text = f"Go-Live readiness could not be read: {type(error).__name__}"
        self.go_live_label.setText(text)

    def _show_account(self, risk: RiskSnapshot | None, now: float) -> None:
        usage = risk.usage if risk is not None else None
        currency = usage.currency if usage is not None else ""
        if usage is not None:
            self.kpis["Balance"].set_text(f"{number(usage.balance)} {currency}".strip())
            self.kpis["Equity"].set_text(f"{number(usage.equity)} {currency}".strip())
            risk_text = f"{usage.open_risk_percent:.2f}% ({usage.open_trades} open)"
            self.kpis["Open risk"].set_text(risk_text)
        net, count = today_result(self.trades, now)
        self.kpis["Today"].set_text(f"{net:+,.2f} ({count} closed)", net)
        if risk is None:
            return
        for name, used, limit, unit in limit_rows(risk):
            share = used / limit * 100.0 if limit > 0 else 0.0
            bar = self.bars[name]
            bar.setValue(int(max(0.0, min(100.0, share))))
            tone = limit_tone(share)
            if bar.property("tone") != tone:
                bar.setProperty("tone", tone)
                repolish(bar)
            used_text = f"{used:.2f}{unit}" if unit else f"{used:.0f}"
            limit_text = f"{limit:.2f}{unit}" if unit else f"{limit:.0f}"
            words = LIMIT_WORDS.get(tone, "")
            self.bar_labels[name].setText(f"{name}: {used_text} of {limit_text}{words}")
        if risk.halted and usage is not None:
            self.status.setText(f"New entries are stopped: {usage.halted_reason or risk.halted}")
        elif usage is None:
            self.status.setText(risk.message)
        else:
            self.status.setText("")

    def _show_trades(self, now: float) -> None:
        recent = last_days(self.trades, now)
        stats = compute_stats(recent, minimum_trades=0)
        self.kpis["Last 30 days"].set_text(
            f"{stats.net_profit:+,.2f}, win rate {stats.win_rate * 100:.0f}% ({stats.trades})",
            stats.net_profit,
        )
        self.equity_plot.clear()
        curve = equity_curve(self.trades, 0.0)
        if len(curve.times):
            pen = chart_pen(chart_tokens().accent, 2.0)
            self.equity_plot.plot(curve.times, curve.equity, pen=pen)

    def _show_positions(self, snapshot: ExecutionSnapshot) -> None:
        views = ordered_positions(snapshot.positions)
        self.positions_list.set_rows([position_row(view) for view in views])
        opened = sum(1 for view in views if not view.pending)
        title = "OPEN POSITIONS"
        if views:
            title += f" ({opened} open, {len(views) - opened} pending)"
        self.positions_title.setText(title)

    def _show_signals(self, snapshot: SignalsSnapshot) -> None:
        self._signals_snapshot = snapshot
        view = self.signal_filter.currentText() or SIGNAL_VIEWS[0]
        records = [r for r in snapshot.signals if shows(view, r.signal.state)]
        self.signals_list.set_rows(
            [signal_row(record) for record in records],
            [signal_tip(record) for record in records],
        )
        title = "LATEST SIGNALS"
        if snapshot.signals:
            title += f" ({len(records)} of {len(snapshot.signals)})"
        self.signals_title.setText(title)

    def _filter_changed(self, _text: str) -> None:
        self.signals_list.show_page(0)
        if self._signals_snapshot is not None:
            self._show_signals(self._signals_snapshot)
