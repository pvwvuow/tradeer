"""The Dashboard (spec F3 page 1), laid out like the owner's UI v2 design (Dashboard.jsx).

At the top a strip of five figures (balance, equity, today, open risk, the last 30 days),
then two columns. The wide one has the equity curve of the closed trades (1D, 1W, 1M) with
the hatched drawdown band, the open positions and pending orders, and the decision trace of
every signal (time, symbol, side, win probability, the five decision steps and the result).
The narrow one has the market sessions on a UTC scale, the risk limits in use, the market
direction of every watched symbol and the system health.

Everything is real: figures come from the risk, execution and signal snapshots (read every
two seconds) and from the closed trades and the Go-Live readiness (every minute). Where the
app does not know a number yet it shows a dash and says why. The page never changes anything.

A gain or loss is never shown by color alone (spec F1): money carries its sign, a limit bar
says "near the limit" in words, and a failed decision step is a cross, not only red.

In Persian (UI v2) the page is Persian and runs right to left, like the design.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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
from app.domain.signals import Direction, OrderType, SignalRecord, SignalState
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.market_watch import MarketSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.risk.risk_manager import RiskSnapshot
from app.strategies.registry import STRATEGIES
from app.ui.navigation import page_by_id
from app.ui.pages import PageHeader
from app.ui.shell import Segmented, persian_digits
from app.ui.tables import number, signed
from app.ui.theme import DARK, ThemeTokens
from app.ui.v2 import (
    Banner,
    Cell,
    Column,
    DesignTable,
    EquityChart,
    InfoRow,
    KpiCell,
    KpiStrip,
    Meter,
    Pipeline,
    Row,
    Section,
    SessionsBar,
    Tag,
    empty_box,
    meter_tone,
    v2_button,
    v2_label,
)

POLL_MS = 2_000
TRADES_SECONDS = 60.0
DAY = 86_400.0
SIDE_WIDTH = 300
EQUITY_SAMPLES = 60  # equity readings kept for the sparkline (two minutes)
UP = "\u25b2"
DOWN = "\u25bc"
MINUS = "\u2212"
LIMIT_WORDS = {"warning": " (near the limit)", "loss": " (limit reached)"}
RANGES: dict[str, float] = {"1D": DAY, "1W": 7 * DAY, "1M": 30 * DAY}
KPI_NAMES = ("Balance", "Equity", "Today", "Open risk", "Last 30 days")
LIMIT_NAMES = ("Daily loss", "Drawdown", "Open risk", "Open trades")
HEALTH_NAMES = ("MT5 connection", "Local database", "Cloud sync", "Update")
STEP_NAMES = ("Trend", "Risk", "Spread", "News", "Order")
GO_LIVE_NOTE = (
    "Until the Go-Live checklist is approved only Paper, Semi-auto and a demo account are "
    "available."
)
NO_POSITIONS = "The bot has no open positions or pending orders."
NO_SIGNALS = "No signals yet. They appear here as each candle closes."
NO_CURVE = "Connect MT5 to see the curve."
NO_TRADES = "No closed trades in this period yet."
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
    "Updates every 2 seconds": "به‌روزرسانی هر ۲ ثانیه",
    "Balance": "موجودی",
    "Equity": "اکوییتی",
    "Today": "امروز",
    "Open risk": "ریسک باز",
    "Last 30 days": "۳۰ روز اخیر",
    "Not known yet": "هنوز معلوم نیست",
    "with open profit": "با سود باز",
    "Equity curve": "منحنی سرمایه",
    "closed trades": "معاملات بسته‌شده",
    "Open positions": "موقعیت‌های باز",
    "All": "همه",
    "Signal decision trace": "ردیابی تصمیم سیگنال‌ها",
    "All signals": "همه‌ی سیگنال‌ها",
    "Traded": "معامله‌شده",
    "Waiting for approval": "منتظر تأیید",
    "Not traded": "معامله‌نشده",
    "Market sessions": "جلسات بازار",
    "Risk limits": "محدودیت‌های ریسک",
    "Adjust": "تنظیم",
    "Daily loss": "زیان روزانه",
    "Drawdown": "افت سرمایه",
    "Open trades": "معاملات باز",
    "near the limit": "نزدیک سقف",
    "limit reached": "به سقف رسید",
    "Market direction": "جهت بازار",
    "Market direction is not a signal.": "جهت بازار سیگنال نیست.",
    "Waiting for the market analysis.": "منتظر تحلیل بازار.",
    "System health": "سلامت سیستم",
    "Details": "جزئیات",
    "MT5 connection": "اتصال MT5",
    "Local database": "پایگاه داده‌ی محلی",
    "Cloud sync": "همگام‌سازی ابری",
    "Update": "به‌روزرسانی",
    "Go to Risk": "رفتن به ریسک",
    "Go-Live checklist": "چک‌لیست Go-Live",
    GO_LIVE_NOTE: "تا تأیید چک‌لیست Go-Live فقط Paper، Semi-auto و حساب دمو در دسترس است.",
    "New entries are stopped.": "ورود تازه متوقف است.",
    NO_POSITIONS: "ربات موقعیت باز یا سفارش در انتظاری ندارد.",
    NO_SIGNALS: "هنوز سیگنالی نیست. با بسته شدن هر کندل، سیگنال‌ها این‌جا می‌آیند.",
    NO_CURVE: "برای دیدن منحنی، MT5 را وصل کنید.",
    NO_TRADES: "در این بازه هنوز معامله‌ی بسته‌شده‌ای نیست.",
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
    "Up to date": "به‌روز",
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
    """(name, used, limit, unit) for the limit bars."""
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


def limit_tone(share: float) -> str:
    """The bar color of a risk limit: calm, then amber from 75% used and red at the limit."""
    if share >= 100.0:
        return "loss"
    if share >= 75.0:
        return "warning"
    return "accent"


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
        return "\u2014"
    low, high = round(estimate.low * 100), round(estimate.high * 100)
    return f"{estimate.value * 100:.0f}% ({low}\u2013{high})"


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
        self.go: Callable[[str], None] = lambda page_id: None
        self._trades_at = -math.inf
        self._go_live_at = -math.inf
        self._signals_snapshot: SignalsSnapshot | None = None
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
        layout.setContentsMargins(30, 26, 30, 40)
        layout.setSpacing(16)
        self.header = PageHeader("Dashboard", page_by_id("dashboard").summary, "TRADE")
        self.header.add_action(v2_label(self.t("Updates every 2 seconds"), "muted"))
        layout.addWidget(self.header)
        self.halt_banner = Banner(self.t("New entries are stopped."), self.t("Go to Risk"), "loss")
        self.halt_banner.setObjectName("DashboardHalted")
        self.halt_banner.clicked.connect(lambda: self.go("risk"))
        self.halt_banner.setVisible(False)
        layout.addWidget(self.halt_banner)
        self.go_live_banner = Banner(self.t(GO_LIVE_NOTE), self.t("Go-Live checklist"))
        self.go_live_banner.setObjectName("DashboardGoLive")
        self.go_live_banner.clicked.connect(lambda: self.go("strategies"))
        layout.addWidget(self.go_live_banner)
        self.go_live_label = self.go_live_banner.text
        self.strip = KpiStrip([self.t(name) for name in KPI_NAMES])
        self.kpis: dict[str, KpiCell] = dict(zip(KPI_NAMES, self.strip.cells, strict=True))
        for name, cell in self.kpis.items():
            cell.setObjectName(f"Kpi{name.replace(' ', '')}")
            cell.sub.setText(self.t(UNKNOWN))
        layout.addWidget(self.strip)
        layout.addSpacing(10)
        grid = QHBoxLayout()
        grid.setSpacing(28)
        main = QVBoxLayout()
        main.setSpacing(26)
        side = QVBoxLayout()
        side.setSpacing(26)
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
        self._build_direction(side)
        self._build_health(side)
        side.addStretch(1)
        self.status = v2_label("", "note", wrap=True)
        self.status.setObjectName("DashboardStatus")
        layout.addWidget(self.status)
        self.bias = self.direction_note
        layout.addStretch(1)
        if persian:
            body.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
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
        value = value if "." in value else persian_digits(value)
        return f"{value} {COUNTED_FA[word]}"

    def count(self, value: int) -> str:
        return persian_digits(str(value)) if self.persian else str(value)

    # Building --------------------------------------------------------------------------
    def _build_equity(self, column: QVBoxLayout) -> None:
        caption = v2_label(self.t("closed trades"), "muted")
        section = Section(self.t("Equity curve"), caption)
        self.range_segment = Segmented(list(RANGES))
        self.range_segment.setObjectName("Segmented")
        for button in self.range_segment.buttons:
            button.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.range_segment.choose(list(RANGES).index(self.range))
        self.range_segment.group.idClicked.connect(self._range_clicked)
        section.add(self.range_segment)
        column.addWidget(section)
        self.equity_chart = EquityChart()
        self.equity_chart.setObjectName("DashboardEquity")
        self.equity_chart.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.equity_empty = empty_box(self.t(NO_CURVE))
        column.addWidget(self.equity_chart)
        column.addWidget(self.equity_empty)

    def _build_positions(self, column: QVBoxLayout) -> None:
        self.positions_title = v2_label("", "cap")
        section = Section(self.t("Open positions"), Tag("SL ON SERVER"))
        section.actions.addWidget(self.positions_title)
        every = v2_button(self.t("All"))
        every.clicked.connect(lambda: self.go("positions"))
        section.add(every)
        column.addWidget(section)
        columns = [
            Column("SYMBOL"),
            Column("STRATEGY"),
            Column("SIDE"),
            Column("LOT", end=True),
            Column("ENTRY", end=True),
            Column("SL", end=True),
            Column("TP", end=True),
            Column("P/L", end=True, stretch=True),
            Column("MODE", end=True),
        ]
        self.positions = DesignTable(columns, self.t(NO_POSITIONS))
        self.positions.setObjectName("DashboardPositions")
        self.positions_list = self.positions
        self.positions.row_clicked.connect(lambda _key: self.go("positions"))
        column.addWidget(self.positions)

    def _build_signals(self, column: QVBoxLayout) -> None:
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
        column.addWidget(section)
        columns = [
            Column("TIME"),
            Column("SYMBOL"),
            Column("SIDE"),
            Column("PROB"),
            Column("PIPELINE", stretch=True),
            Column("RESULT", end=True),
        ]
        self.signals = DesignTable(columns, self.t(NO_SIGNALS))
        self.signals.setObjectName("DashboardSignals")
        self.signals_list = self.signals
        self.signals.row_clicked.connect(lambda _key: self.go("signals"))
        column.addWidget(self.signals)

    def _build_sessions(self, column: QVBoxLayout) -> None:
        column.addWidget(Section(self.t("Market sessions"), v2_label("UTC", "cap")))
        self.sessions = SessionsBar()
        self.sessions.setObjectName("DashboardSessions")
        column.addWidget(self.sessions)

    def _build_limits(self, column: QVBoxLayout) -> None:
        section = Section(self.t("Risk limits"))
        adjust = v2_button(self.t("Adjust"))
        adjust.clicked.connect(lambda: self.go("risk"))
        section.add(adjust)
        column.addWidget(section)
        self.bars: dict[str, Meter] = {}
        self.bar_labels: dict[str, QLabel] = {}
        self.bar_values: dict[str, QLabel] = {}
        self.bar_tags: dict[str, Tag] = {}
        for name in LIMIT_NAMES:
            box = QVBoxLayout()
            box.setSpacing(6)
            top = QHBoxLayout()
            top.setSpacing(8)
            label = v2_label(f"{self.t(name)}", "row")
            label.setAccessibleName(f"{name}: n/a")
            tag = Tag("", "warning", mono=False)
            tag.setVisible(False)
            value = v2_label("\u2014", "row_num")
            value.setProperty("tone", "muted")
            value.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            top.addWidget(label)
            top.addWidget(tag)
            top.addStretch(1)
            top.addWidget(value)
            bar = Meter()
            bar.setObjectName(f"Limit{name.replace(' ', '')}")
            box.addLayout(top)
            box.addWidget(bar)
            column.addLayout(box)
            self.bars[name] = bar
            self.bar_labels[name] = label
            self.bar_values[name] = value
            self.bar_tags[name] = tag

    def _build_direction(self, column: QVBoxLayout) -> None:
        column.addWidget(Section(self.t("Market direction")))
        self.direction_rows = QVBoxLayout()
        self.direction_rows.setSpacing(0)
        column.addLayout(self.direction_rows)
        self.direction_note = v2_label(self.t("Waiting for the market analysis."), "note", True)
        self.direction_note.setObjectName("DashboardBias")
        column.addWidget(self.direction_note)
        self._direction_shown: list[tuple[str, str, str]] = []

    def _build_health(self, column: QVBoxLayout) -> None:
        section = Section(self.t("System health"))
        details = v2_button(self.t("Details"))
        details.clicked.connect(lambda: self.go("health"))
        section.add(details)
        column.addWidget(section)
        self.health: dict[str, Tag] = {}
        for name in HEALTH_NAMES:
            tag = Tag(self.t("Unknown"), "neutral", mono=False)
            row = InfoRow(self.t(name), tag)
            row.setObjectName(f"Health{name.replace(' ', '')}")
            column.addWidget(row)
            self.health[name] = tag

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
        tones = {item.tone for item in self.health.values()}
        self.health_tone = "loss" if "loss" in tones else "warning" if "warning" in tones else ""

    def set_account_kind(self, kind: str) -> None:
        """The account kind under the balance: "Demo account" or "Real account"."""
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
        if context.go_live is not None and abs(moment - self._go_live_at) >= TRADES_SECONDS:
            self._go_live_at = moment
            self._show_go_live(context.go_live)
        risk = context.risk() if context.risk is not None else None
        execution = context.execution() if context.execution is not None else None
        self._show_account(risk, execution, moment)
        if execution is not None:
            self._show_positions(execution)
        if context.signals is not None:
            self._show_signals(context.signals())
        if context.market is not None:
            self._show_direction(context.market())
        self.sessions.set_time(moment)

    def _show_go_live(self, readiness: Callable[[], str]) -> None:
        try:
            text = readiness()
        except Exception as error:
            text = f"Go-Live readiness could not be read: {type(error).__name__}"
        self.go_live_banner.setToolTip(text)
        shown = self.t(GO_LIVE_NOTE) if self.persian else f"{GO_LIVE_NOTE} {text}"
        self.go_live_banner.set_text(shown)

    def _show_account(
        self,
        risk: RiskSnapshot | None,
        execution: ExecutionSnapshot | None,
        now: float,
    ) -> None:
        usage = risk.usage if risk is not None else None
        rows = limit_rows(risk) if risk is not None else []
        if usage is not None:
            self._connected_data = True
            self._currency = usage.currency or self._currency
            currency = usage.currency
            balance = self.kpis["Balance"]
            balance.set_text(f"{number(usage.balance)}")
            kind = self.t(self.account_kind) if self.account_kind else ""
            balance.sub.setText(" \u00b7 ".join(part for part in (kind, currency) if part))
            equity = self.kpis["Equity"]
            equity.set_text(f"{number(usage.equity)}")
            equity.sub.setText(self.t("with open profit"))
            self._equity_seen = [*self._equity_seen, usage.equity][-EQUITY_SAMPLES:]
            equity.set_spark(self._equity_seen)
            cap = next((limit for name, _, limit, _ in rows if name == "Open risk"), math.nan)
            open_risk = self.kpis["Open risk"]
            open_risk.set_text(f"{usage.open_risk_percent:.1f}%")
            of_cap = f"of the {cap:.1f}% cap" if math.isfinite(cap) else ""
            if self.persian and math.isfinite(cap):
                of_cap = f"از سقف {cap:.1f}%"
            open_risk.sub.setText(of_cap)
        net, closed = today_result(self.trades, now)
        today = self.kpis["Today"]
        if usage is not None or self.trades:
            today.set_text(money(net, self._currency), net)
            opened = 0
            if execution is not None:
                opened = sum(1 for view in execution.positions if not view.pending)
            sub = f"{closed} closed \u00b7 {opened} open"
            if self.persian:
                sub = f"{self.count(closed)} بسته \u00b7 {self.count(opened)} معامله‌ی باز"
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

    def _show_limits(self, rows: Sequence[tuple[str, float, float, str]]) -> None:
        for name, used, limit, unit in rows:
            share = used / limit if limit > 0 else 0.0
            self.bars[name].set_share(share)
            tone = meter_tone(share)
            used_text = f"{used:.1f}{unit}" if unit else f"{used:.0f}"
            limit_text = f"{limit:.1f}{unit}" if unit else f"{limit:.0f}"
            self.bar_values[name].setText(f"{used_text} / {limit_text}")
            words = {"warning": "near the limit", "loss": "limit reached"}.get(tone, "")
            tag = self.bar_tags[name]
            tag.set(self.t(words), "loss" if tone == "loss" else "warning")
            tag.setVisible(bool(words))
            english = LIMIT_WORDS.get(limit_tone(share * 100.0), "")
            spoken = f"{name}: {used:.2f}{unit} of {limit:.2f}{unit}{english}"
            if not unit:
                spoken = f"{name}: {used:.0f} of {limit:.0f}{english}"
            self.bar_labels[name].setAccessibleName(spoken)
            self.bar_labels[name].setToolTip(spoken)

    def _show_trades(self, now: float) -> None:
        recent = last_days(self.trades, now)
        stats = compute_stats(recent, minimum_trades=0)
        month = self.kpis["Last 30 days"]
        if self.trades or self._connected_data:
            month.set_text(money(stats.net_profit, self._currency), stats.net_profit)
            rate = f"{stats.win_rate * 100:.0f}%"
            sub = f"{stats.trades} trades \u00b7 win rate {rate}"
            if self.persian:
                sub = f"{self.count(stats.trades)} معامله \u00b7 نرخ برد {rate}"
            month.sub.setText(sub)
            curve = equity_curve(recent, 0.0)
            tone = "profit" if stats.net_profit > 0 else "loss" if stats.net_profit < 0 else ""
            month.set_spark([float(v) for v in curve.equity], tone or "neutral")
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
                title = f"{self.count(opened)} باز، {self.count(len(views) - opened)} در انتظار"
        self.positions_title.setText(title)

    def _position(self, view: PositionView) -> Row:
        buy = view.direction in ("buy", "long")
        side = f"\u2191 {self.t('Buy')}" if buy else f"\u2193 {self.t('Sell')}"
        if view.pending or view.profit is None:
            result = Cell(self.t("Pending order"), tag="warning")
        else:
            tone = "profit" if view.profit > 0 else "loss" if view.profit < 0 else ""
            result = Cell(money(view.profit, self._currency), mono=True, bold=True, tone=tone)
        cells = (
            Cell(view.symbol, mono=True, bold=True),
            Cell(view.strategy or "-", mono=True, tone="muted"),
            Cell(side),
            Cell(f"{view.volume:.2f}", mono=True),
            Cell(price_text(view.entry), mono=True),
            Cell(price_text(view.sl), mono=True),
            Cell(price_text(view.tp), mono=True),
            result,
            Cell(view.mode.upper(), mono=True, tag="neutral"),
        )
        tip = " \u00b7 ".join(position_row(view))
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
            title = f"{self.count(len(records))}/{self.count(len(snapshot.signals))}"
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
            Cell(_clock(signal.created_at), mono=True),
            Cell(signal.symbol, mono=True, bold=True),
            Cell(side, mono=True, tag="neutral"),
            Cell(probability_text(record), mono=True),
            Cell(key=key, widget=lambda: Pipeline(steps, tips)),
            Cell(self.t(word), tag=tone),
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
            row = InfoRow(symbol, value)
            row.name.setProperty("v2", "row_num")
            self.direction_rows.addWidget(row)
        note = "Market direction is not a signal." if found else "Waiting for the market analysis."
        self.direction_note.setText(self.t(note))
        pairs = bias_text(snapshot)
        if pairs:
            self.direction_note.setToolTip("   ".join(f"{s} {t}" for s, t in pairs))

    def _range_clicked(self, index: int) -> None:
        self.range = list(RANGES)[index]
        self._show_curve(time.time())

    def _filter_changed(self, _index: int) -> None:
        self.signals.show_page(0)
        if self._signals_snapshot is not None:
            self._show_signals(self._signals_snapshot)
