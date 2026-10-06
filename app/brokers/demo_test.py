"""Health > Demo test: everything the bot does with orders, done for real on a DEMO account
(asked for on 5 October 2026, after the London open showed a problem that only real orders
could show).

Every step uses the bot's own code: `LiveBroker` on the app's MT5 gateway thread, so each
order gets the same checks, retries, return-code texts and request log as the execution
engine's orders. Every MT5 read is one short gateway call and the waits for a fill or an
expiry happen outside the gateway, so the market analysis keeps running during the test.

The test trades the minimum lot under its own magic number (`DEMO_MAGIC`), which no
strategy uses, so the engine never manages its trades. It refuses any account that is not
DEMO, and at the end it closes every position and cancels every order of that magic number,
also after a failure or a Stop. A step that cannot run (the market is closed, the price did
not move) is skipped with the reason.

Every expiry is a whole minute: MT5 drops the seconds of a pending order's expiry (0.23.4).
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from app.brokers.base import BrokerOrder, BrokerPosition, OrderResult
from app.brokers.live_broker import EXPIRATION_FLAGS, FILLING_FLAGS, ORDER_FLAGS, LiveBroker
from app.brokers.market import Quote, read_quote
from app.brokers.requests import OrderPlan, whole_minute
from app.domain.history import summarize_position
from app.domain.orders import round_price
from app.domain.signals import Direction, OrderType
from app.mt5 import api
from app.mt5.api import MT5Api
from app.mt5.errors import MT5Error
from app.mt5.gateway import MT5Gateway
from app.mt5.models import (
    AccountKind,
    AccountSnapshot,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
)
from app.mt5.retcodes import ALGO_TRADING_FIX, PYTHON_API_FIX
from app.mt5.symbols import resolve_symbol

T = TypeVar("T")

DEMO_MAGIC = 26_070_098  # no strategy uses it, so the engine never manages these trades
COMMENT = "tw-demo-test"
BREAKOUT_COMMENT = "tw-demo-breakout"
DEVIATION_POINTS = 20
RETRIES = 2
READ_TIMEOUT_SECONDS = 15.0
FILL_WAIT_SECONDS = 90.0
EXPIRY_SECONDS = 120  # the expiring order's shortest life (MT5 refuses one under a minute)
EXPIRY_GRACE_SECONDS = 90.0  # how long MT5 may take to remove it after its expiry
PENDING_LIFE_SECONDS = 3600  # the other pending orders expire too, should the clean-up fail
BREAKOUT_LIFE_SECONDS = 600
BREAKOUT_GAP_POINTS = 5  # the breakout pair sits this far outside the stops level
DEAL_WAIT_SECONDS = 10.0
POLL_SECONDS = 1.0
EXPIRY_POLL_SECONDS = 5.0
MARGIN_ROOM = 6.0  # free margin needed, in margins of the minimum lot
MARKET_CLOSED = 10018
SYMBOL_EXPIRATION_SPECIFIED = 4
VOLUME_EPSILON = 1e-9
REPORT_PREFIX = "demo-test"
NO_POSITION = "no position to work on: the buy above did not open one"
STOPPED_TEXT = "stopped by you; the steps after it did not run and the test's trades are closed"


class DemoTestError(Exception):
    """A step could not go on (no price, no symbol data)."""


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"

    @property
    def mark(self) -> str:
        return MARKS[self]

    @property
    def label(self) -> str:
        return LABELS[self]


MARKS = {Outcome.PASS: "\u2713", Outcome.FAIL: "\u2717", Outcome.SKIP: "\u25cb"}
LABELS = {Outcome.PASS: "Pass", Outcome.FAIL: "Fail", Outcome.SKIP: "Skipped"}


@dataclass(frozen=True)
class Step:
    symbol: str  # "" for the account step
    name: str
    outcome: Outcome
    detail: str
    seconds: float = 0.0

    def line(self) -> str:
        where = f"{self.symbol}: " if self.symbol else ""
        mark = self.outcome.mark
        return f"[{mark}] {where}{self.name}: {self.detail} ({self.seconds:.1f} s)"


@dataclass(frozen=True)
class DemoReport:
    started: float  # UTC seconds
    finished: float
    account: str  # kind, broker and server; never the login or the holder's name
    steps: tuple[Step, ...]
    leftovers: tuple[str, ...] = ()
    stopped: bool = False

    def count(self, outcome: Outcome) -> int:
        return sum(1 for step in self.steps if step.outcome is outcome)

    @property
    def verdict(self) -> str:
        if self.count(Outcome.FAIL) or self.leftovers:
            return "FAIL"
        if self.count(Outcome.SKIP) or not self.count(Outcome.PASS):
            return "INCOMPLETE"
        return "PASS"

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"

    def summary(self) -> str:
        minutes = max(0.0, self.finished - self.started) / 60
        text = (
            f"Demo test {self.verdict}: {self.count(Outcome.PASS)} passed, "
            f"{self.count(Outcome.FAIL)} failed, {self.count(Outcome.SKIP)} skipped "
            f"in {minutes:.1f} min"
        )
        if self.stopped:
            text += " (stopped)"
        if self.leftovers:
            text += ". Close by hand in MT5: " + ", ".join(self.leftovers)
        return text

    def markdown(self) -> str:
        when = datetime.fromtimestamp(self.started, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "# Demo test",
            "",
            f"- Started: {when}",
            f"- Account: {self.account or 'not read'}",
            f"- Magic number of the test trades: {DEMO_MAGIC}",
            f"- {self.summary()}",
            "",
            "## Steps",
            "",
            *(f"- {step.line()}" for step in self.steps),
            "",
            "Send this file (or all.log) when a step failed.",
        ]
        return "\n".join(lines) + "\n"


def save_report(report: DemoReport, folder: Path) -> Path:
    """The report as Markdown in `folder`, named after its start time."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(report.started, UTC).strftime("%Y%m%d-%H%M%S")
    path = folder / f"{REPORT_PREFIX}-{stamp}.md"
    path.write_text(report.markdown(), encoding="utf-8")
    return path


def stop_room(spec: SymbolSpec, spread: float) -> float:
    """SL and TP distance of the test's orders: the SL moved halfway in stays outside the
    stops and freeze levels and the spread, and the stops sit well outside the spread."""
    point = spec.point if spec.point > 0 else 10.0**-spec.digits
    spread_points = spread / point
    level = max(spec.stops_level, spec.freeze_level)
    return max(2 * level + 4 * spread_points + 40, 20 * spread_points, 100.0) * point


def split_volume(spec: SymbolSpec) -> float:
    """Twice the minimum lot, so part of the position can be closed, unless that is too much."""
    double = round(2 * spec.volume_min, 8)
    return double if double <= spec.volume_max else spec.volume_min


def flags(value: int | None, names: Sequence[tuple[int, str]]) -> str:
    """The named bits of an MT5 flags field, "not reported" when MT5 leaves it out."""
    if value is None:
        return "not reported"
    return "+".join(name for bit, name in names if value & bit) or "none"


def server_clock(server_time: int) -> str:
    return datetime.fromtimestamp(server_time, UTC).strftime("%H:%M:%S")


def attempts_text(result: OrderResult) -> str:
    """Each MT5 call of an order with its answer and time, as the request log has them."""
    parts = [
        f"{attempt.action} {attempt.retcode_text} ({attempt.latency_ms:.0f} ms)"
        for attempt in result.attempts
    ]
    return "; ".join(parts) or "nothing was sent"


def side_name(plan: OrderPlan) -> str:
    side = "buy" if plan.direction is Direction.LONG else "sell"
    return side if plan.order_type is OrderType.MARKET else f"{side} {plan.order_type.value}"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@dataclass(frozen=True)
class Market:
    """One symbol as the test trades it: the broker's name, its rules and a price."""

    symbol: str
    spec: SymbolSpec
    quote: Quote
    expiration_mode: int | None = None
    order_mode: int | None = None

    @property
    def point(self) -> float:
        return self.spec.point if self.spec.point > 0 else 10.0**-self.spec.digits

    @property
    def room(self) -> float:
        return stop_room(self.spec, self.quote.ask - self.quote.bid)

    @property
    def expiry_allowed(self) -> bool:
        mode = self.expiration_mode
        return not mode or bool(mode & SYMBOL_EXPIRATION_SPECIFIED)

    def price(self, value: float) -> float:
        return round_price(value, self.spec.digits, self.spec.tick_size)

    def same(self, first: float, second: float) -> bool:
        tick = self.spec.tick_size if self.spec.tick_size > 0 else self.point
        return abs(first - second) <= tick / 2

    def text(self, value: float) -> str:
        return f"{value:.{self.spec.digits}f}"


def read_market(mt5: MT5Api, symbol: str) -> Market | None:
    """Select the symbol and read its rules and price (in the gateway thread)."""
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    quote = read_quote(mt5, symbol)
    if info is None or quote is None:
        return None
    expiry = _integer(getattr(info, "expiration_mode", None))
    orders = _integer(getattr(info, "order_mode", None))
    return Market(symbol, SymbolSpec.from_mt5(info), quote, expiry, orders)


def stops_problem(market: Market, position: BrokerPosition, plan: OrderPlan) -> str:
    """What differs between a position in MT5 and the order that opened it."""
    wrong: list[str] = []
    if not market.same(position.sl, plan.sl):
        wrong.append(f"the SL in MT5 is {market.text(position.sl)}, not {market.text(plan.sl)}")
    if not market.same(position.tp, plan.tp):
        wrong.append(f"the TP in MT5 is {market.text(position.tp)}, not {market.text(plan.tp)}")
    if position.magic != DEMO_MAGIC:
        wrong.append(f"the magic number in MT5 is {position.magic}, not {DEMO_MAGIC}")
    return "; ".join(wrong)


def order_problem(market: Market, order: BrokerOrder | None, plan: OrderPlan) -> str:
    """What differs between a pending order in MT5 and the plan that placed it (the expiry
    as it was sent: moved up to a whole minute)."""
    if order is None:
        return "not in the MT5 order list"
    pairs = (
        ("price", order.price, plan.price),
        ("SL", order.sl, plan.sl),
        ("TP", order.tp, plan.tp),
    )
    wrong = [
        f"{name} {market.text(found)}, not {market.text(wanted)}"
        for name, found, wanted in pairs
        if not market.same(found, wanted)
    ]
    if plan.expiration is not None:
        sent = whole_minute(plan.expiration)
        if order.expiration != sent:
            kept = server_clock(order.expiration) if order.expiration else "none"
            wrong.append(f"expiry {kept}, not {server_clock(sent)} server time")
    if order.magic != DEMO_MAGIC:
        wrong.append(f"magic number {order.magic}, not {DEMO_MAGIC}")
    return "; ".join(wrong)


def _symbol_names(mt5: MT5Api) -> list[str]:
    return [str(getattr(item, "name", "")) for item in (mt5.symbols_get() or ())]


def _terminal_and_account(mt5: MT5Api) -> tuple[Any, Any]:
    return mt5.terminal_info(), mt5.account_info()


@dataclass
class _Run:
    """What the steps of one symbol share."""

    wanted: str
    market: Market | None = None
    closed: bool = False  # the market is closed: the order steps are skipped
    position: BrokerPosition | None = None
    closed_ticket: int = 0
    pending: list[int] = field(default_factory=list)
    expiring: int = 0
    expires_at: int = 0


def _market(run: _Run) -> Market:
    if run.market is None:
        raise DemoTestError("the symbol's data was not read")
    return run.market


Work = Callable[[], tuple[Outcome, str]]


class DemoTest:
    """One run of the demo test (make a new one for every run). `stop` works from any thread."""

    def __init__(
        self,
        gateway: MT5Gateway,
        *,
        pause: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        fill_wait: float = FILL_WAIT_SECONDS,
        expiry_grace: float = EXPIRY_GRACE_SECONDS,
        log: Callable[[str, str], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._broker = LiveBroker(
            gateway,
            retries=lambda: RETRIES,
            deviation=lambda: DEVIATION_POINTS,
            pause=pause,
        )
        self._pause = pause
        self._clock = clock
        self._wall = wall
        self._fill_wait = fill_wait
        self._expiry_grace = expiry_grace
        self._log = log
        self._halt = threading.Event()
        self._steps: list[Step] = []
        self._listener: Callable[[Step], None] | None = None

    def stop(self) -> None:
        """Stop after the running step; the test's trades are still closed."""
        self._halt.set()

    @property
    def stopping(self) -> bool:
        return self._halt.is_set()

    def run(
        self,
        symbols: Sequence[str],
        on_step: Callable[[Step], None] | None = None,
    ) -> DemoReport:
        """Every step on each symbol in turn; `on_step` hears of each step when it ends."""
        self._listener = on_step
        self._steps = []
        started = self._wall()
        if self._log is not None:
            self._log("INFO", f"started on {', '.join(symbols)} (magic {DEMO_MAGIC})")
        account = self._account()
        leftovers: tuple[str, ...] = ()
        if account is not None:
            try:
                for wanted in symbols:
                    if self.stopping:
                        break
                    self._symbol(wanted)
            finally:
                leftovers = self._clean_up()
        if self.stopping:
            self._add("", "Stopped", Outcome.SKIP, STOPPED_TEXT, 0.0)
        steps = tuple(self._steps)
        report = DemoReport(started, self._wall(), account or "", steps, leftovers, self.stopping)
        if self._log is not None:
            self._log("INFO" if report.ok else "WARNING", report.summary())
        return report

    def _account(self) -> str | None:
        """The account check; None ends the test before any order."""
        found: list[str] = []
        outcome = self._step("", "MT5 and the demo account", partial(self._check_account, found))
        return found[0] if outcome is Outcome.PASS and found else None

    def _check_account(self, found: list[str]) -> tuple[Outcome, str]:
        terminal_raw, account_raw = self._read("account_info", _terminal_and_account)
        if terminal_raw is None or account_raw is None:
            return Outcome.FAIL, "MT5 is not connected: connect on the Connection page first"
        terminal = TerminalSnapshot.from_mt5(terminal_raw)
        account = AccountSnapshot.from_mt5(account_raw)
        if account.kind is not AccountKind.DEMO:
            text = (
                f"refused: this is a {account.kind.value} account. The demo test places real "
                "orders, so it runs on a DEMO account only. Nothing was sent."
            )
            return Outcome.FAIL, text
        if account.read_only:
            return Outcome.FAIL, "refused: an investor (read-only) login cannot trade"
        problems: list[str] = []
        if not terminal.connected:
            problems.append("MT5 has no connection to the broker's server.")
        if not terminal.trade_allowed:
            problems.append(ALGO_TRADING_FIX)
        if terminal.tradeapi_disabled:
            problems.append(PYTHON_API_FIX)
        if not account.trade_expert:
            problems.append("The broker does not allow automated trading on this account.")
        if problems:
            return Outcome.FAIL, "MT5 does not let the app trade. " + " ".join(problems)
        found.append(
            f"DEMO at {account.company}, server {account.server}, {account.currency}, "
            f"{account.margin_mode.value}",
        )
        text = (
            f"DEMO account at {account.company} ({account.server}), balance "
            f"{account.balance:,.2f} {account.currency}, {account.margin_mode.value}; Algo "
            "Trading on, trading from Python allowed"
        )
        return Outcome.PASS, text

    def _symbol(self, wanted: str) -> None:
        run = _Run(wanted)
        steps: tuple[tuple[str, Callable[[_Run], tuple[Outcome, str]]], ...] = (
            ("Symbol and price", self._read_market),
            ("Lot size and margin", self._sizing),
            ("Market buy with SL and TP", self._buy),
            ("Move the stop loss", self._move_stop),
            ("Close part of the position", self._close_part),
            ("Close the position", self._close),
            ("Closed trade in the history", self._history),
            ("Market sell, then close", self._sell),
            ("Pending order with an expiry", self._place_expiring),
            ("Pending orders: buy and sell, limit and stop", self._pending),
            ("Cancel pending orders", self._cancel),
            ("Breakout pair: one fills, the other is cancelled", self._breakout),
            ("Pending order expires by itself", self._expiry),
            ("Refused order is not sent again", self._refused),
            ("Close everything, like the kill switch", self._kill),
        )
        for name, work in steps:
            if self.stopping:
                return
            if run.closed:
                where = run.market.symbol if run.market is not None else wanted
                text = f"the market is closed for {where}: run the test while it trades"
                self._add(wanted, "Other order steps", Outcome.SKIP, text, 0.0)
                return
            self._step(wanted, name, partial(work, run))
            if run.market is None:
                return  # without the symbol's data no order can be built

    def _read_market(self, run: _Run) -> tuple[Outcome, str]:
        symbol = resolve_symbol(run.wanted, self._read("symbols_get", _symbol_names))
        if symbol is None:
            return Outcome.FAIL, f"{run.wanted} is not in the broker's symbol list"
        market = self._read("symbol_info", partial(read_market, symbol=symbol))
        if market is None:
            return Outcome.FAIL, f"MT5 has no symbol data or no live price for {symbol}"
        run.market = market
        spec, quote = market.spec, market.quote
        spread = (quote.ask - quote.bid) / market.point
        detail = (
            f"{symbol}: bid {market.text(quote.bid)}, ask {market.text(quote.ask)}, spread "
            f"{spread:.0f} points, stops level {spec.stops_level}, freeze level "
            f"{spec.freeze_level}, lots {spec.volume_min:g} to {spec.volume_max:g} by "
            f"{spec.volume_step:g}, filling {flags(spec.filling_mode, FILLING_FLAGS)}, orders "
            f"{flags(market.order_mode, ORDER_FLAGS)}, expiry "
            f"{flags(market.expiration_mode, EXPIRATION_FLAGS)}"
        )
        if spec.trade_mode is not SymbolTradeMode.FULL:
            run.closed = True
            return Outcome.SKIP, f"{detail}. Trading is {spec.trade_mode.value} now"
        return Outcome.PASS, detail

    def _sizing(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        volume = market.spec.volume_min
        ask = market.quote.ask
        stop = ask - market.room

        def work(mt5: MT5Api) -> tuple[float | None, float | None, Any]:
            loss = mt5.order_calc_profit(api.ORDER_TYPE_BUY, market.symbol, volume, ask, stop)
            margin = mt5.order_calc_margin(api.ORDER_TYPE_BUY, market.symbol, volume, ask)
            return _finite(loss), _finite(margin), mt5.account_info()

        loss, margin, account = self._read("order_calc_profit", work)
        if loss is None or margin is None:
            text = (
                f"MT5 cannot work out the loss or the margin of {market.symbol}: the bot sizes "
                "every order with them and would refuse every signal"
            )
            return Outcome.FAIL, text
        currency = str(getattr(account, "currency", "") or "")
        points = market.room / market.point
        detail = (
            f"{volume:g} lot loses {abs(loss):,.2f} {currency} at a {points:,.0f}-point stop, "
            f"margin {margin:,.2f} {currency}"
        )
        free = _finite(getattr(account, "margin_free", None))
        if free is None:
            return Outcome.PASS, detail
        detail += f", free margin {free:,.2f} {currency}"
        if free < margin * MARGIN_ROOM:
            return Outcome.FAIL, f"{detail}: too little free margin for the test's trades"
        return Outcome.PASS, detail

    def _buy(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        quote = self._quote(market)
        room = market.room
        volume = split_volume(market.spec)
        plan = self._plan(
            market,
            Direction.LONG,
            OrderType.MARKET,
            volume,
            quote.ask,
            quote.ask - room,
            quote.ask + room,
        )
        result = self._broker.open(plan)
        if not result.ok:
            return self._refusal(run, result)
        position = self._position(result.position)
        if position is None:
            text = f"MT5 opened position {result.position}, but it is not among the open ones"
            return Outcome.FAIL, f"{text} [{attempts_text(result)}]"
        run.position = position
        slippage = (result.price - result.requested_price) / market.point
        detail = (
            f"position {position.ticket}: {position.volume:g} lot at {market.text(result.price)} "
            f"(asked {market.text(result.requested_price)}, {slippage:+.1f} points), SL "
            f"{market.text(position.sl)}, TP {market.text(position.tp)} "
            f"[{attempts_text(result)}]"
        )
        problem = stops_problem(market, position, plan)
        if problem:
            return Outcome.FAIL, f"{detail}. {problem}"
        return Outcome.PASS, detail

    def _move_stop(self, run: _Run) -> tuple[Outcome, str]:
        market, position = _market(run), run.position
        if position is None:
            return Outcome.SKIP, NO_POSITION
        target = market.price(position.sl + market.room / 2)
        result = self._broker.modify(position, target, position.tp)
        if not result.ok:
            return Outcome.FAIL, f"{result.text} [{attempts_text(result)}]"
        moved = self._position(position.ticket)
        if moved is None:
            return Outcome.FAIL, f"position {position.ticket} is gone after the SL was moved"
        run.position = moved
        if not market.same(moved.sl, target):
            text = f"MT5 answered done, but the SL is {market.text(moved.sl)}"
            return Outcome.FAIL, f"{text}, not {market.text(target)}"
        text = (
            f"SL {market.text(position.sl)} to {market.text(moved.sl)}, as breakeven and the "
            f"trailing stop move it [{attempts_text(result)}]"
        )
        return Outcome.PASS, text

    def _close_part(self, run: _Run) -> tuple[Outcome, str]:
        market, position = _market(run), run.position
        if position is None:
            return Outcome.SKIP, NO_POSITION
        part = market.spec.volume_min
        if position.volume < 2 * part - VOLUME_EPSILON:
            return Outcome.SKIP, f"the position is the minimum lot ({part:g}): it cannot be split"
        result = self._broker.close(position, part)
        if not result.ok:
            return Outcome.FAIL, f"{result.text} [{attempts_text(result)}]"
        left = self._position(position.ticket)
        if left is None:
            run.position, run.closed_ticket = None, position.ticket
            return Outcome.FAIL, f"the whole position was closed, not {part:g} lot of it"
        run.position = left
        expected = round(position.volume - part, 8)
        text = (
            f"closed {part:g} of {position.volume:g} lot at {market.text(result.price)}, "
            f"{left.volume:g} lot left [{attempts_text(result)}]"
        )
        if abs(left.volume - expected) > VOLUME_EPSILON:
            return Outcome.FAIL, f"{text}; {expected:g} lot should be left"
        return Outcome.PASS, text

    def _close(self, run: _Run) -> tuple[Outcome, str]:
        market, position = _market(run), run.position
        if position is None:
            return Outcome.SKIP, NO_POSITION
        result = self._broker.close(position)
        if not result.ok:
            return Outcome.FAIL, f"{result.text} [{attempts_text(result)}]"
        run.position, run.closed_ticket = None, position.ticket
        if self._position(position.ticket) is not None:
            return Outcome.FAIL, f"MT5 answered done, but position {position.ticket} is open"
        text = f"closed {position.volume:g} lot at {market.text(result.price)}"
        return Outcome.PASS, f"{text} [{attempts_text(result)}]"

    def _history(self, run: _Run) -> tuple[Outcome, str]:
        ticket = run.closed_ticket
        if not ticket:
            return Outcome.SKIP, "no closed position to look up"
        deadline = self._clock() + DEAL_WAIT_SECONDS
        while True:
            summary = summarize_position(self._broker.deals(ticket) or ())
            if summary is not None and summary.closed:
                text = (
                    f"{summary.deals} deals, profit {summary.profit:+.2f}, commission "
                    f"{summary.commission:+.2f}, swap {summary.swap:+.2f}, net "
                    f"{summary.net_profit:+.2f}, closed by {summary.exit_reason or 'unknown'}"
                )
                return Outcome.PASS, text
            if self._clock() >= deadline:
                text = (
                    f"no closed trade for position {ticket} in the MT5 history after "
                    f"{DEAL_WAIT_SECONDS:.0f} s: the journal would miss it"
                )
                return Outcome.FAIL, text
            self._pause(POLL_SECONDS / 2)

    def _sell(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        quote = self._quote(market)
        room = market.room
        volume = market.spec.volume_min
        plan = self._plan(
            market,
            Direction.SHORT,
            OrderType.MARKET,
            volume,
            quote.bid,
            quote.bid + room,
            quote.bid - room,
        )
        result = self._broker.open(plan)
        if not result.ok:
            return self._refusal(run, result)
        position = self._position(result.position)
        if position is None:
            return Outcome.FAIL, f"MT5 opened position {result.position}, but it is not open"
        problem = stops_problem(market, position, plan)
        closed = self._broker.close(position)
        if not closed.ok:
            return Outcome.FAIL, f"sold, but the close of {position.ticket} failed: {closed.text}"
        if self._position(position.ticket) is not None:
            return Outcome.FAIL, f"MT5 answered done, but position {position.ticket} is open"
        text = (
            f"position {position.ticket}: sold {volume:g} lot at {market.text(result.price)}, "
            f"bought back at {market.text(closed.price)} [{attempts_text(result)}; "
            f"{attempts_text(closed)}]"
        )
        if problem:
            return Outcome.FAIL, f"{text}. {problem}"
        return Outcome.PASS, text

    def _place_expiring(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        if not market.expiry_allowed:
            text = (
                f"{market.symbol} takes no expiry time on pending orders (expiry "
                f"{flags(market.expiration_mode, EXPIRATION_FLAGS)}): the London breakout's "
                "orders carry one and would be refused"
            )
            return Outcome.FAIL, text
        quote = self._quote(market)
        room = market.room
        entry = quote.bid + 3 * room
        expires = whole_minute(quote.time + EXPIRY_SECONDS)
        plan = self._plan(
            market,
            Direction.SHORT,
            OrderType.LIMIT,
            market.spec.volume_min,
            entry,
            entry + room,
            entry - room,
            expiration=expires,
        )
        result = self._broker.open(plan)
        if not (result.ok and result.placed):
            return self._refusal(run, result)
        run.expiring, run.expires_at = result.order, expires
        life = expires - quote.time
        text = (
            f"sell limit {result.order} at {market.text(plan.price)}, expires at "
            f"{server_clock(expires)} server time ({life:.0f} s); checked after the "
            f"breakout step [{attempts_text(result)}]"
        )
        return Outcome.PASS, text

    def _pending(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        quote = self._quote(market)
        room = market.room
        away = 2 * room
        expires = None
        if market.expiry_allowed:
            expires = whole_minute(quote.time + PENDING_LIFE_SECONDS)
        kinds = (
            ("buy limit", Direction.LONG, OrderType.LIMIT, quote.ask - away),
            ("sell limit", Direction.SHORT, OrderType.LIMIT, quote.bid + away),
            ("buy stop", Direction.LONG, OrderType.STOP, quote.ask + away),
            ("sell stop", Direction.SHORT, OrderType.STOP, quote.bid - away),
        )
        placed: list[tuple[str, OrderPlan, int]] = []
        problems: list[str] = []
        for label, direction, kind, entry in kinds:
            sign = 1.0 if direction is Direction.LONG else -1.0
            sl, tp = entry - sign * room, entry + sign * room
            volume = market.spec.volume_min
            plan = self._plan(market, direction, kind, volume, entry, sl, tp, expiration=expires)
            result = self._broker.open(plan)
            if result.ok and result.placed:
                placed.append((label, plan, result.order))
            else:
                problems.append(f"{label}: {result.text}")
        run.pending = [ticket for _, _, ticket in placed]
        orders = {order.ticket: order for order in self._orders()}
        for label, plan, ticket in placed:
            problem = order_problem(market, orders.get(ticket), plan)
            if problem:
                problems.append(f"{label} {ticket}: {problem}")
        shown = ", ".join(f"{label} {market.text(plan.price)}" for label, plan, _ in placed)
        life = "good till cancelled"
        if expires is not None:
            life = f"expiry {server_clock(expires)} server time"
        if problems:
            text = "; ".join(problems)
            return Outcome.FAIL, f"{text}. Placed: {shown}" if shown else text
        return Outcome.PASS, f"all 4 placed and found in MT5 with their SL and TP: {shown}; {life}"

    def _cancel(self, run: _Run) -> tuple[Outcome, str]:
        if not run.pending:
            return Outcome.SKIP, "no pending orders to cancel"
        count = len(run.pending)
        orders = {order.ticket: order for order in self._orders()}
        problems: list[str] = []
        for ticket in run.pending:
            order = orders.get(ticket)
            if order is None:
                problems.append(f"{ticket} was gone before the cancel")
                continue
            result = self._broker.cancel(order)
            if not result.ok:
                problems.append(f"{ticket}: {result.text}")
        left = [order.ticket for order in self._orders() if order.ticket in run.pending]
        run.pending = left
        if left:
            problems.append("still in MT5: " + ", ".join(str(ticket) for ticket in left))
        if problems:
            return Outcome.FAIL, "; ".join(problems)
        text = (
            f"{count} cancelled and gone from MT5, as the bot cancels a breakout's other side "
            "or the order of an expired signal"
        )
        return Outcome.PASS, text

    def _breakout(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        quote = self._quote(market)
        room = market.room
        gap = (market.spec.stops_level + BREAKOUT_GAP_POINTS) * market.point
        expires = None
        if market.expiry_allowed:
            expires = whole_minute(quote.time + BREAKOUT_LIFE_SECONDS)
        volume = market.spec.volume_min
        high, low = quote.ask + gap, quote.bid - gap
        sides = {
            Direction.LONG: self._plan(
                market,
                Direction.LONG,
                OrderType.STOP,
                volume,
                high,
                high - room,
                high + room,
                comment=BREAKOUT_COMMENT,
                expiration=expires,
            ),
            Direction.SHORT: self._plan(
                market,
                Direction.SHORT,
                OrderType.STOP,
                volume,
                low,
                low + room,
                low - room,
                comment=BREAKOUT_COMMENT,
                expiration=expires,
            ),
        }
        tickets: dict[int, OrderPlan] = {}
        for plan in sides.values():
            result = self._broker.open(plan)
            if not (result.ok and result.placed):
                self._cancel_tickets(tickets)
                return Outcome.FAIL, f"{side_name(plan)}: {result.text} [{attempts_text(result)}]"
            tickets[result.order] = plan
        started = self._clock()
        filled: list[BrokerPosition] = []
        while not self.stopping:
            filled = [p for p in self._positions() if p.comment == BREAKOUT_COMMENT]
            if filled or self._clock() - started >= self._fill_wait:
                break
            self._pause(POLL_SECONDS)
        waited = self._clock() - started
        problems = self._cancel_tickets(tickets)
        if not filled:
            if problems:
                text = "; ".join(problems)
                return Outcome.FAIL, f"neither side filled in {waited:.0f} s, and {text}"
            text = (
                f"the price did not reach either side ({gap / market.point:.0f} points away) in "
                f"{waited:.0f} s, so both were cancelled; run it again in a busier hour"
            )
            return Outcome.SKIP, text
        closes = [self._broker.close(position) for position in filled]
        for position, closed in zip(filled, closes, strict=True):
            if not closed.ok:
                problems.append(f"close {position.ticket}: {closed.text}")
        if len(filled) > 1:
            text = (
                f"both sides filled within {waited:.0f} s, before one could be cancelled (a very "
                "fast market); both were closed"
            )
            return Outcome.SKIP, text
        position = filled[0]
        plan = sides[position.direction]
        problem = stops_problem(market, position, plan)
        if problem:
            problems.append(problem)
        text = (
            f"the {side_name(plan)} filled after {waited:.0f} s at "
            f"{market.text(position.price_open)}; the other side was cancelled and the "
            "position closed"
        )
        if problems:
            return Outcome.FAIL, f"{text}. " + "; ".join(problems)
        return Outcome.PASS, text

    def _expiry(self, run: _Run) -> tuple[Outcome, str]:
        ticket = run.expiring
        if not ticket:
            return Outcome.SKIP, "no expiring order (see the step before the pending orders)"
        market = _market(run)
        limit = max(0.0, run.expires_at - self._quote(market).time) + self._expiry_grace
        started = self._clock()
        while True:
            pending = self._is_pending(ticket)
            if not pending or self.stopping or self._clock() - started >= limit:
                break
            self._pause(EXPIRY_POLL_SECONDS)
        if not pending:
            run.expiring = 0
            late = max(0, self._quote(market).time - run.expires_at)
            text = (
                f"gone {late} s after its expiry, without a cancel from the app, as the bot's "
                "breakout orders end with their session"
            )
            return Outcome.PASS, text
        problems = self._cancel_tickets({ticket})
        run.expiring = 0
        if self.stopping:
            return Outcome.SKIP, "stopped before it expired; it was cancelled"
        text = (
            f"still in MT5 {limit:.0f} s after its expiry, so it was cancelled: the bot's "
            "breakout orders would stay open after their session"
        )
        return Outcome.FAIL, "; ".join([text, *problems])

    def _refused(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        quote = self._quote(market)
        room = market.room
        before = {position.ticket for position in self._positions()}
        plan = self._plan(
            market,
            Direction.LONG,
            OrderType.MARKET,
            market.spec.volume_min,
            quote.ask,
            quote.ask + room,
            quote.ask + 2 * room,
        )
        result = self._broker.open(plan)
        opened = [position for position in self._positions() if position.ticket not in before]
        for position in opened:
            self._broker.close(position)
        sends = sum(1 for attempt in result.attempts if attempt.action.endswith("_send"))
        if result.ok or opened:
            text = f"MT5 took a buy with its SL above the price ({result.text}); it was closed"
            return Outcome.FAIL, text
        if sends > 1:
            text = f"the refused order was sent {sends} times ({result.text}): never send again"
            return Outcome.FAIL, text
        text = (
            f"refused, as it should be: {result.text}; nothing opened, not sent again "
            f"[{attempts_text(result)}]"
        )
        return Outcome.PASS, text

    def _kill(self, run: _Run) -> tuple[Outcome, str]:
        market = _market(run)
        quote = self._quote(market)
        room = market.room
        volume = market.spec.volume_min
        expires = None
        if market.expiry_allowed:
            expires = whole_minute(quote.time + PENDING_LIFE_SECONDS)
        entry = quote.ask - 2 * room
        buy = self._plan(
            market,
            Direction.LONG,
            OrderType.MARKET,
            volume,
            quote.ask,
            quote.ask - room,
            quote.ask + room,
        )
        limit = self._plan(
            market,
            Direction.LONG,
            OrderType.LIMIT,
            volume,
            entry,
            entry - room,
            entry + room,
            expiration=expires,
        )
        opened, placed = self._broker.open(buy), self._broker.open(limit)
        started = self._clock()
        closed, cancelled, problems = self._close_everything()
        seconds = self._clock() - started
        left = self._leftovers()
        for result in (opened, placed):
            if not result.ok:
                problems.insert(0, f"a trade to close could not be opened: {result.text}")
        if left:
            problems.append("still in MT5: " + ", ".join(left))
        text = f"closed {closed} position(s) and cancelled {cancelled} order(s) in {seconds:.1f} s"
        if problems:
            return Outcome.FAIL, f"{text}; " + "; ".join(problems)
        return Outcome.PASS, f"{text}; nothing of the test is left in MT5"

    def _refusal(self, run: _Run, result: OrderResult) -> tuple[Outcome, str]:
        """A refused order: the market is closed (the rest is skipped) or a real failure."""
        text = f"{result.text} [{attempts_text(result)}]"
        if result.retcode == MARKET_CLOSED:
            run.closed = True
            return Outcome.SKIP, f"the market is closed: {text}"
        return Outcome.FAIL, text

    def _step(self, symbol: str, name: str, work: Work) -> Outcome:
        started = self._clock()
        try:
            outcome, detail = work()
        except MT5Error as error:
            outcome, detail = Outcome.FAIL, f"{error.title}. {error.fix}".strip()
        except Exception as error:  # a broken step must still end in the clean-up
            outcome, detail = Outcome.FAIL, f"{type(error).__name__}: {error}"
        self._add(symbol, name, outcome, detail, self._clock() - started)
        return outcome

    def _add(self, symbol: str, name: str, outcome: Outcome, detail: str, seconds: float) -> None:
        step = Step(symbol, name, outcome, detail, round(seconds, 1))
        self._steps.append(step)
        if self._log is not None:
            self._log("WARNING" if outcome is Outcome.FAIL else "INFO", step.line())
        if self._listener is not None:
            with contextlib.suppress(Exception):
                self._listener(step)

    def _read(self, name: str, work: Callable[[MT5Api], T]) -> T:
        return self._gateway.run(name, work, timeout=READ_TIMEOUT_SECONDS)

    def _quote(self, market: Market) -> Quote:
        quote = self._read("symbol_info_tick", partial(read_quote, symbol=market.symbol))
        if quote is None:
            raise DemoTestError(f"MT5 has no live price for {market.symbol}")
        return quote

    def _plan(
        self,
        market: Market,
        direction: Direction,
        order_type: OrderType,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        *,
        comment: str = COMMENT,
        expiration: int | None = None,
    ) -> OrderPlan:
        return OrderPlan(
            symbol=market.symbol,
            direction=direction,
            order_type=order_type,
            volume=volume,
            price=market.price(price),
            sl=market.price(sl),
            tp=market.price(tp),
            magic=DEMO_MAGIC,
            comment=comment,
            deviation=DEVIATION_POINTS,
            filling=0,  # the live broker sets the symbol's filling mode
            digits=market.spec.digits,
            expiration=expiration,
        )

    def _positions(self) -> list[BrokerPosition]:
        return self._broker.positions({DEMO_MAGIC}) or []

    def _orders(self) -> list[BrokerOrder]:
        return self._broker.orders({DEMO_MAGIC}) or []

    def _position(self, ticket: int) -> BrokerPosition | None:
        return next((item for item in self._positions() if item.ticket == ticket), None)

    def _is_pending(self, ticket: int) -> bool:
        return any(order.ticket == ticket for order in self._orders())

    def _cancel_tickets(self, tickets: Collection[int]) -> list[str]:
        """Cancel those of `tickets` that are still pending; what went wrong, if anything."""
        problems: list[str] = []
        for order in self._orders():
            if order.ticket in tickets:
                result = self._broker.cancel(order)
                if not result.ok:
                    problems.append(f"the cancel of {order.ticket} failed: {result.text}")
        left = [str(order.ticket) for order in self._orders() if order.ticket in tickets]
        if left:
            problems.append("still in MT5: " + ", ".join(left))
        return problems

    def _close_everything(self) -> tuple[int, int, list[str]]:
        """Close every position and cancel every order of the test's magic number."""
        closed, cancelled = 0, 0
        problems: list[str] = []
        for position in self._positions():
            result = self._broker.close(position)
            if result.ok:
                closed += 1
            else:
                problems.append(f"the close of position {position.ticket} failed: {result.text}")
        for order in self._orders():
            result = self._broker.cancel(order)
            if result.ok:
                cancelled += 1
            else:
                problems.append(f"the cancel of order {order.ticket} failed: {result.text}")
        return closed, cancelled, problems

    def _leftovers(self) -> tuple[str, ...]:
        positions = self._broker.positions({DEMO_MAGIC})
        orders = self._broker.orders({DEMO_MAGIC})
        if positions is None or orders is None:
            return (f"unknown, MT5 did not list them: look for magic {DEMO_MAGIC} in MT5",)
        found = [f"position {item.ticket} {item.symbol} {item.volume:g} lot" for item in positions]
        found += [f"order {item.ticket} {item.symbol}" for item in orders]
        return tuple(found)

    def _clean_up(self) -> tuple[str, ...]:
        """Close what the test still has open; what is left, if anything."""
        try:
            self._close_everything()
            return self._leftovers()
        except Exception as error:  # MT5 is gone: the user has to look
            text = f"unknown ({type(error).__name__}: {error})"
            return (f"{text}: look for magic {DEMO_MAGIC} in MT5",)
