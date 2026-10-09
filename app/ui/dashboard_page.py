"""The Dashboard (spec F3 page 1), drawn like the owner's No Curve v2 design (20c).

The title row carries the trading mode (PAPER, SEMI-AUTO, AUTO with its lock, ANALYSIS).
Below it a strip of four figures: equity with the mode, today, the win rate of the last 30
days with its range, and the drawdown against its cap. Then two columns. The wide one has
the equity curve of the closed trades (1D, 1W, 1M) with the drawdown band, the open
positions and pending orders (symbol, side, lots, entry, the live price, the stop on the
server, the result) and the decision trace of every signal (time, symbol, side, win
probability, the five decision steps and the result). The 332 px side has the market
sessions on a UTC scale, the risk limits in the design's order, the system health, the
Go-Live checklist (the seven gate checks over every strategy that is on, then the
approval) and, an extra of the app, the market direction of every watched symbol.

Everything is real: figures come from the risk, execution, market and signal snapshots (read
every two seconds) and from the closed trades and the Go-Live desk (every minute). Where the
app does not know a number yet it shows a dash and says why. The page never changes
anything: the mode is changed on the Positions page, with its checks.

A gain or loss is never shown by color alone (spec F1): money carries its sign, a limit bar
says "near the limit" in words, and a failed decision step is a cross, not only red.

In Persian the page runs right to left like the design; numbers, symbols, charts and the
time scale stay left to right, and digits stay Latin as the design writes them.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.analysis.structure import Trend
from app.analytics.stats import compute_stats, equity_curve
from app.analytics.trades import TradeRecord
from app.domain.modes import OperatingMode
from app.domain.signals import Direction, OrderType, SignalRecord, SignalState
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.go_live_desk import APPROVAL, GoLiveChecklist
from app.engine.market_watch import MarketSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.models import Quote
from app.risk.risk_manager import RiskSnapshot
from app.strategies.registry import STRATEGIES
from app.ui.navigation import page_by_id
from app.ui.pages import PageHeader
from app.ui.shell import Segmented, ticker_symbol
from app.ui.tables import number, signed
from app.ui.theme import DARK, ThemeTokens
from app.ui.v2 import (
    Banner,
    Cell,
    CheckLine,
    Checklist,
    Column,
    DesignTable,
    EquityChart,
    IconText,
    InfoRow,
    KpiCell,
    KpiStrip,
    Meter,
    ModeSegment,
    Pipeline,
    Row,
    Section,
    SessionsBar,
    StatusRow,
    Tag,
    drawdowns,
    empty_box,
    meter_tone,
    v2_label,
)

POLL_MS = 2_000
TRADES_SECONDS = 60.0
DAY = 86_400.0
SIDE_WIDTH = 332
COLUMN_GAP = 28
BLOCK_GAP = 32
EQUITY_SAMPLES = 60  # equity readings kept while no trade has closed yet
MAX_RISK_PER_TRADE = 1.0  # the hard cap of RiskSettings.risk_per_trade_percent
Z_95 = 1.96
UP = "\u25b2"
DOWN = "\u25bc"
MINUS = "\u2212"
DASH = "\u2014"
DOT = " \u00b7 "
LIMIT_WORDS = {"warning": " (near the limit)", "loss": " (limit reached)"}
RANGES: dict[str, float] = {"1D": DAY, "1W": 7 * DAY, "1M": 30 * DAY}
KPI_NAMES = ("Equity", "Today", "Win rate", "Max drawdown")
KPI_CAPTIONS = {
    "Equity": "EQUITY",
    "Today": "TODAY",
    "Win rate": "WIN RATE (EST.)",
    "Max drawdown": "MAX DRAWDOWN",
}
LIMIT_NAMES = ("Open risk", "Open trades", "Daily loss", "Trade size")
HEALTH_NAMES = ("MT5 connection", "Local database", "Cloud sync", "Update")
STEP_NAMES = ("Trend", "Risk", "Spread", "News", "Order")
MODES: tuple[OperatingMode, ...] = (
    OperatingMode.PAPER,
    OperatingMode.SEMI_AUTO,
    OperatingMode.AUTO,
    OperatingMode.ANALYSIS_ONLY,
)
MODE_WORDS: dict[OperatingMode, str] = {
    OperatingMode.PAPER: "PAPER",
    OperatingMode.SEMI_AUTO: "SEMI-AUTO",
    OperatingMode.AUTO: "AUTO",
    OperatingMode.ANALYSIS_ONLY: "ANALYSIS",
}
CHECK_NAMES: dict[str, str] = {
    "walk_forward": "Walk-forward backtest",
    "paper": "Paper trades",
    "slippage": "Paper slippage",
    "calibration": "Win chance calibration",
    "health": "No errors for 7 days",
    "health_now": "Health checks green now",
    "risk": "Risk settings reviewed",
    APPROVAL: "Typed AUTO approval",
}
GO_LIVE_NOTE = (
    "Until the Go-Live checklist is approved only Paper, Semi-auto and a demo account are "
    "available."
)
NO_POSITIONS = "The bot has no open positions or pending orders."
NO_SIGNALS = "No signals yet. They appear here as each candle closes."
NO_CURVE = "Connect MT5 to see the curve."
NO_TRADES = "No closed trades in this period yet."
NO_RATE = "No closed trades in the last 30 days yet."
UNKNOWN = "Not known yet"
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
CURRENCY_SIGNS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5"}
POSITION_COLUMNS = ("Symbol", "Strategy", "Side", "Lots", "Entry", "SL", "TP", "P/L", "Mode")
SIGNAL_COLUMNS = ("Time (UTC)", "Strategy", "Symbol", "Side", "Entry", "SL", "TP", "State")

COUNTED = re.compile(r"(\S+) (waiting|refused|ready)")
COUNTED_FA = {"waiting": "در صف", "refused": "رد شده", "ready": "آماده"}

# The page in Persian, keyed by the English (the design's words).
DASH_FA: dict[str, str] = {
    "Dashboard": "داشبورد",
    "Balance": "موجودی",
    "Equity": "اکوییتی",
    "Today": "امروز",
    "Not known yet": "هنوز معلوم نیست",
    "Equity curve": "منحنی سرمایه",
    "Open positions": "معاملات باز",
    "Signal decision trace": "ردپای تصمیم سیگنال‌ها",
    "All signals": "همه‌ی سیگنال‌ها",
    "Traded": "معامله‌شده",
    "Waiting for approval": "منتظر تأیید",
    "Not traded": "معامله‌نشده",
    "Market sessions (UTC)": "جلسات بازار (UTC)",
    "Risk limits": "محدودیت‌های ریسک",
    "Open risk": "ریسک باز",
    "Open trades": "معاملات باز",
    "Daily loss": "افت روزانه",
    "Trade size": "اندازه هر معامله",
    "near the limit": "نزدیک سقف",
    "limit reached": "به سقف رسید",
    "Market direction": "جهت بازار",
    "Market direction is not a signal.": "جهت بازار سیگنال نیست.",
    "Waiting for the market analysis.": "منتظر تحلیل بازار.",
    "System health": "سلامت سیستم",
    "MT5 connection": "اتصال MT5",
    "Local database": "پایگاه داده محلی",
    "Cloud sync": "همگام‌سازی ابری",
    "Update": "به‌روزرسانی",
    "Go to Risk": "رفتن به ریسک",
    "Checklist": "چک‌لیست",
    "Open the Risk page": "باز کردن صفحه‌ی ریسک",
    "Open the Health page": "باز کردن صفحه‌ی سلامت",
    "Open Go-Live on the Strategies page": "باز کردن Go-Live در صفحه‌ی استراتژی‌ها",
    "Open the Positions page": "باز کردن صفحه‌ی موقعیت‌ها",
    "The mode is changed on the Positions page.": "حالت در صفحه‌ی موقعیت‌ها عوض می‌شود.",
    "Auto is locked: ": "AUTO قفل است: ",
    "Walk-forward backtest": "بک‌تست walk-forward",
    "Paper trades": "معاملات Paper",
    "Paper slippage": "لغزش قیمت در Paper",
    "Win chance calibration": "کالیبراسیون احتمال برد",
    "No errors for 7 days": "بدون خطا در ۷ روز",
    "Health checks green now": "سلامت سیستم همین حالا",
    "Risk settings reviewed": "مرور تنظیمات ریسک",
    "Typed AUTO approval": "تأیید تایپی AUTO",
    "No strategy is on.": "هیچ استراتژی‌ای روشن نیست.",
    "Go-Live is not known yet.": "وضعیت Go-Live هنوز معلوم نیست.",
    GO_LIVE_NOTE: "تا تأیید چک‌لیست Go-Live فقط Paper، Semi-auto و حساب دمو در دسترس است.",
    "New entries are stopped.": "ورود تازه متوقف است.",
    NO_POSITIONS: "ربات موقعیت باز یا سفارش در انتظاری ندارد.",
    NO_SIGNALS: "هنوز سیگنالی نیست. با بسته شدن هر کندل، سیگنال‌ها این‌جا می‌آیند.",
    NO_CURVE: "برای دیدن منحنی، MT5 را وصل کنید.",
    NO_TRADES: "در این بازه هنوز معامله‌ی بسته‌شده‌ای نیست.",
    NO_RATE: "در ۳۰ روز اخیر هنوز معامله‌ی بسته‌شده‌ای نیست.",
    "Buy": "خرید",
    "Sell": "فروش",
    "Pending order": "سفارش در انتظار",
    "Filtered out": "رد شد",
    "Rejected by risk": "رد ریسک",
    "Rejected by you": "رد شما",
    "Expired": "منقضی",
    "Order failed": "سفارش ناموفق",
    "Up": "صعودی",
    "Mildly up": "صعودی ملایم",
    "Flat": "خنثی",
    "Mildly down": "نزولی ملایم",
    "Down": "نزولی",
    "Trend": "روند",
    "Risk": "ریسک",
    "Spread": "اسپرد",
    "News": "خبر",
    "Order": "سفارش",
    "OK": "OK",
    "OFF": "OFF",
    "Off": "خاموش",
    "Connecting": "در حال اتصال",
    "Failed": "ناموفق",
    "Unknown": "نامعلوم",
    "Demo account": "حساب دمو",
    "Real account": "حساب واقعی",
    "Contest account": "حساب مسابقه",
    "Up to date": "LATEST",
    "Uploading": "در حال آپلود",
    "Signed out": "خارج شده",
    "Offline": "بدون اینترنت",
    "Project paused": "پروژه متوقف",
    "Setup needed": "نیاز به راه‌اندازی",
    "Error": "خطا",
}


@dataclass
class DashboardContext:
    execution: Callable[[], ExecutionSnapshot] | None = None
    signals: Callable[[], SignalsSnapshot] | None = None
    risk: Callable[[], RiskSnapshot] | None = None
    market: Callable[[], MarketSnapshot] | None = None
    trades: Callable[[], Sequence[TradeRecord]] | None = None
    go_live: Callable[[], str] | None = None
    # The Go-Live checklist; when not given, the desk behind `go_live` gives it.
    go_live_checklist: Callable[[], GoLiveChecklist] | None = None


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%m-%d %H:%M")


def _clock(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%H:%M")


def today_result(trades: Sequence[TradeRecord], now: float) -> tuple[float, int]:
    """Net result and count of the trades closed today (UTC)."""
    start = math.floor(now / DAY) * DAY
    closed = [t for t in trades if t.close_time >= start]
    return sum(t.net_profit for t in closed), len(closed)


def last_days(trades: Sequence[TradeRecord], now: float, days: int = 30) -> list[TradeRecord]:
    return [t for t in trades if t.close_time >= now - days * DAY]


def limit_rows(snapshot: RiskSnapshot) -> list[tuple[str, float, float, str]]:
    """(name, used, limit, unit) of the risk bars, in the design's order."""
    usage = snapshot.usage
    limits = snapshot.config.settings
    if usage is None:
        return []
    return [
        ("Open risk", usage.open_risk_percent, limits.max_total_open_risk_percent, "%"),
        ("Open trades", float(usage.open_trades), float(limits.max_open_trades), ""),
        ("Daily loss", usage.daily_loss_percent, limits.max_daily_loss_percent, "%"),
        ("Trade size", limits.risk_per_trade_percent, MAX_RISK_PER_TRADE, "%"),
    ]


def limit_tone(share: float) -> str:
    """The bar color of a risk limit: calm, then amber from 75% used and red at the limit."""
    if share >= 100.0:
        return "loss"
    if share >= 75.0:
        return "warning"
    return "accent"


def wilson_range(wins: int, trades: int, z: float = Z_95) -> tuple[float, float]:
    """The 95% range of a win rate from `wins` of `trades` (Wilson score interval)."""
    if trades <= 0:
        return math.nan, math.nan
    rate = wins / trades
    base = 1 + z * z / trades
    middle = (rate + z * z / (2 * trades)) / base
    half = z * math.sqrt(rate * (1 - rate) / trades + z * z / (4 * trades * trades)) / base
    return max(0.0, middle - half), min(1.0, middle + half)


def running_win_rate(trades: Sequence[TradeRecord]) -> list[float]:
    """The win rate after each closed trade, oldest first (the win rate sparkline)."""
    found: list[float] = []
    wins = 0
    for index, trade in enumerate(sorted(trades, key=lambda t: t.close_time), start=1):
        wins += 1 if trade.net_profit > 0 else 0
        found.append(wins / index)
    return found


def direction_words(bias: float, trend: Trend) -> tuple[str, str, str]:
    """(arrow, word, tone) of a market bias, e.g. ("\u2197", "Mildly up", "profit")."""
    if trend is Trend.UP:
        return ("\u2191", "Up", "profit") if bias >= 50 else ("\u2197", "Mildly up", "profit")
    if trend is Trend.DOWN:
        return ("\u2193", "Down", "loss") if bias <= -50 else ("\u2198", "Mildly down", "loss")
    return "\u2192", "Flat", ""


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


def money(value: float, currency: str = "USD") -> str:
    """Money as the design writes it: "+$12.40", "\u2212$8.20" (a true minus sign)."""
    sign = "+" if value > 0 else MINUS if value < 0 else ""
    unit = CURRENCY_SIGNS.get(currency, "")
    text = f"{sign}{unit}{abs(value):,.2f}"
    return text if unit or not currency else f"{text} {currency}"


def percent(value: float) -> str:
    """A limit as the design writes it: 2%, 1.5%, 0.25%."""
    return f"{value:g}%"


def is_buy(view: PositionView) -> bool:
    return view.direction in ("buy", "long")


def position_row(view: PositionView) -> list[str]:
    side = "Buy" if is_buy(view) else "Sell"
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


def quote_for(symbol: str, quotes: Mapping[str, Quote]) -> Quote | None:
    """The live quote of a position's symbol ("EURUSD.m" finds the watched "EURUSD")."""
    found = quotes.get(symbol)
    if found is not None:
        return found
    for name, quote in quotes.items():
        if symbol.startswith(name) or name.startswith(symbol):
            return quote
    return None


def last_price(view: PositionView, quotes: Mapping[str, Quote]) -> float:
    """The price the position would close at now: the bid for a buy, the ask for a sell."""
    quote = quote_for(view.symbol, quotes)
    if quote is None:
        return math.nan
    return float(quote.bid if is_buy(view) else quote.ask)


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
    lines.append(f"Win probability: {record.probability.text()}")
    lines.append(f"State: {state_text(record)}")
    return "\n".join(line for line in lines if line)


def pipeline_steps(record: SignalRecord) -> list[int]:
    """The five decision steps of a signal: 1 passed, -1 stopped here, 0 not reached.

    Trend (the strategy's setup and the other filters), Risk, Spread, News and Order, the
    design's order. A filter that stopped the signal is placed by its reason.
    """
    state = record.signal.state
    if state in TRADED:
        return [1, 1, 1, 1, 1]
    if state is SignalState.RISK_REJECTED:
        return [1, -1, 0, 0, 0]
    if state is SignalState.FILTERED_OUT:
        reason = record.reject_reason.lower()
        if "spread" in reason:
            return [1, 1, -1, 0, 0]
        if "news" in reason or "event" in reason:
            return [1, 1, 1, -1, 0]
        return [-1, 0, 0, 0, 0]
    if state in (SignalState.USER_REJECTED, SignalState.EXPIRED, SignalState.FAILED):
        return [1, 1, 1, 1, -1]
    return [1, 1, 1, 1, 0]  # waiting for approval


def result_words(state: SignalState) -> tuple[str, str]:
    """(word, tag tone) of a signal's outcome."""
    if state in TRADED:
        return "Traded", "profit"
    if state in WAITING:
        return "Waiting for approval", "warning"
    words = {
        SignalState.FILTERED_OUT: "Filtered out",
        SignalState.RISK_REJECTED: "Rejected by risk",
        SignalState.USER_REJECTED: "Rejected by you",
        SignalState.EXPIRED: "Expired",
        SignalState.FAILED: "Order failed",
    }
    return words.get(state, "Not traded"), "loss"


def probability_text(record: SignalRecord) -> str:
    """The win chance with its range, "54% (41\u201367)"; a dash while it is unknown."""
    estimate = record.probability
    if estimate.value is None or estimate.low is None or estimate.high is None:
        return DASH
    low, high = round(estimate.low * 100), round(estimate.high * 100)
    return f"{estimate.value * 100:.0f}% ({low}\u2013{high})"


def checklist_lines(found: GoLiveChecklist) -> list[tuple[str, str, str]]:
    """(name, state, detail) of each line: done, open, or locked (the approval while a check
    still fails)."""
    lines: list[tuple[str, str, str]] = []
    for item in found.items:
        name = CHECK_NAMES.get(item.key, item.name)
        state = "done" if item.passed else "open"
        if item.key == APPROVAL and not item.passed and not found.checks_passed:
            state = "locked"
        lines.append((name, state, item.detail))
    return lines


class DashboardPage(QWidget):
    def __init__(
        self,
        context: DashboardContext | None,
        parent: QWidget | None = None,
        persian: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page_dashboard")
        self.right_to_left_ready = True  # the page body follows the window's language
        self.persian = persian
        self.context = context or DashboardContext()
        self.tokens: ThemeTokens = DARK
        self.trades: list[TradeRecord] = []
        self.range = "1M"
        self.account_kind = ""
        self.waiting_count = 0
        self.health_tone = ""
        self.mode = OperatingMode.PAPER
        self.checklist: GoLiveChecklist | None = None
        self.go: Callable[[str], None] = lambda page_id: None
        self._trades_at = -math.inf
        self._go_live_at = -math.inf
        self._signals_snapshot: SignalsSnapshot | None = None
        self._quotes: Mapping[str, Quote] = {}
        self._equity_seen: list[float] = []
        self._currency = "USD"
        self._connected_data = False
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
        layout.setContentsMargins(32, 28, 32, 40)
        layout.setSpacing(26)
        self.header = PageHeader("Dashboard", page_by_id("dashboard").summary, "TRADE")
        self.header.subtitle.setVisible(False)  # the design has no line under the title
        self.mode_segment = ModeSegment(
            [MODE_WORDS[mode] for mode in MODES],
            MODES.index(OperatingMode.AUTO),
        )
        self.mode_segment.setObjectName("Segmented")
        self.mode_segment.setAccessibleName("Trading mode")
        self.mode_segment.setToolTip(self.t("The mode is changed on the Positions page."))
        self.mode_segment.choose(MODES.index(self.mode))
        self.mode_segment.group.idClicked.connect(self._mode_clicked)
        self.header.add_action(self.mode_segment)
        layout.addWidget(self.header)
        self.halt_banner = Banner(self.t("New entries are stopped."), self.t("Go to Risk"), "loss")
        self.halt_banner.setObjectName("DashboardHalted")
        self.halt_banner.clicked.connect(lambda: self.go("risk"))
        self.halt_banner.setVisible(False)
        layout.addWidget(self.halt_banner)
        self.strip = KpiStrip([KPI_CAPTIONS[name] for name in KPI_NAMES])
        self.kpis: dict[str, KpiCell] = dict(zip(KPI_NAMES, self.strip.cells, strict=True))
        for name, cell in self.kpis.items():
            cell.setObjectName(f"Kpi{name.replace(' ', '')}")
            cell.sub.setText(self.t(UNKNOWN))
        layout.addWidget(self.strip)
        grid = QHBoxLayout()
        grid.setSpacing(COLUMN_GAP)
        main = QVBoxLayout()
        main.setSpacing(BLOCK_GAP)
        side = QVBoxLayout()
        side.setSpacing(BLOCK_GAP)
        side_box = QWidget()
        side_box.setFixedWidth(SIDE_WIDTH)
        side_box.setLayout(side)
        grid.addLayout(main, 1)
        grid.addWidget(side_box, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(grid)
        self._build_equity(main)
        self._build_positions(main)
        self._build_signals(main)
        main.addStretch(1)
        self._build_sessions(side)
        self._build_limits(side)
        self._build_health(side)
        self._build_go_live(side)
        self._build_direction(side)
        side.addStretch(1)
        self.status = v2_label("", "note", wrap=True)
        self.status.setObjectName("DashboardStatus")
        layout.addWidget(self.status)
        self.bias = self.direction_note
        layout.addStretch(1)
        if persian:
            body.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.equity_chart.caption_at_end = True
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # Words -----------------------------------------------------------------------------
    def t(self, english: str) -> str:
        """A text of the page in the chosen language."""
        return DASH_FA.get(english, english) if self.persian else english

    def phrase(self, text: str) -> str:
        """A health value: a known word, or "2 waiting" / "0.32.0 ready" in Persian."""
        if not self.persian or text in DASH_FA:
            return self.t(text)
        found = COUNTED.fullmatch(text)
        if found is None:
            return text
        value, word = found.groups()
        return f"{value} {COUNTED_FA[word]}"

    def count(self, value: int) -> str:
        """A count; Latin digits in Persian too, as the design writes them."""
        return str(value)

    # Building --------------------------------------------------------------------------
    def _block(self, column: QVBoxLayout) -> QVBoxLayout:
        """One of the design's blocks: its section row and what follows, 32 px apart."""
        box = QVBoxLayout()
        box.setSpacing(0)
        column.addLayout(box)
        return box

    def _build_equity(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        section = Section(self.t("Equity curve"))
        self.range_segment = Segmented(list(RANGES))
        self.range_segment.setObjectName("Segmented")
        self.range_segment.setProperty("small", "true")
        self.range_segment.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.range_segment.choose(list(RANGES).index(self.range))
        self.range_segment.group.idClicked.connect(self._range_clicked)
        section.add(self.range_segment)
        box.addWidget(section)
        self.equity_chart = EquityChart()
        self.equity_chart.setObjectName("DashboardEquity")
        self.equity_chart.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.equity_chart.setVisible(False)  # shown once there are two closed trades
        self.equity_empty = empty_box(self.t(NO_CURVE))
        box.addWidget(self.equity_chart)
        box.addWidget(self.equity_empty)

    def _build_positions(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        self.positions_title = v2_label("", "cap")
        link = self.t("Open the Positions page")
        section = Section(self.t("Open positions"), Tag("SL ON SERVER"), link)
        section.clicked.connect(lambda: self.go("positions"))
        section.add(self.positions_title)
        box.addWidget(section)
        columns = [
            Column("SYMBOL", width=96),
            Column("SIDE", width=84),
            Column("LOT", width=54),
            Column("ENTRY", share=1.0),
            Column("LAST", share=1.0),
            Column("STOP", share=1.2),
            Column("P/L", width=110),
        ]
        self.positions = DesignTable(columns, self.t(NO_POSITIONS), row_height=52)
        self.positions.setObjectName("DashboardPositions")
        self.positions_list = self.positions
        self.positions.row_clicked.connect(lambda _key: self.go("positions"))
        box.addWidget(self.positions)

    def _build_signals(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        self.signals_title = v2_label("", "cap")
        section = Section(self.t("Signal decision trace"), self.signals_title)
        self.signal_filter = QComboBox()
        self.signal_filter.setObjectName("DashboardSignalFilter")
        self.signal_filter.setProperty("v2", "select")
        self.signal_filter.setAccessibleName("Show signals")
        for view in SIGNAL_VIEWS:
            self.signal_filter.addItem(self.t(view), view)
        self.signal_filter.currentIndexChanged.connect(self._filter_changed)
        section.add(self.signal_filter)
        box.addWidget(section)
        columns = [
            Column("TIME", width=48),
            Column("SYMBOL", width=86),
            Column("SIDE", width=62),
            Column("PROB", width=108),
            Column("PIPELINE", share=1.0),
            Column("RESULT", end=True, width=84),
        ]
        self.signals = DesignTable(columns, self.t(NO_SIGNALS), row_height=56, header=False)
        self.signals.setObjectName("DashboardSignals")
        self.signals_list = self.signals
        self.signals.row_clicked.connect(lambda _key: self.go("signals"))
        box.addWidget(self.signals)

    def _build_sessions(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        box.addWidget(Section(self.t("Market sessions (UTC)")))
        self.sessions = SessionsBar()
        self.sessions.setObjectName("DashboardSessions")
        box.addWidget(self.sessions)

    def _build_limits(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        section = Section(self.t("Risk limits"), link=self.t("Open the Risk page"))
        section.clicked.connect(lambda: self.go("risk"))
        box.addWidget(section)
        self.bars: dict[str, Meter] = {}
        self.bar_labels: dict[str, QLabel] = {}
        self.bar_values: dict[str, QLabel] = {}
        self.bar_tags: dict[str, Tag] = {}
        for index, name in enumerate(LIMIT_NAMES):
            if index:
                box.addSpacing(16)
            top = QHBoxLayout()
            top.setSpacing(8)
            label = v2_label(self.t(name), "row_muted")
            label.setAccessibleName(f"{name}: n/a")
            tag = Tag("", "warning", mono=not self.persian)
            tag.setVisible(False)
            value = v2_label(DASH, "row_num")
            value.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            top.addWidget(label)
            top.addWidget(tag)
            top.addStretch(1)
            top.addWidget(value)
            bar = Meter()
            bar.setObjectName(f"Limit{name.replace(' ', '')}")
            box.addLayout(top)
            box.addSpacing(8)
            box.addWidget(bar)
            self.bars[name] = bar
            self.bar_labels[name] = label
            self.bar_values[name] = value
            self.bar_tags[name] = tag

    def _build_health(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        section = Section(self.t("System health"), link=self.t("Open the Health page"))
        section.clicked.connect(lambda: self.go("health"))
        box.addWidget(section)
        self.health: dict[str, Tag] = {}
        self.health_rows: dict[str, StatusRow] = {}
        for name in HEALTH_NAMES:
            tag = Tag(self.t("Unknown"), "neutral", mono=not self.persian)
            row = StatusRow(self.t(name), tag)
            row.setObjectName(f"Health{name.replace(' ', '')}")
            box.addWidget(row)
            self.health[name] = tag
            self.health_rows[name] = row

    def _build_go_live(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        link = self.t("Open Go-Live on the Strategies page")
        self.go_live_section = Section(self.t("Checklist"), link=link)
        self.go_live_section.clicked.connect(lambda: self.go("strategies"))
        self.go_live_count = v2_label("", "row_num")
        self.go_live_count.setProperty("bold", "true")
        self.go_live_section.add(self.go_live_count)
        box.addWidget(self.go_live_section)
        self.go_live_list = Checklist()
        self.go_live_list.setObjectName("DashboardGoLive")
        self.go_live_list.setVisible(False)
        box.addWidget(self.go_live_list)
        self.go_live_label = v2_label(self.t("Go-Live is not known yet."), "note", wrap=True)
        self.go_live_label.setObjectName("DashboardGoLiveNote")
        box.addWidget(self.go_live_label)

    def _build_direction(self, column: QVBoxLayout) -> None:
        box = self._block(column)
        box.addWidget(Section(self.t("Market direction")))
        self.direction_rows = QVBoxLayout()
        self.direction_rows.setSpacing(0)
        box.addLayout(self.direction_rows)
        self.direction_note = v2_label(self.t("Waiting for the market analysis."), "note", True)
        self.direction_note.setObjectName("DashboardBias")
        box.addWidget(self.direction_note)
        self._direction_shown: list[tuple[str, str, str]] = []

    # Theme -----------------------------------------------------------------------------
    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        for widget in self.findChildren(QWidget):
            apply = getattr(widget, "apply_tokens", None)
            if callable(apply) and widget is not self:
                apply(tokens)

    # Facts the window knows ------------------------------------------------------------
    def set_health(self, name: str, text: str, tone: str) -> None:
        """One line of System health, e.g. ("Cloud sync", "2 waiting", "warning")."""
        tag = self.health.get(name)
        if tag is None:
            return
        tag.set(self.phrase(text), tone)
        self.health_rows[name].set_tone(tone)
        tones = {item.tone for item in self.health.values()}
        self.health_tone = "loss" if "loss" in tones else "warning" if "warning" in tones else ""

    def set_account_kind(self, kind: str) -> None:
        """The account kind under the equity: "Demo account" or "Real account"."""
        self.account_kind = kind

    # Refresh ---------------------------------------------------------------------------
    def refresh(self, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        context = self.context
        if context.trades is not None and abs(moment - self._trades_at) >= TRADES_SECONDS:
            self._trades_at = moment
            try:
                self.trades = list(context.trades())
                self.status.setText("")
            except Exception as error:
                self.status.setText(f"Trades could not be read: {type(error).__name__}")
            self._show_trades(moment)
        if abs(moment - self._go_live_at) >= TRADES_SECONDS:
            self._go_live_at = moment
            self._show_go_live()
        if context.market is not None:
            market = context.market()
            self._quotes = market.quotes
            self._show_direction(market)
        risk = context.risk() if context.risk is not None else None
        execution = context.execution() if context.execution is not None else None
        if execution is not None:
            self._show_mode(execution.mode)
        self._show_account(risk, execution, moment)
        if execution is not None:
            self._show_positions(execution)
        if context.signals is not None:
            self._show_signals(context.signals())
        self.sessions.set_time(moment)

    def _checklist_source(self) -> Callable[[], GoLiveChecklist] | None:
        context = self.context
        if context.go_live_checklist is not None:
            return context.go_live_checklist
        desk = getattr(context.go_live, "__self__", None)  # the GoLiveDesk of `readiness`
        found = getattr(desk, "checklist", None)
        return cast(Callable[[], GoLiveChecklist], found) if callable(found) else None

    def _show_go_live(self) -> None:
        source = self._checklist_source()
        readiness = self.context.go_live
        if source is None and readiness is None:
            return
        text = ""
        found: GoLiveChecklist | None = None
        try:
            if readiness is not None:
                text = readiness()
            if source is not None:
                found = source()
        except Exception as error:
            text = f"Go-Live readiness could not be read: {type(error).__name__}"
        self.checklist = found
        self.go_live_section.setToolTip(text or self.go_live_section.link)
        if found is None:
            self.go_live_list.setVisible(False)
            self.go_live_count.setText("")
            self.go_live_label.setText(text or self.t("Go-Live is not known yet."))
            self.go_live_label.setVisible(True)
            self._show_auto_lock()
            return
        lines = [
            CheckLine(self.t(name), state, detail) for name, state, detail in checklist_lines(found)
        ]
        self.go_live_list.set_lines(lines)
        self.go_live_list.setVisible(bool(lines))
        self.go_live_count.setText(f"{found.done}/{found.total}" if lines else "")
        note = self.t(found.note) if found.note else ""
        if lines and not found.passed:
            note = self.t(GO_LIVE_NOTE) if found.real else ""
        self.go_live_label.setText(note)
        self.go_live_label.setVisible(bool(note))
        self.go_live_list.setAccessibleName(
            f"Go-Live checklist: {found.done} of {found.total} done",
        )
        self._show_auto_lock()

    def _show_auto_lock(self) -> None:
        """AUTO wears its lock while the Go-Live desk says Auto may not be switched on."""
        found = self.checklist
        block = found.auto_block if found is not None else "Go-Live is not known yet."
        locked = bool(block) and self.mode is not OperatingMode.AUTO
        self.mode_segment.set_locked(locked)
        auto = self.mode_segment.buttons[MODES.index(OperatingMode.AUTO)]
        auto.setToolTip(self.t("Auto is locked: ") + block if locked else "")

    def _show_mode(self, mode: OperatingMode) -> None:
        if mode is not self.mode:
            self.mode = mode
            self._show_auto_lock()
        if mode in MODES:
            self.mode_segment.choose(MODES.index(mode))
        self.mode_segment.set_locked(self.mode_segment.locked)
        self.kpis["Equity"].caption.setText(f"EQUITY{DOT}{MODE_WORDS.get(mode, 'PAPER')}")

    def _show_account(
        self,
        risk: RiskSnapshot | None,
        execution: ExecutionSnapshot | None,
        now: float,
    ) -> None:
        usage = risk.usage if risk is not None else None
        rows = limit_rows(risk) if risk is not None else []
        if usage is not None and risk is not None:
            self._connected_data = True
            self._currency = usage.currency or self._currency
            equity = self.kpis["Equity"]
            equity.set_text(number(usage.equity))
            kind = self.t(self.account_kind) if self.account_kind else ""
            balance = f"{self.t('Balance')} {number(usage.balance)}"
            parts = (kind, balance, usage.currency)
            equity.sub.setText(DOT.join(part for part in parts if part))
            self._equity_seen = [*self._equity_seen, usage.equity][-EQUITY_SAMPLES:]
            self._show_equity_spark(usage.balance, usage.equity, now)
            cap = risk.config.settings.max_total_drawdown_percent
            fall = self.kpis["Max drawdown"]
            shown = f"{MINUS}{usage.drawdown_percent:.1f}%" if usage.drawdown_percent > 0 else ""
            fall.set_text(shown or "0.0%", color="loss" if shown else "", arrow=True)
            allowed = f"allowed cap {percent(cap)}"
            if self.persian:
                allowed = f"سقف مجاز {percent(cap)}"
            fall.sub.setText(allowed)
            fall.setToolTip("Account drawdown from the equity high (Risk page), not a backtest.")
        net, closed = today_result(self.trades, now)
        today = self.kpis["Today"]
        if usage is not None or self.trades:
            today.set_text(money(net, self._currency), net, arrow=True)
            opened = 0
            if execution is not None:
                opened = sum(1 for view in execution.positions if not view.pending)
            sub = f"{closed} closed{DOT}{opened} open"
            if self.persian:
                sub = f"{opened} معامله باز{DOT}{closed} بسته‌شده"
            today.sub.setText(sub)
        self._show_limits(rows)
        halted = ""
        if risk is not None and risk.halted:
            halted = (usage.halted_reason if usage is not None else "") or str(risk.halted)
        if execution is not None and execution.stopped:
            halted = halted or "kill switch"
        self.halt_banner.setVisible(bool(halted))
        if halted:
            text = self.t("New entries are stopped.")
            self.halt_banner.set_text(text if self.persian else f"{text} {halted}")
            self.halt_banner.setToolTip(halted)
        if risk is not None and usage is None and risk.message:
            self.status.setText(risk.message)

    def _show_equity_spark(self, balance: float, equity: float, now: float) -> None:
        """The balance after each trade of the last 30 days, then the equity now; only the
        readings of this run while no trade has closed yet."""
        recent = sorted(last_days(self.trades, now), key=lambda t: t.close_time)
        cell = self.kpis["Equity"]
        if not recent:
            cell.set_spark(self._equity_seen)
            return
        total = sum(t.net_profit for t in recent)
        start = balance - total
        values = [start]
        for trade in recent:
            values.append(values[-1] + trade.net_profit)
        cell.set_spark([*values, equity])

    def _show_limits(self, rows: Sequence[tuple[str, float, float, str]]) -> None:
        for name, used, limit, unit in rows:
            share = used / limit if limit > 0 else 0.0
            self.bars[name].set_share(share)
            tone = meter_tone(share)
            used_text = f"{used:.1f}{unit}" if unit else f"{used:.0f}"
            if name == "Trade size":
                used_text = percent(used)
            limit_text = percent(limit) if unit else f"{limit:.0f}"
            self.bar_values[name].setText(f"{used_text} / {limit_text}")
            words = {"warning": "near the limit", "loss": "limit reached"}.get(tone, "")
            if name == "Trade size":
                words = ""  # a setting, capped by the app: never over its limit
            tag = self.bar_tags[name]
            tag.set(self.t(words), "loss" if tone == "loss" else "warning")
            tag.setVisible(bool(words))
            english = LIMIT_WORDS.get(limit_tone(share * 100.0), "") if words else ""
            spoken = f"{name}: {used:.2f}{unit} of {limit:.2f}{unit}{english}"
            if not unit:
                spoken = f"{name}: {used:.0f} of {limit:.0f}{english}"
            self.bar_labels[name].setAccessibleName(spoken)
            self.bar_labels[name].setToolTip(spoken)

    def _show_trades(self, now: float) -> None:
        recent = last_days(self.trades, now)
        stats = compute_stats(recent, minimum_trades=0)
        rate = self.kpis["Win rate"]
        if stats.trades:
            wins = round(stats.win_rate * stats.trades)
            low, high = wilson_range(wins, stats.trades)
            rate.set_text(f"{stats.win_rate * 100:.0f}%")
            span = f"{low * 100:.0f}\u2013{high * 100:.0f}%"
            sub = f"range {span}{DOT}{stats.trades} trades"
            if self.persian:
                sub = f"بازه {span}{DOT}{stats.trades} نمونه"
            rate.sub.setText(sub)
            rate.setToolTip("Closed trades of the last 30 days; the range is the 95% interval.")
            rate.set_spark(running_win_rate(recent))
            dips = drawdowns([float(v) for v in equity_curve(recent, 0.0).equity])
            self.kpis["Max drawdown"].set_spark(dips, "loss")
        elif self.trades or self._connected_data:
            rate.set_text(DASH)
            rate.sub.setText(self.t(NO_RATE))
        today = sorted(
            (t for t in self.trades if t.close_time >= math.floor(now / DAY) * DAY),
            key=lambda t: t.close_time,
        )
        values = [0.0]
        for trade in today:
            values.append(values[-1] + trade.net_profit)
        net = values[-1]
        tone = "profit" if net > 0 else "loss" if net < 0 else "neutral"
        self.kpis["Today"].set_spark(values if today else [], tone)
        self._show_curve(now)

    def _show_curve(self, now: float) -> None:
        span = RANGES.get(self.range, RANGES["1M"])
        trades = [t for t in self.trades if t.close_time >= now - span]
        curve = equity_curve(trades, 0.0)
        values = [float(v) for v in curve.equity]
        has_curve = len(values) >= 2
        self.equity_chart.set_values(values)
        self.equity_chart.setVisible(has_curve)
        self.equity_empty.setVisible(not has_curve)
        label = self.equity_empty.findChild(QLabel, "EmptyText")
        if label is not None:
            known = bool(self.trades) or self._connected_data
            label.setText(self.t(NO_TRADES if known else NO_CURVE))

    def _show_positions(self, snapshot: ExecutionSnapshot) -> None:
        views = ordered_positions(snapshot.positions)
        self.positions.set_rows([self._position(view) for view in views])
        opened = sum(1 for view in views if not view.pending)
        title = ""
        if views:
            title = f"{opened} open, {len(views) - opened} pending"
            if self.persian:
                title = f"{opened} باز، {len(views) - opened} در انتظار"
        self.positions_title.setText(title)

    def _position(self, view: PositionView) -> Row:
        buy = is_buy(view)
        word = self.t("Buy" if buy else "Sell")
        arrow = "up" if buy else "down"
        side = Cell(word, key=f"{arrow}{word}", widget=lambda: IconText(arrow, word))
        if view.pending or view.profit is None:
            result = Cell(self.t("Pending order"), tag="warning", mono=not self.persian)
        else:
            profit = view.profit
            tone = "profit" if profit > 0 else "loss" if profit < 0 else ""
            text = money(profit, self._currency)
            mark = "down" if profit < 0 else "up"
            made = partial(IconText, mark, text, tone=tone, mono=True, bold=True)
            result = Cell(text, mono=True, bold=True, tone=tone, key=f"{mark}{tone}", widget=made)
        stop = price_text(view.sl)
        stop_cell = Cell(stop, mono=True)
        if stop != "-":
            locked = partial(IconText, "lock", stop, mono=True, icon_after=True, icon_tone="muted")
            stop_cell = Cell(stop, mono=True, key="lock", widget=locked)
        last = last_price(view, self._quotes)
        cells = (
            Cell(ticker_symbol(view.symbol), mono=True, bold=True),
            side,
            Cell(f"{view.volume:.2f}", mono=True),
            Cell(price_text(view.entry), mono=True),
            Cell(price_text(last) if math.isfinite(last) else DASH, mono=True),
            stop_cell,
            result,
        )
        tip = DOT.join(position_row(view))
        return Row(cells, tip, str(view.ticket))

    def _show_signals(self, snapshot: SignalsSnapshot) -> None:
        self._signals_snapshot = snapshot
        view = str(self.signal_filter.currentData() or SIGNAL_VIEWS[0])
        records = [r for r in snapshot.signals if shows(view, r.signal.state)]
        self.waiting_count = sum(
            1 for r in snapshot.signals if r.signal.state is SignalState.PENDING_APPROVAL
        )
        self.signals.set_rows([self._signal(record) for record in records])
        title = ""
        if snapshot.signals:
            title = f"{len(records)}/{len(snapshot.signals)}"
        self.signals_title.setText(title)

    def _signal(self, record: SignalRecord) -> Row:
        signal = record.signal
        steps = pipeline_steps(record)
        names = [self.t(name) for name in STEP_NAMES]
        marks = {1: "\u2713", -1: "\u2715", 0: "\u2026"}
        tips = [f"{marks[step]} {name}" for name, step in zip(names, steps, strict=True)]
        word, tone = result_words(signal.state)
        side = "BUY" if signal.direction is Direction.LONG else "SELL"
        if signal.order_type is not OrderType.MARKET:
            side += f" {signal.order_type.value.upper()}"
        key = "".join(str(step) for step in steps)
        cells = (
            Cell(_clock(signal.created_at), mono=True, tone="muted"),
            Cell(ticker_symbol(signal.symbol), mono=True, bold=True),
            Cell(side, mono=True, tag="neutral"),
            Cell(probability_text(record), mono=True),
            Cell(key=key, widget=lambda: Pipeline(steps, tips, names)),
            Cell(self.t(word), tag=tone, mono=not self.persian),
        )
        return Row(cells, signal_tip(record), signal.id)

    def _show_direction(self, snapshot: MarketSnapshot) -> None:
        found: list[tuple[str, str, str]] = []
        for symbol, analysis in sorted(snapshot.analyses.items()):
            trend = analysis.trend
            arrow, word, tone = direction_words(trend.bias, trend.direction)
            found.append((symbol, f"{arrow} {self.t(word)}", tone))
        if found == self._direction_shown:
            return
        self._direction_shown = found
        while self.direction_rows.count():
            item = self.direction_rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for symbol, text, tone in found:
            value = v2_label(text, "row")
            value.setProperty("tone", tone)
            row = InfoRow(ticker_symbol(symbol), value)
            row.name.setProperty("v2", "row_num")
            self.direction_rows.addWidget(row)
        note = "Market direction is not a signal." if found else "Waiting for the market analysis."
        self.direction_note.setText(self.t(note))
        pairs = bias_text(snapshot)
        if pairs:
            self.direction_note.setToolTip("   ".join(f"{s} {t}" for s, t in pairs))

    def _mode_clicked(self, _index: int) -> None:
        """The mode is not changed here: the segment shows the real mode and opens the
        Positions page, where the mode is switched with its checks (Go-Live for Auto)."""
        if self.mode in MODES:
            self.mode_segment.choose(MODES.index(self.mode))
        self.go("positions")

    def _range_clicked(self, index: int) -> None:
        self.range = list(RANGES)[index]
        self._show_curve(time.time())

    def _filter_changed(self, _index: int) -> None:
        self.signals.show_page(0)
        if self._signals_snapshot is not None:
            self._show_signals(self._signals_snapshot)
