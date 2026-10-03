"""The execution engine (spec C5-C8): approved signals become orders, open trades are
managed, closed trades are synced back from the deal history, and the kill switch.

Runs in the `market-analysis` thread, called by the signal pipeline: `execute` when the user
approved a signal, `cycle` on every analysis cycle. It talks to brokers only through the
`Broker` interface, so Paper and Semi-auto share every line here. It never touches a
position without one of the bot's magic numbers (manual trades are never modified).

- Approval: re-check the signal against the live price (still valid, entry tolerance,
  spread, stops), size it again with the risk manager at the current price, then send it
  with SL and TP in the request. A failed re-check expires the signal.
- Management: MFE/MAE in R on every cycle; the strategy's optional rules (break-even, ATR
  trailing, partial close, time exit) through `app.domain.management`.
- Close: a managed position that is gone from MT5 is read back from the deal history (real
  profit, commission, swap, the exit reason) and the signal becomes CLOSED.
- Recovery: the managed trades are saved after every change; on a restart or reconnect bot
  positions found in MT5 are matched by ticket or by the signal id in the comment, and an
  unknown bot position is adopted and logged.
- Kill switch: close every bot position, cancel every bot pending order, stop new entries.
"""

from __future__ import annotations

import contextlib
import json
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

from app.analysis.bars import TF_SECONDS
from app.brokers.base import Broker, BrokerOrder, BrokerPosition, OrderResult
from app.brokers.market import MarketReads
from app.brokers.requests import OrderPlan, choose_filling
from app.core.clock import BrokerClock
from app.core.execution_settings import ExecutionConfig
from app.domain.history import summarize_position
from app.domain.management import ManagedPosition, plan, track
from app.domain.modes import OperatingMode
from app.domain.orders import (
    Check,
    failed,
    market_price,
    recheck_at_approval,
    round_price,
    short_id,
)
from app.domain.signals import Direction, OrderType, Signal, SignalRecord, SignalState
from app.mt5.models import SymbolSpec
from app.risk.risk_manager import RiskDecision
from app.storage.signal_store import iso_time
from app.storage.trade_store import STATE_KEY, TradeRepository, bot_trade_id

CLOSE_SYNC_CYCLES = 30  # cycles to wait for the closing deal before closing without it
KEEP_MESSAGES = 30

Log = Callable[[str, str], None]
Listener = Callable[["ExecutionSnapshot"], None]


class ExecutionRisk(Protocol):
    def evaluate(
        self,
        signal: Signal,
        spec: SymbolSpec | None,
        clock: BrokerClock,
        now: float,
    ) -> RiskDecision: ...


@dataclass(frozen=True)
class SignalUpdate:
    """A state change the pipeline applies to a signal, with its trace lines."""

    signal_id: str
    state: SignalState
    reason: str
    checks: tuple[Check, ...] = ()


@dataclass
class Tracked:
    """One bot trade the engine manages (saved as JSON for crash recovery)."""

    mode: str  # live or paper
    ticket: int  # the position, or the pending order while `pending`
    pending: bool
    signal_id: str
    trade_id: str
    strategy: str
    symbol: str  # the broker's symbol name
    direction: str
    volume: float
    entry: float
    sl: float
    tp: float
    initial_sl: float
    magic: int
    comment: str
    opened_at: float  # UTC
    timeframe: str = "M15"
    atr: float = math.nan
    risk_money: float | None = None
    oco_group: str = ""
    best_r: float = 0.0
    worst_r: float = 0.0
    break_even_done: bool = False
    partial_done: bool = False
    missing: int = 0
    killed: bool = False

    @property
    def key(self) -> tuple[str, int]:
        return (self.mode, self.ticket)

    @property
    def side(self) -> Direction:
        return Direction(self.direction)


@dataclass(frozen=True)
class PositionView:
    mode: str
    ticket: int
    symbol: str
    direction: str
    volume: float
    entry: float
    sl: float
    tp: float
    profit: float | None
    strategy: str
    pending: bool
    best_r: float = 0.0
    worst_r: float = 0.0


@dataclass(frozen=True)
class ExecutionSnapshot:
    mode: OperatingMode = OperatingMode.PAPER
    positions: tuple[PositionView, ...] = ()
    messages: tuple[str, ...] = ()
    stopped: str = ""  # why the kill switch stopped trading, "" while running
    updated_at: float = 0.0


def _quiet(level: str, message: str) -> None:
    return None


def _tracked_from(raw: Mapping[str, Any]) -> Tracked | None:
    try:
        return Tracked(**dict(raw))
    except TypeError:
        return None


class ExecutionEngine:
    def __init__(
        self,
        config: Callable[[], ExecutionConfig],
        *,
        market: MarketReads,
        paper: Broker | None,
        live: Broker | None,
        magics: Mapping[str, int],
        risk: ExecutionRisk | None = None,
        store: TradeRepository | None = None,
        account: Callable[[], str | None] | None = None,
        clock: Callable[[], BrokerClock] | None = None,
        connected: Callable[[], bool] | None = None,
        netting: Callable[[], bool] | None = None,
        broker_symbol: Callable[[str], str] | None = None,
        stop_trading: Callable[[str], None] | None = None,
        paper_step: Callable[[], Sequence[str]] | None = None,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._market = market
        self._brokers: dict[str, Broker] = {}
        if paper is not None:
            self._brokers["paper"] = paper
        if live is not None:
            self._brokers["live"] = live
        self._magics = dict(magics)
        self._strategy_of = {number: name for name, number in self._magics.items()}
        self._risk = risk
        self._store = store
        self._account = account or (lambda: None)
        self._clock = clock or BrokerClock.assumed
        self._connected = connected or (lambda: True)
        self._netting = netting or (lambda: False)
        self._broker_symbol = broker_symbol or (lambda name: name)
        self._stop_trading = stop_trading
        self._paper_step = paper_step
        self._log = log
        self._now = utc_now
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._closes: list[tuple[str, int]] = []
        self._kills: list[str] = []
        self._tracked: dict[tuple[str, int], Tracked] = {}
        self._loaded_for: str | None = None
        self._profits: dict[tuple[str, int], float] = {}
        self._messages: list[str] = []
        self._stopped = ""
        self._snapshot = ExecutionSnapshot()

    # Public API (any thread) -----------------------------------------------------------
    @property
    def snapshot(self) -> ExecutionSnapshot:
        with self._lock:
            return self._snapshot

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def request_close(self, mode: str, ticket: int) -> None:
        """Close one bot position on the next cycle (the UI asked for a confirmation)."""
        with self._lock:
            self._closes.append((mode, int(ticket)))

    def request_kill(self, reason: str = "kill switch pressed") -> None:
        """Close every bot position, cancel every bot order, stop new entries."""
        with self._lock:
            self._kills.append(reason)

    def mode(self) -> OperatingMode:
        return self._config().mode

    def approval_block(self) -> str:
        """Why an approval cannot be sent right now, "" when it can."""
        mode = self.mode()
        if mode is OperatingMode.ANALYSIS_ONLY:
            return "Analysis-only mode places no orders"
        if mode is OperatingMode.AUTO:
            return "Auto mode needs the Go-Live gate (Phase 13)"
        if self._broker_for(mode) is None:
            return f"No broker for {mode.label} mode"
        return ""

    # Analysis thread: approval ---------------------------------------------------------
    def execute(self, record: SignalRecord, now: float | None = None) -> list[SignalUpdate]:
        """The user approved `record`: re-check, size again, send. Never raises."""
        moment = self._now() if now is None else now
        try:
            return self._execute(record, moment)
        except Exception as error:
            text = f"execution failed: {type(error).__name__}: {error}"
            self._log("ERROR", f"Signal {record.id}: {text}")
            return [SignalUpdate(record.id, SignalState.EXPIRED, text)]

    def _execute(self, record: SignalRecord, now: float) -> list[SignalUpdate]:
        signal = record.signal
        self._ensure_loaded()
        block = self.approval_block()
        if not block and self._stopped:
            block = f"trading is stopped ({self._stopped})"
        if block:
            return [SignalUpdate(signal.id, SignalState.EXPIRED, f"not sent: {block}")]
        mode = self.mode()
        broker = self._broker_for(mode)
        assert broker is not None
        settings = self._config().settings
        symbol = self._broker_symbol(signal.symbol)
        spec = self._market.spec(symbol)
        quote = self._market.quote(symbol)
        if spec is None or quote is None:
            why = f"re-check failed: no live price or symbol data for {symbol}"
            return [SignalUpdate(signal.id, SignalState.EXPIRED, why)]
        point = spec.point if spec.point > 0 else 10.0**-spec.digits
        checks = recheck_at_approval(
            signal,
            quote.bid,
            quote.ask,
            max_entry_move_r=settings.max_entry_move_r,
            max_spread_sl_fraction=settings.max_spread_sl_fraction,
            stops_level=spec.stops_level,
            point=point,
            now=now,
        )
        checks.append(self._netting_check(symbol, mode))
        bad = failed(checks)
        if bad:
            why = "re-check failed: " + "; ".join(f"{c.name} ({c.detail})" for c in bad)
            return [SignalUpdate(signal.id, SignalState.EXPIRED, why, tuple(checks))]
        market = signal.order_type is OrderType.MARKET
        price = market_price(signal.direction, quote.bid, quote.ask) if market else signal.entry
        volume = record.volume or 0.0
        risk_money = record.risk_money
        if self._risk is not None:
            sized = replace(signal, entry=price)
            decision = self._risk.evaluate(sized, spec, self._clock(), now)
            checks.append(
                Check(
                    "risk at approval",
                    decision.ok,
                    decision.volume if decision.ok else None,
                    None,
                    decision.reason,
                ),
            )
            if not decision.ok:
                why = f"risk re-check failed: {decision.reason}"
                return [SignalUpdate(signal.id, SignalState.EXPIRED, why, tuple(checks))]
            volume, risk_money = decision.volume, decision.risk_money
        if volume <= 0:
            why = "not sent: no position size (the risk manager is not running)"
            return [SignalUpdate(signal.id, SignalState.EXPIRED, why, tuple(checks))]
        clock = self._clock()
        expiration: int | None = None
        if not market:
            expiration = int(signal.expires_at + clock.offset_at(signal.expires_at) * 3600)
        order = OrderPlan(
            symbol=symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            volume=volume,
            price=round_price(price, spec.digits, spec.tick_size),
            sl=round_price(signal.sl, spec.digits, spec.tick_size),
            tp=round_price(signal.tp, spec.digits, spec.tick_size),
            magic=self._magics.get(signal.strategy, 0),
            comment=short_id(signal.id),
            deviation=settings.deviation_points,
            filling=choose_filling(spec.filling_mode),
            digits=spec.digits,
            expiration=expiration,
        )
        if order.magic == 0:
            why = f"not sent: strategy {signal.strategy} has no magic number"
            return [SignalUpdate(signal.id, SignalState.EXPIRED, why, tuple(checks))]
        updates = [
            SignalUpdate(
                signal.id,
                SignalState.APPROVED,
                "approved; re-check passed",
                tuple(checks),
            ),
            SignalUpdate(signal.id, SignalState.SENT, f"sent to {broker.mode} ({mode.label})"),
        ]
        result = broker.open(order)
        self._record_attempts(record.trace.trace_id, result)
        if not result.ok:
            self._message(f"Order failed: {signal.summary()}: {result.text}", "WARNING")
            updates.append(SignalUpdate(signal.id, SignalState.FAILED, result.text))
            return updates
        tracked = Tracked(
            mode=broker.mode,
            ticket=result.order if result.placed else result.position,
            pending=result.placed,
            signal_id=signal.id,
            trade_id="",
            strategy=signal.strategy,
            symbol=symbol,
            direction=signal.direction.value,
            volume=result.volume or volume,
            entry=result.price,
            sl=order.sl,
            tp=order.tp,
            initial_sl=order.sl,
            magic=order.magic,
            comment=order.comment,
            opened_at=now,
            timeframe=signal.timeframe,
            atr=record.atr,
            risk_money=risk_money,
            oco_group=str(signal.features.get("oco_group", "")),
        )
        account = self._account() or ""
        if result.placed:
            self._tracked[tracked.key] = tracked
            self._save_state()
            text = f"pending order {tracked.ticket} placed at {order.price}"
            self._message(f"{signal.summary()}: {text} ({broker.mode})")
            return updates
        tracked.trade_id = bot_trade_id(account, broker.mode, tracked.ticket)
        self._tracked[tracked.key] = tracked
        slippage = (result.price - result.requested_price) * signal.direction.sign / point
        latency = sum(a.latency_ms for a in result.attempts[1:])  # after the pre-check
        self._save_open(tracked, record, result.requested_price, slippage, now)
        fill = f"filled at {result.price:g} (slippage {slippage:+.1f} points, {latency:.0f} ms)"
        self._message(f"{signal.summary()}: {fill} ({broker.mode})")
        updates.append(SignalUpdate(signal.id, SignalState.FILLED, fill))
        updates.append(SignalUpdate(signal.id, SignalState.MANAGED, "position managed"))
        self._save_state()
        return updates

    def _netting_check(self, symbol: str, mode: OperatingMode) -> Check:
        if mode is OperatingMode.PAPER or not self._netting():
            return Check("netting", True, None, None, "hedging account: positions are separate")
        same = [t for t in self._tracked.values() if t.mode == "live" and t.symbol == symbol]
        detail = "netting account: a new trade would merge with the open position (blocked)"
        return Check("netting", not same, len(same), 0, detail)

    # Analysis thread: every cycle ------------------------------------------------------
    def cycle(
        self,
        now: float | None = None,
        signals: Mapping[str, SignalRecord] | None = None,
    ) -> list[SignalUpdate]:
        moment = self._now() if now is None else now
        updates: list[SignalUpdate] = []
        if not self._connected():
            self._publish(moment)
            return updates
        self._ensure_loaded()
        with self._lock:
            kills, self._kills = self._kills, []
            closes, self._closes = self._closes, []
        for reason in kills:
            updates += self._kill(reason, moment)
        for mode, ticket in closes:
            self._close_one(mode, ticket, "closed by you", moment)
        if self._paper_step is not None and "paper" in self._brokers:
            for line in self._paper_step():
                self._message(line)
        known = signals or {}
        for name, broker in self._brokers.items():
            try:
                updates += self._sync(name, broker, known, moment)
            except Exception as error:
                text = f"{type(error).__name__}: {error}"
                self._log("ERROR", f"Execution sync ({name}) failed: {text}")
        self._save_state()
        self._publish(moment)
        return updates

    def _sync(
        self,
        mode: str,
        broker: Broker,
        signals: Mapping[str, SignalRecord],
        now: float,
    ) -> list[SignalUpdate]:
        positions = broker.positions(self._magics.values())
        orders = broker.orders(self._magics.values())
        if positions is None or orders is None:
            return []  # a failed read never means "closed"
        updates: list[SignalUpdate] = []
        by_ticket = {p.ticket: p for p in positions}
        order_tickets = {o.ticket for o in orders}
        for position in positions:
            self._profits[(mode, position.ticket)] = position.profit + position.swap
            if (mode, position.ticket) not in self._tracked:
                updates += self._adopt(mode, position, signals, now)
        for tracked in [t for t in self._tracked.values() if t.mode == mode]:
            if tracked.pending:
                if tracked.ticket not in order_tickets:
                    updates += self._pending_gone(tracked, by_ticket, broker, now)
                continue
            current = by_ticket.get(tracked.ticket)
            if current is None:
                updates += self._closed(tracked, broker, now)
            else:
                updates += self._manage(tracked, current, broker, now)
        for order in orders:
            if (mode, order.ticket) not in self._tracked:
                self._adopt_order(mode, order, signals)
        return updates

    def _signal_for(self, comment: str, signals: Mapping[str, SignalRecord]) -> SignalRecord | None:
        for record in signals.values():
            if short_id(record.id) == comment:
                return record
        return None

    def _adopt(
        self,
        mode: str,
        position: BrokerPosition,
        signals: Mapping[str, SignalRecord],
        now: float,
    ) -> list[SignalUpdate]:
        record = self._signal_for(position.comment, signals)
        pending = next(
            (
                t
                for t in self._tracked.values()
                if t.mode == mode and t.pending and t.comment == position.comment
            ),
            None,
        )
        if pending is not None:
            return []  # the pending order filled; handled by `_pending_gone`
        signal = record.signal if record is not None else None
        sl = position.sl
        tracked = Tracked(
            mode=mode,
            ticket=position.ticket,
            pending=False,
            signal_id=signal.id if signal is not None else "",
            trade_id=bot_trade_id(self._account() or "", mode, position.ticket),
            strategy=self._strategy_of.get(position.magic, ""),
            symbol=position.symbol,
            direction=position.direction.value,
            volume=position.volume,
            entry=position.price_open,
            sl=sl,
            tp=position.tp,
            initial_sl=signal.sl if signal is not None else sl,
            magic=position.magic,
            comment=position.comment,
            opened_at=now,
            timeframe=signal.timeframe if signal is not None else "M15",
            atr=record.atr if record is not None else math.nan,
            risk_money=record.risk_money if record is not None else None,
            oco_group=str(signal.features.get("oco_group", "")) if signal is not None else "",
        )
        self._tracked[tracked.key] = tracked
        found = f"signal {signal.id}" if signal is not None else "no matching signal"
        self._message(
            f"Adopted bot position {position.ticket} {position.symbol} "
            f"({tracked.strategy or position.magic}, {mode}): {found}",
            "WARNING",
        )
        if position.sl <= 0:
            self._message(f"Bot position {position.ticket} has no stop loss!", "ERROR")
        self._save_open(tracked, record, math.nan, math.nan, now, event="adopted")
        if signal is None:
            return []
        return [
            SignalUpdate(signal.id, SignalState.FILLED, f"position {position.ticket} found in MT5"),
            SignalUpdate(signal.id, SignalState.MANAGED, "position managed again after a restart"),
        ]

    def _adopt_order(
        self,
        mode: str,
        order: BrokerOrder,
        signals: Mapping[str, SignalRecord],
    ) -> None:
        record = self._signal_for(order.comment, signals)
        signal = record.signal if record is not None else None
        tracked = Tracked(
            mode=mode,
            ticket=order.ticket,
            pending=True,
            signal_id=signal.id if signal is not None else "",
            trade_id="",
            strategy=self._strategy_of.get(order.magic, ""),
            symbol=order.symbol,
            direction=order.direction.value,
            volume=order.volume,
            entry=order.price,
            sl=order.sl,
            tp=order.tp,
            initial_sl=order.sl,
            magic=order.magic,
            comment=order.comment,
            opened_at=self._now(),
            timeframe=signal.timeframe if signal is not None else "M15",
            atr=record.atr if record is not None else math.nan,
            risk_money=record.risk_money if record is not None else None,
            oco_group=str(signal.features.get("oco_group", "")) if signal is not None else "",
        )
        self._tracked[tracked.key] = tracked
        self._message(f"Adopted bot pending order {order.ticket} {order.symbol} ({mode})")

    def _pending_gone(
        self,
        tracked: Tracked,
        positions: Mapping[int, BrokerPosition],
        broker: Broker,
        now: float,
    ) -> list[SignalUpdate]:
        del self._tracked[tracked.key]
        filled = positions.get(tracked.ticket) or next(
            (p for p in positions.values() if p.comment == tracked.comment),
            None,
        )
        if filled is None:
            why = "cancelled by the kill switch" if tracked.killed else "expired or cancelled"
            self._message(f"Pending order {tracked.ticket} {tracked.symbol}: {why}")
            return [SignalUpdate(tracked.signal_id, SignalState.FAILED, f"pending order {why}")]
        account = self._account() or ""
        requested = tracked.entry
        tracked = replace(
            tracked,
            ticket=filled.ticket,
            pending=False,
            entry=filled.price_open,
            volume=filled.volume,
            trade_id=bot_trade_id(account, tracked.mode, filled.ticket),
            opened_at=now,
        )
        self._tracked[tracked.key] = tracked
        point = self._point(tracked.symbol)
        sign = tracked.side.sign
        slippage = (filled.price_open - requested) * sign / point if point > 0 else math.nan
        self._save_open(tracked, None, requested, slippage, now)
        self._message(f"Pending order filled: {tracked.symbol} at {filled.price_open:g}")
        updates = [
            SignalUpdate(tracked.signal_id, SignalState.FILLED, f"filled at {filled.price_open:g}"),
            SignalUpdate(tracked.signal_id, SignalState.MANAGED, "position managed"),
        ]
        if tracked.oco_group:
            updates += self._cancel_siblings(tracked, broker)
        return updates

    def _cancel_siblings(self, filled: Tracked, broker: Broker) -> list[SignalUpdate]:
        updates: list[SignalUpdate] = []
        siblings = [
            t
            for t in self._tracked.values()
            if t.pending and t.mode == filled.mode and t.oco_group == filled.oco_group
        ]
        for sibling in siblings:
            order = BrokerOrder(
                sibling.ticket,
                sibling.symbol,
                sibling.side,
                0,
                sibling.volume,
                sibling.entry,
                sibling.sl,
                sibling.tp,
                sibling.magic,
                sibling.comment,
                0,
            )
            result = broker.cancel(order)
            self._record_attempts(sibling.signal_id, result)
            if result.ok:
                del self._tracked[sibling.key]
                why = "cancelled: the other side of the breakout filled (OCO)"
                updates.append(SignalUpdate(sibling.signal_id, SignalState.FAILED, why))
                self._message(f"Pending order {sibling.ticket}: {why}")
            else:
                self._message(f"OCO cancel of {sibling.ticket} failed: {result.text}", "ERROR")
        return updates

    def _closed(self, tracked: Tracked, broker: Broker, now: float) -> list[SignalUpdate]:
        deals = broker.deals(tracked.ticket)
        summary = summarize_position(list(deals or []))
        if summary is None or not summary.closed:
            tracked.missing += 1
            if tracked.missing < CLOSE_SYNC_CYCLES:
                return []
            del self._tracked[tracked.key]
            why = "the position is closed; its closing deal is not in the history yet"
            self._message(f"Position {tracked.ticket}: {why}", "WARNING")
            if not tracked.signal_id:
                return []
            return [SignalUpdate(tracked.signal_id, SignalState.CLOSED, why)]
        del self._tracked[tracked.key]
        risk = tracked.risk_money
        r_multiple = summary.net_profit / risk if risk and risk > 0 else None
        account = self._account() or ""
        row_id = tracked.trade_id or bot_trade_id(account, tracked.mode, tracked.ticket)
        row: dict[str, Any] = {
            "id": row_id,
            "account_id": self._account(),
            "close_price": summary.close_price,
            "profit": summary.profit,
            "commission": summary.commission,
            "swap": summary.swap,
            "fee": summary.fee,
            "net_profit": summary.net_profit,
            "outcome": summary.outcome,
            "exit_reason": summary.exit_reason,
            "r_multiple": round(r_multiple, 3) if r_multiple is not None else None,
            "mfe_r": round(tracked.best_r, 3),
            "mae_r": round(tracked.worst_r, 3),
            "close_time": iso_time(now),
        }
        if tracked.mode == "live":
            row.pop("close_time")  # the history import sets it from the deal time
        self._save_row(row)
        self._event(tracked, "close", now, new=summary.net_profit, reason=summary.exit_reason or "")
        text = (
            f"closed by {summary.exit_reason or 'unknown'}: net {summary.net_profit:+.2f} "
            f"(profit {summary.profit:+.2f}, commission {summary.commission:+.2f}, "
            f"swap {summary.swap:+.2f})"
        )
        if r_multiple is not None:
            text += f", {r_multiple:+.2f} R"
        self._message(f"{tracked.symbol} position {tracked.ticket} {text}")
        if not tracked.signal_id:
            return []
        return [SignalUpdate(tracked.signal_id, SignalState.CLOSED, text)]

    def _manage(
        self,
        tracked: Tracked,
        position: BrokerPosition,
        broker: Broker,
        now: float,
    ) -> list[SignalUpdate]:
        quote = self._market.quote(tracked.symbol)
        spec = self._market.spec(tracked.symbol)
        if quote is None or spec is None:
            return []
        managed = ManagedPosition(
            ticket=tracked.ticket,
            symbol=tracked.symbol,
            direction=tracked.side,
            entry=position.price_open,
            sl=position.sl,
            tp=position.tp,
            initial_sl=tracked.initial_sl,
            volume=position.volume,
            opened_at=tracked.opened_at,
            bar_seconds=TF_SECONDS.get(tracked.timeframe, 0),
            atr=tracked.atr,
            digits=spec.digits,
            point=spec.point,
            tick_size=spec.tick_size,
            stops_level=spec.stops_level,
            freeze_level=spec.freeze_level,
            volume_min=spec.volume_min,
            volume_step=spec.volume_step,
            best_r=tracked.best_r,
            worst_r=tracked.worst_r,
            break_even_done=tracked.break_even_done,
            partial_done=tracked.partial_done,
        )
        managed = track(managed, quote.bid, quote.ask)
        tracked.best_r, tracked.worst_r = managed.best_r, managed.worst_r
        tracked.sl, tracked.tp, tracked.volume = position.sl, position.tp, position.volume
        if self.mode() is OperatingMode.ANALYSIS_ONLY or self._stopped:
            return []
        rules = self._config().management_for(tracked.strategy)
        for action in plan(managed, rules, quote.bid, quote.ask, now):
            if action.kind == "modify_sl" and action.sl is not None:
                result = broker.modify(position, action.sl, position.tp)
                self._record_attempts(tracked.signal_id, result)
                if result.ok:
                    self._event(
                        tracked,
                        "modify_sl",
                        now,
                        old=position.sl,
                        new=action.sl,
                        reason=action.reason,
                    )
                    tracked.sl = action.sl
                    tracked.break_even_done |= action.reason.startswith("break-even")
                    moved = f"SL to {action.sl:g} ({action.reason})"
                    self._message(f"{tracked.symbol} {tracked.ticket}: {moved}")
                else:
                    self._message(f"SL change of {tracked.ticket} failed: {result.text}", "WARNING")
            elif action.kind in ("partial_close", "close"):
                result = broker.close(position, action.volume)
                self._record_attempts(tracked.signal_id, result)
                kind = "partial_close" if action.kind == "partial_close" else "close"
                if result.ok:
                    self._event(tracked, kind, now, new=action.volume, reason=action.reason)
                    tracked.partial_done |= kind == "partial_close"
                    self._message(f"{tracked.symbol} {tracked.ticket}: {action.reason}")
                else:
                    why = f"{action.reason} of {tracked.ticket} failed: {result.text}"
                    self._message(why, "WARNING")
        return []

    # Kill switch and closes ------------------------------------------------------------
    def _kill(self, reason: str, now: float) -> list[SignalUpdate]:
        self._stopped = reason
        self._message(f"KILL SWITCH: {reason}", "WARNING")
        if self._stop_trading is not None:
            try:
                self._stop_trading(f"kill switch: {reason}")
            except Exception as error:
                self._log("ERROR", f"Kill switch could not stop new entries: {error}")
        for name, broker in self._brokers.items():
            positions = broker.positions(self._magics.values()) or []
            orders = broker.orders(self._magics.values()) or []
            for order in orders:
                result = broker.cancel(order)
                self._record_attempts("kill-switch", result)
                tracked = self._tracked.get((name, order.ticket))
                if tracked is not None:
                    tracked.killed = True
                level = "INFO" if result.ok else "ERROR"
                self._message(f"Kill switch: cancel order {order.ticket}: {result.text}", level)
            for position in positions:
                result = broker.close(position)
                self._record_attempts("kill-switch", result)
                level = "INFO" if result.ok else "ERROR"
                self._message(f"Kill switch: close {position.ticket}: {result.text}", level)
        return []

    def resume(self) -> None:
        """Allow approvals again after the kill switch (the risk stop is lifted separately)."""
        with self._lock:
            self._kills.clear()
        self._stopped = ""
        self._message("Trading resumed after the kill switch")

    def _close_one(self, mode: str, ticket: int, reason: str, now: float) -> None:
        broker = self._brokers.get(mode)
        tracked = self._tracked.get((mode, ticket))
        if broker is None or tracked is None:
            why = f"Close of {ticket} refused: not a bot position this app manages"
            self._message(why, "WARNING")
            return
        positions = broker.positions(self._magics.values()) or []
        position = next((p for p in positions if p.ticket == ticket), None)
        if position is None:
            return
        result = broker.close(position)
        self._record_attempts(tracked.signal_id, result)
        level = "INFO" if result.ok else "WARNING"
        self._message(f"Close {tracked.symbol} {ticket}: {result.text}", level)
        if result.ok:
            self._event(tracked, "close_request", now, reason=reason)

    # Persistence -------------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        account = self._account()
        if account is None or account == self._loaded_for:
            return
        self._loaded_for = account
        self._tracked = {}
        if self._store is None:
            return
        try:
            text = self._store.get_state(f"{STATE_KEY}:{account}")
            raw = json.loads(text) if text else []
        except (ValueError, TypeError):
            raw = []
        for item in raw:
            tracked = _tracked_from(item)
            if tracked is not None:
                self._tracked[tracked.key] = tracked
        if self._tracked:
            self._message(f"Resumed managing {len(self._tracked)} bot trade(s) after a restart")

    def _save_state(self) -> None:
        account = self._account()
        if self._store is None or account is None:
            return
        payload = json.dumps([asdict(t) for t in self._tracked.values()])
        try:
            self._store.set_state(f"{STATE_KEY}:{account}", payload)
        except Exception as error:
            self._log("ERROR", f"Execution state could not be saved: {type(error).__name__}")

    def _save_open(
        self,
        tracked: Tracked,
        record: SignalRecord | None,
        requested: float,
        slippage: float,
        now: float,
        *,
        event: str = "open",
    ) -> None:
        signal = record.signal if record is not None else None
        row: dict[str, Any] = {
            "id": tracked.trade_id,
            "account_id": self._account(),
            "mode": tracked.mode,
            "source": "bot",
            "ticket": tracked.ticket,
            "position_id": tracked.ticket,
            "magic": tracked.magic,
            "symbol": tracked.symbol,
            "direction": "buy" if tracked.side is Direction.LONG else "sell",
            "volume": tracked.volume,
            "open_price": tracked.entry,
            "sl_initial": tracked.initial_sl,
            "tp_initial": tracked.tp,
            "risk_money": tracked.risk_money,
        }
        if tracked.signal_id:
            row["signal_id"] = tracked.signal_id
        if math.isfinite(requested):
            row["requested_price"] = requested
        if math.isfinite(slippage):
            row["slippage"] = round(slippage, 2)
        if tracked.mode == "paper":
            row["open_time"] = iso_time(now)
        if record is not None and record.probability.value is not None:
            row["predicted_probability"] = record.probability.value
        if signal is not None:
            row["session_label"] = str(signal.features.get("session", ""))
        self._save_row(row)
        self._event(tracked, event, now, new=tracked.entry, payload={"comment": tracked.comment})

    def _save_row(self, row: Mapping[str, Any]) -> None:
        if self._store is None:
            return
        try:
            self._store.save_trade(row)
        except Exception as error:
            text = f"{type(error).__name__}: {error}"
            self._log("ERROR", f"Trade {row.get('id')} could not be saved: {text}")

    def _event(
        self,
        tracked: Tracked,
        kind: str,
        now: float,
        *,
        old: object = None,
        new: object = None,
        reason: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if self._store is None or not tracked.trade_id:
            return
        try:
            self._store.record_event(
                self._account(),
                tracked.trade_id,
                kind,
                now,
                old=old,
                new=new,
                reason=reason,
                payload=payload,
            )
        except Exception as error:
            self._log("ERROR", f"Trade event could not be saved: {type(error).__name__}")

    def _record_attempts(self, trace_id: str, result: OrderResult) -> None:
        for attempt in result.attempts:
            self._log(
                "INFO",
                f"{attempt.action} #{attempt.attempt} {attempt.request.get('symbol', '')}: "
                f"{attempt.retcode_text} ({attempt.latency_ms:.0f} ms)",
            )
        if self._store is None or not result.attempts:
            return
        try:
            self._store.record_attempts(self._account(), trace_id, result.attempts)
        except Exception as error:
            self._log("ERROR", f"MT5 requests could not be saved: {type(error).__name__}")

    # Helpers ---------------------------------------------------------------------------
    def _broker_for(self, mode: OperatingMode) -> Broker | None:
        if mode is OperatingMode.PAPER:
            return self._brokers.get("paper")
        if mode is OperatingMode.SEMI_AUTO:
            return self._brokers.get("live")
        return None

    def _point(self, symbol: str) -> float:
        spec = self._market.spec(symbol)
        return spec.point if spec is not None and spec.point > 0 else 0.0

    def _message(self, text: str, level: str = "INFO") -> None:
        self._log(level, text)
        stamp = time.strftime("%H:%M:%S", time.gmtime(self._now()))
        self._messages.append(f"{stamp} UTC  {text}")
        del self._messages[:-KEEP_MESSAGES]

    def _publish(self, now: float) -> None:
        views = tuple(
            PositionView(
                mode=t.mode,
                ticket=t.ticket,
                symbol=t.symbol,
                direction=t.direction,
                volume=t.volume,
                entry=t.entry,
                sl=t.sl,
                tp=t.tp,
                profit=None if t.pending else self._profits.get(t.key),
                strategy=t.strategy,
                pending=t.pending,
                best_r=t.best_r,
                worst_r=t.worst_r,
            )
            for t in self._tracked.values()
        )
        snapshot = ExecutionSnapshot(
            mode=self.mode(),
            positions=views,
            messages=tuple(reversed(self._messages)),
            stopped=self._stopped,
            updated_at=now,
        )
        with self._lock:
            self._snapshot = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(snapshot)
