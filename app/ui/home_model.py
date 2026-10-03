"""What the Simple view's Home screen says (spec B3b, F0), in plain words. Pure: no Qt.

Every text on the default Home screen is built here from the same snapshots the Advanced
pages use (signals, execution, risk), so nothing is computed a second way, and one test can
check that the default screen carries no trading jargon. The exact numbers stay one level
deeper, under "Details".
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.modes import OperatingMode
from app.domain.probability import ProbabilityEstimate
from app.domain.signals import Direction, OrderType, SignalRecord
from app.engine.execution import ExecutionSnapshot, PositionView
from app.risk.limits import RiskUsage
from app.risk.limits_state import DAILY_LIMIT, DD_LIMIT, MANUAL_STOP

UP = "\u25b2"
DOWN = "\u25bc"
FLAT = "\u25cf"
MINUS = "\u2212"
WEEK_DAYS = 7
SYMBOL_NAMES = {
    "EURUSD": "Euro vs US dollar",
    "GBPUSD": "British pound vs US dollar",
    "USDJPY": "US dollar vs Japanese yen",
    "USDCHF": "US dollar vs Swiss franc",
    "USDCAD": "US dollar vs Canadian dollar",
    "AUDUSD": "Australian dollar vs US dollar",
    "NZDUSD": "New Zealand dollar vs US dollar",
    "EURGBP": "Euro vs British pound",
    "EURJPY": "Euro vs Japanese yen",
    "GBPJPY": "British pound vs Japanese yen",
    "EURCHF": "Euro vs Swiss franc",
    "AUDJPY": "Australian dollar vs Japanese yen",
    "XAUUSD": "Gold",
    "XAGUSD": "Silver",
    "BTCUSD": "Bitcoin",
    "ETHUSD": "Ether",
}
SINGLE_ASSETS = frozenset({"XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD"})
CURRENCY_SIGNS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5"}
TIMEFRAME_TEXT = {
    "M1": "1 minute",
    "M5": "5 minutes",
    "M15": "15 minutes",
    "M30": "30 minutes",
    "H1": "1 hour",
    "H4": "4 hours",
    "D1": "1 day",
}
MODE_LINES = {
    OperatingMode.PAPER: "Practice money: trades are simulated, nothing real is bought or sold.",
    OperatingMode.SEMI_AUTO: "Real orders: a trade is placed only after you approve it.",
    OperatingMode.ANALYSIS_ONLY: "Watching only: the app shows suggestions but places nothing.",
    OperatingMode.AUTO: "Automatic trading is not available yet.",
}
PRACTICE_TITLE = "This is practice money, not real"
PRACTICE_TEXT = (
    "The app starts in practice mode. It watches the market for you and suggests trades. "
    "When you approve one, it is only simulated on live prices: no real money is used, and "
    "nothing reaches your account until you choose otherwise."
)
KNOWLEDGE_QUESTION = "How much do you know about trading?"
# Words a non-technical user should not meet on the default Home screen (spec G3 row 9).
JARGON = re.compile(
    r"\b(R|ATR|EV|SL|TP|R:R|EMA\d*|RSI|ADX|lots?|magic|ticket|long|short|pips?|retcode|"
    r"spread|drawdown|stop loss|take profit|timeframe|M5|M15|H1|H4|D1)\b",
)


@dataclass(frozen=True)
class SuggestionView:
    signal_id: str
    title: str  # "Gold (XAUUSD)"
    action: str  # "Buy" or "Sell"
    arrow: str
    reason: str
    make: str
    lose: str
    confidence: str
    confidence_note: str
    ends: str
    moved: str  # "" unless the price moved too far since the suggestion was found
    queue: str  # "1 of 3 suggestions", "" for one
    details: tuple[tuple[str, str], ...]

    def default_texts(self) -> list[str]:
        """Everything the card shows before "Details" is opened."""
        texts = [self.title, f"{self.arrow} {self.action}", self.reason, self.make, self.lose]
        texts += [self.confidence, self.confidence_note, self.ends, self.moved, self.queue]
        return [text for text in texts if text]


@dataclass(frozen=True)
class BalanceView:
    title: str
    balance: str
    today: str
    today_kind: str  # profit, loss or muted
    week: str
    week_kind: str
    points: tuple[float, ...]  # the running result of the last 7 days, for the sparkline


@dataclass(frozen=True)
class TradeRow:
    mode: str
    ticket: int
    title: str
    action: str
    result: str
    kind: str  # profit, loss or muted
    pending: bool

    def texts(self) -> list[str]:
        return [self.title, self.action, self.result]


def base_symbol(symbol: str) -> str:
    """"EURUSD.m" or "eurusd_i" -> "EURUSD"; other names unchanged (upper case)."""
    letters = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    if letters in SYMBOL_NAMES:
        return letters
    return letters[:6] if letters[:6] in SYMBOL_NAMES else letters


def plain_name(symbol: str) -> str:
    return SYMBOL_NAMES.get(base_symbol(symbol), symbol)


def plain_title(symbol: str) -> str:
    name = plain_name(symbol)
    return name if name == symbol else f"{name} ({symbol})"


def subject(symbol: str) -> str:
    base = base_symbol(symbol)
    name = SYMBOL_NAMES.get(base)
    if name is None:
        return symbol
    return name if base in SINGLE_ASSETS else f"The {name} rate"


def action_text(direction: Direction | str) -> tuple[str, str]:
    buying = direction in (Direction.LONG, "long")
    return ("Buy", UP) if buying else ("Sell", DOWN)


def money(value: float, currency: str, *, signed: bool = False) -> str:
    """"+$12.30", "−$5.00" (signed) or "$1,000.00"; "12.30 CHF" without a known sign."""
    amount = f"{abs(value):,.2f}"
    sign_symbol = CURRENCY_SIGNS.get(currency)
    text = f"{sign_symbol}{amount}" if sign_symbol else f"{amount} {currency}".strip()
    if value < 0:
        return f"{MINUS}{text}"
    return f"+{text}" if signed and value > 0 else text


def kind_of(value: float) -> str:
    if value > 0:
        return "profit"
    return "loss" if value < 0 else "muted"


def arrow_of(value: float) -> str:
    if value > 0:
        return UP
    return DOWN if value < 0 else FLAT


def plain_reason(record: SignalRecord) -> str:
    """One sentence a beginner understands (the strategy's rules are under Details)."""
    signal = record.signal
    who = subject(signal.symbol)
    up = signal.direction is Direction.LONG
    if signal.strategy == "trend_pullback":
        if up:
            return f"{who} is trending up and just dipped back, so it may rise again."
        return f"{who} is trending down and just bounced back, so it may fall again."
    if signal.strategy == "london_breakout":
        way = "above" if up else "below"
        return (
            f"{who} stayed in a narrow range overnight. If it breaks {way} that range after "
            "the London market opens, the move may carry on."
        )
    verb = "rise" if up else "fall"
    return f"The app found a chance that {who} may {verb}."


def confidence(estimate: ProbabilityEstimate) -> tuple[str, str]:
    """A label for the win chance (spec F0); the exact number is under Details."""
    if estimate.value is None or estimate.low is None:
        needed = estimate.min_samples
        return (
            "Confidence: not known yet",
            f"The app needs {needed} finished suggestions of this kind before it can say.",
        )
    if estimate.low >= 0.55:
        label = "Fairly confident"
    elif estimate.value >= 0.5:
        label = "Moderately confident"
    else:
        label = "Low confidence"
    return label, f"Based on {estimate.samples} earlier suggestions of this kind."


def duration_words(seconds: float) -> str:
    minutes = max(0, int(math.ceil(seconds / 60.0)))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d} min"


def price_moved(
    record: SignalRecord,
    bid: float | None,
    ask: float | None,
    tolerance_r: float,
) -> bool:
    """True when the live price is further from the entry than the approval re-check allows."""
    signal = record.signal
    if signal.order_type is not OrderType.MARKET or bid is None or ask is None:
        return False
    if signal.risk <= 0 or bid <= 0 or ask <= 0:
        return False
    price = ask if signal.direction is Direction.LONG else bid
    return abs(price - signal.entry) / signal.risk > tolerance_r


def _chance_text(estimate: ProbabilityEstimate) -> str:
    if estimate.value is None or estimate.low is None or estimate.high is None:
        return f"not known yet: {estimate.samples} of {estimate.min_samples} past results"
    return (
        f"{estimate.value * 100:.0f}% (likely between {estimate.low * 100:.0f}% and "
        f"{estimate.high * 100:.0f}%), from {estimate.samples} past results"
    )


def _order_text(record: SignalRecord) -> str:
    signal = record.signal
    action, _ = action_text(signal.direction)
    price = signal.price(signal.entry)
    if signal.order_type is OrderType.MARKET:
        return f"{action} now at about {price}"
    if signal.order_type is OrderType.STOP:
        return f"{action} when the price reaches {price}"
    return f"{action} if the price comes back to {price}"


def suggestion_view(
    record: SignalRecord,
    currency: str,
    now: float,
    *,
    position: int = 1,
    total: int = 1,
    moved: bool = False,
    strategy_title: str = "",
) -> SuggestionView:
    signal = record.signal
    action, arrow = action_text(signal.direction)
    risk = record.risk_money
    if risk is None or not math.isfinite(risk):
        lose = "You could lose: not known until the account is read"
        make = "You could make: not known until the account is read"
    else:
        lose = f"You could lose about {money(-abs(risk), currency)} if it goes wrong"
        profit = money(abs(risk) * signal.rr, currency, signed=True)
        make = f"You could make about {profit} if it goes right"
    label, note = confidence(record.probability)
    left = signal.expires_at - now
    ends = "This suggestion has ended"
    if left > 0:
        ends = f"This suggestion ends in {duration_words(left)}"
    moved_text = (
        "The price has moved since this was found. It is checked again when you approve and "
        "may be cancelled."
        if moved
        else ""
    )
    lot = "-" if record.volume is None else f"{record.volume:g} lots"
    ev = record.expected_value
    expected = (
        "not known yet"
        if ev is None
        else f"{ev:+.2f} times the amount at risk, on average (EV {ev:+.2f} R)"
    )
    details = (
        ("Chance it works", _chance_text(record.probability)),
        ("Order", _order_text(record)),
        ("Exit if it goes wrong (stop loss)", signal.price(signal.sl)),
        ("Exit if it goes right (take profit)", signal.price(signal.tp)),
        ("Size", f"{lot}, sized from your account and risk settings"),
        ("Expected result", expected),
        ("Chart", TIMEFRAME_TEXT.get(signal.timeframe, signal.timeframe)),
        ("Strategy", f"{strategy_title or signal.strategy} (an example, not proven to pay)"),
        ("Why, in detail", signal.reason),
    )
    return SuggestionView(
        signal_id=signal.id,
        title=plain_title(signal.symbol),
        action=action,
        arrow=arrow,
        reason=plain_reason(record),
        make=make,
        lose=lose,
        confidence=label,
        confidence_note=note,
        ends=ends,
        moved=moved_text,
        queue=f"{position} of {total} suggestions" if total > 1 else "",
        details=details,
    )


def ordered_suggestions(
    records: Iterable[SignalRecord],
    skip: Iterable[str] = (),
) -> list[SignalRecord]:
    """Waiting suggestions, the one that ends first on top (spec F0: one at a time)."""
    hidden = set(skip)
    waiting = [record for record in records if record.id not in hidden]
    return sorted(waiting, key=lambda record: (record.signal.expires_at, record.id))


def approve_question(view: SuggestionView, mode: OperatingMode) -> str:
    if mode is OperatingMode.SEMI_AUTO:
        where = "This places a REAL order on your MetaTrader 5 account."
    else:
        where = "This is practice money: the trade is only simulated."
    return (
        f"{view.action} {view.title}?\n\n{view.lose}.\n{view.make}.\n\n{where}\n"
        "The price is checked again before anything is placed."
    )


def approve_block_text(block: str, mode: OperatingMode) -> str:
    if not block:
        return ""
    if mode is OperatingMode.ANALYSIS_ONLY:
        return "Watching only: choose practice or real orders in the Advanced view to approve."
    return f"Approve is not available right now: {block}."


def _day(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).date().isoformat()


def week_points(daily: Sequence[tuple[str, float]], now: float) -> tuple[float, ...]:
    """The running result over the last 7 days (today last), 0 on days without a close."""
    by_day: Mapping[str, float] = dict(daily)
    today = datetime.fromtimestamp(now, UTC).date()
    offsets = range(WEEK_DAYS - 1, -1, -1)
    days = [(today - timedelta(days=offset)).isoformat() for offset in offsets]
    total = 0.0
    points = []
    for day in days:
        total += float(by_day.get(day, 0.0))
        points.append(round(total, 2))
    return tuple(points)


def balance_view(
    usage: RiskUsage | None,
    mode: OperatingMode,
    daily: Sequence[tuple[str, float]],
    now: float,
) -> BalanceView:
    title = "Practice balance" if mode is OperatingMode.PAPER else "Balance"
    points = week_points(daily, now)
    if usage is None or not math.isfinite(usage.balance):
        unknown = "Today: not known yet"
        return BalanceView(title, "not known yet", unknown, "muted", "", "muted", points)
    currency = usage.currency
    balance = money(usage.balance, currency)
    today_kind = "muted"
    if usage.day_start_equity > 0 and math.isfinite(usage.equity):
        change = usage.equity - usage.day_start_equity
        percent = change / usage.day_start_equity * 100.0
        sign = "+" if percent > 0 else MINUS if percent < 0 else ""
        today = (
            f"Today: {arrow_of(change)} {money(change, currency, signed=True)} "
            f"({sign}{abs(percent):.2f}%)"
        )
        today_kind = kind_of(change)
    else:
        today = "Today: not known yet"
    week_total = points[-1] if points else 0.0
    week = f"Last 7 days: {arrow_of(week_total)} {money(week_total, currency, signed=True)}"
    return BalanceView(title, balance, today, today_kind, week, kind_of(week_total), points)


def trade_rows(
    snapshot: ExecutionSnapshot,
    capital: float,
    currency: str,
) -> list[TradeRow]:
    return [_trade_row(view, capital, currency) for view in snapshot.positions]


def _trade_row(view: PositionView, capital: float, currency: str) -> TradeRow:
    action, arrow = action_text(view.direction)
    if view.pending:
        return TradeRow(
            view.mode,
            view.ticket,
            plain_title(view.symbol),
            f"{arrow} {action}",
            "Waiting for its price",
            "muted",
            True,
        )
    if view.profit is None:
        result, kind = "Result not known yet", "muted"
    else:
        result = f"{arrow_of(view.profit)} {money(view.profit, currency, signed=True)}"
        if capital > 0 and math.isfinite(capital):
            percent = view.profit / capital * 100.0
            sign = "+" if percent > 0 else MINUS if percent < 0 else ""
            result += f" ({sign}{abs(percent):.2f}%)"
        kind = kind_of(view.profit)
    return TradeRow(
        view.mode,
        view.ticket,
        plain_title(view.symbol),
        f"{arrow} {action}",
        result,
        kind,
        False,
    )


def close_question(row: TradeRow) -> str:
    return (
        f"Close {row.title} now at the current price?\n\nIts result now: {row.result}. "
        "The final result can differ a little."
    )


def home_status(
    *,
    connected: bool,
    stopped: str,
    halted: str,
    suggestions: int,
    open_trades: int,
    market_open: bool,
) -> str:
    """The bottom status line (spec F0), from the same state as the Advanced view."""
    if not connected:
        return "Not connected to MetaTrader 5, so the app is not watching the market."
    if stopped:
        return "Stopped: you pressed Stop trading. Nothing new is opened until you allow it."
    if halted == DAILY_LIMIT:
        return "Paused: today's loss limit is reached. New trades are allowed again tomorrow."
    if halted == DD_LIMIT:
        return (
            "Paused: the account fell too far below its best level. Allow trading again in the "
            "Advanced view (Risk)."
        )
    if halted == MANUAL_STOP:
        return "Paused: new trades are stopped. Allow them again in the Advanced view (Risk)."
    if suggestions:
        return "Found a trade for you." if suggestions == 1 else f"Found {suggestions} trades."
    if open_trades:
        return "Trade running." if open_trades == 1 else f"{open_trades} trades running."
    if not market_open:
        return "The market is closed. The app starts watching again when it opens."
    return "Watching the market\u2026"


def jargon_in(texts: Iterable[str]) -> list[str]:
    """The jargon words found in `texts` (an empty list means plain language)."""
    found: list[str] = []
    for text in texts:
        found += [match.group(0) for match in JARGON.finditer(text)]
    return found
