"""The Dashboard (spec F3 page 1): balance, equity, today's result, open risk and the last
30 days at a glance; the equity and drawdown curve; open positions; the latest signals; how
much of each risk limit is used; the market bias of every watched symbol; and the Go-Live
readiness of the strategies that are on (Phase 13b).

The page only reads snapshots (it polls them every two seconds) and the closed trades and the
readiness (every minute); it never changes anything.
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
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.analytics.stats import compute_stats, equity_curve
from app.analytics.trades import TradeRecord
from app.engine.execution import ExecutionSnapshot
from app.engine.market_watch import MarketSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.risk.risk_manager import RiskSnapshot
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.style import repolish
from app.ui.tables import fill_table, make_table, number, signed

POLL_MS = 2_000
TRADES_SECONDS = 60.0
DAY = 86_400.0
LATEST_SIGNALS = 8
GO_LIVE_NOTE = (
    "Go-Live readiness: the checklist before Auto trades real money is on the Strategies page. "
    "Until a strategy is approved, use Paper, Semi-auto or a demo account."
)


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
        """Show the value; a signed `tone` colors it green (gain) or red (loss)."""
        self.value.setText(text)
        role = "kpi"
        if tone is not None and math.isfinite(tone) and tone != 0:
            role = "kpi_profit" if tone > 0 else "kpi_loss"
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
        layout = QVBoxLayout(self)
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
        tables = QHBoxLayout()
        tables.setSpacing(12)
        self.positions = make_table(("Symbol", "Side", "Lots", "Entry", "SL", "TP", "P/L", "Mode"))
        self.positions.setObjectName("DashboardPositions")
        self.signals = make_table(("Time (UTC)", "Symbol", "Signal", "State"))
        self.signals.setObjectName("DashboardSignals")
        for title, table in (("OPEN POSITIONS", self.positions), ("LATEST SIGNALS", self.signals)):
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(styled_label(title, "crumb"))
            column.addWidget(table, 1)
            tables.addLayout(column, 1)
        layout.addLayout(tables, 1)
        self.bias = styled_label("Market bias: waiting for the analysis.", "muted", wrap=True)
        self.bias.setObjectName("DashboardBias")
        layout.addWidget(self.bias)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("DashboardStatus")
        layout.addWidget(self.status)
        self.go_live_label = styled_label(GO_LIVE_NOTE, "muted", wrap=True)
        self.go_live_label.setObjectName("DashboardGoLive")
        layout.addWidget(self.go_live_label)
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
            self.bar_labels[name].setText(f"{name}: {used_text} of {limit_text}")
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
            self.equity_plot.plot(curve.times, curve.equity, pen=pg.mkPen("#5b8cff", width=2))

    def _show_positions(self, snapshot: ExecutionSnapshot) -> None:
        rows = [
            [
                view.symbol + (" (pending)" if view.pending else ""),
                "Buy" if view.direction in ("buy", "long") else "Sell",
                f"{view.volume:g}",
                f"{view.entry:g}",
                f"{view.sl:g}",
                f"{view.tp:g}",
                signed(view.profit),
                view.mode,
            ]
            for view in snapshot.positions
        ]
        fill_table(self.positions, rows)

    def _show_signals(self, snapshot: SignalsSnapshot) -> None:
        rows = [
            [
                _utc(record.signal.created_at),
                record.signal.symbol,
                record.signal.summary(),
                record.signal.state.value.replace("_", " ").lower(),
            ]
            for record in snapshot.signals[:LATEST_SIGNALS]
        ]
        fill_table(self.signals, rows)
