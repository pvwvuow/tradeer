"""The risk manager (spec C6): sizes every signal and checks every limit. Lives in the engine,
not the UI, and nothing can skip it.

Runs in the `market-analysis` thread, called by the signal pipeline. For each signal that
passed the filters it reads the account from MT5 (positions, today's deals), rolls the limit
state, sizes the trade from `order_calc_profit` and checks the limits; the result and the
full calculation go into the decision trace and the `risk` log. The UI asks to stop or
re-enable trading through a queue, so the limit state is only written in this one thread.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.clock import BrokerClock
from app.domain.signals import Direction, Signal
from app.domain.sizing import (
    SizingInput,
    SizingResult,
    VolumeRules,
    commission_per_lot,
    size_position,
)
from app.mt5.models import SymbolSpec
from app.risk.budget import Budget, budget_of, capped_percent, small_budget_reason
from app.risk.limits import (
    AccountPicture,
    Candidate,
    LimitCheck,
    RiskUsage,
    check_trade,
    failed,
    usage,
)
from app.risk.limits_state import (
    LimitsState,
    LimitsStateStore,
    RiskEvent,
    advance,
    re_enable,
    stop_trading,
)
from app.risk.settings import RiskConfig, RiskSettings

REFRESH_SECONDS = 30.0
NO_ACCOUNT = "no-account"
BLOCK_EVENTS = ("exposure_block", "margin_block")

Log = Callable[[str, str], None]
Listener = Callable[["RiskSnapshot"], None]


class RiskBroker(Protocol):
    """MT5 reads the risk manager needs (the gateway-backed one is `GatewayRiskBroker`)."""

    def picture(
        self,
        *,
        day: str,
        day_start_server: int,
        read_at: float,
    ) -> AccountPicture | None: ...

    def loss_per_lot(
        self,
        symbol: str,
        direction: Direction,
        entry: float,
        sl: float,
    ) -> float | None: ...

    def margin(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price: float,
    ) -> float | None: ...


class SavedEvent(Protocol):
    @property
    def time(self) -> float: ...

    @property
    def type(self) -> str: ...

    @property
    def message(self) -> str: ...


class RiskStore(Protocol):
    def get_state(self, key: str) -> str | None: ...

    def set_state(self, key: str, value: str) -> None: ...

    def record_event(
        self,
        account: str | None,
        event_type: str,
        message: str,
        details: Mapping[str, Any],
        at: float,
    ) -> str: ...

    def recent_events(self, account: str | None, limit: int = ...) -> Sequence[SavedEvent]: ...

    def commission_stats(self, account: str, symbol: str) -> tuple[float, float]: ...


@dataclass(frozen=True)
class ShownEvent:
    time: float
    type: str
    message: str


@dataclass(frozen=True)
class RiskDecision:
    ok: bool
    volume: float
    risk_money: float
    reason: str
    sizing: SizingResult | None = None
    checks: tuple[LimitCheck, ...] = ()
    currency: str = ""
    equity: float = math.nan
    loss_per_lot: float | None = None
    commission_per_lot: float = 0.0
    commission_source: str = ""
    margin_required: float | None = None

    def failed_names(self) -> list[str]:
        return [check.name for check in failed(self.checks)]


@dataclass(frozen=True)
class RiskSnapshot:
    config: RiskConfig = field(default_factory=RiskConfig)
    usage: RiskUsage | None = None
    events: tuple[ShownEvent, ...] = ()
    account: str | None = None
    message: str = "Waiting for the MT5 account"
    updated_at: float = 0.0

    @property
    def halted(self) -> str:
        return self.usage.halted if self.usage is not None else ""


def _quiet(level: str, message: str) -> None:
    return None


def server_midnight(day: str) -> int:
    """Broker midnight of `day` in server seconds (MT5 deal times are server time)."""
    moment = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return int(moment.timestamp())


def volume_rules(spec: SymbolSpec | None) -> VolumeRules:
    if spec is None:
        return VolumeRules(math.nan, math.nan, math.nan)
    return VolumeRules(spec.volume_min, spec.volume_max, spec.volume_step)


def risk_share(signal: Signal) -> float:
    """The part of one trade's risk this signal may use: 1 for a normal signal, less for one
    leg of a signal split over several targets (docs/SIGNAL_DESK.md 2.4)."""
    raw = signal.features.get("risk_share", 1.0)
    try:
        share = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return share if math.isfinite(share) and 0 < share <= 1 else 1.0


def currencies(signal: Signal, spec: SymbolSpec | None) -> tuple[str, str]:
    base = spec.currency_margin if spec is not None else ""
    quote = spec.currency_profit if spec is not None else ""
    name = signal.symbol.upper()
    return base or name[:3], quote or name[3:6]


class RiskManager:
    def __init__(
        self,
        config: Callable[[], RiskConfig],
        broker: RiskBroker,
        *,
        store: RiskStore | None = None,
        account: Callable[[], str | None] | None = None,
        connected: Callable[[], bool] | None = None,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> None:
        self._connected = connected or (lambda: True)
        self._config = config
        self._broker = broker
        self._store = store
        self._states = LimitsStateStore(store, log) if store is not None else None
        self._account = account or (lambda: None)
        self._log = log
        self._now = utc_now
        self._refresh_seconds = refresh_seconds
        self.clock_source: Callable[[], BrokerClock] | None = None
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._requests: list[tuple[str, str]] = []  # ("stop", reason) or ("enable", "")
        self._state: LimitsState | None = None
        self._state_account: str | None = None
        self._next_refresh = 0.0
        self._snapshot = RiskSnapshot(config=RiskConfig())

    # Public API (any thread) -----------------------------------------------------------
    @property
    def snapshot(self) -> RiskSnapshot:
        with self._lock:
            return self._snapshot

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def request_stop(self, reason: str = "stopped by the user") -> None:
        """Stop new entries; applied on the next analysis cycle."""
        with self._lock:
            self._requests.append(("stop", reason))
            self._next_refresh = 0.0

    def request_enable(self) -> None:
        """Re-enable trading after a stop (the UI asked for a typed confirmation first)."""
        with self._lock:
            self._requests.append(("enable", ""))
            self._next_refresh = 0.0

    # Analysis thread -------------------------------------------------------------------
    def refresh(self, now: float | None = None, *, force: bool = False) -> None:
        """Read the account, roll the limit state and publish the usage (throttled)."""
        moment = self._now() if now is None else now
        with self._lock:
            due = force or moment >= self._next_refresh
            if due:
                self._next_refresh = moment + self._refresh_seconds
        if not due:
            return
        if not self._connected():
            self._publish(None, None, "Not connected to MT5")
            return
        settings = self._config().settings
        picture = self._picture(self._clock(), moment)
        if picture is None:
            self._publish(None, None, "MT5 gave no account information yet")
            return
        state = self._roll(picture, settings, moment)
        self._publish(usage(picture, state, settings), self._account(), "Limits checked")

    def evaluate(
        self,
        signal: Signal,
        spec: SymbolSpec | None,
        clock: BrokerClock,
        now: float,
    ) -> RiskDecision:
        """Size `signal` and check every limit. Never raises: an error rejects the trade."""
        try:
            return self._evaluate(signal, spec, clock, now)
        except Exception as error:
            reason = f"risk check failed: {type(error).__name__}: {error}"
            self._log("ERROR", f"Risk check of {signal.id} failed: {type(error).__name__}")
            return RiskDecision(False, 0.0, 0.0, reason)

    def _evaluate(
        self,
        signal: Signal,
        spec: SymbolSpec | None,
        clock: BrokerClock,
        now: float,
    ) -> RiskDecision:
        settings = self._config().settings
        picture = self._picture(clock, now)
        if picture is None:
            return RiskDecision(False, 0.0, 0.0, "MT5 gave no account information")
        state = self._roll(picture, settings, now)
        account = self._account()
        # MT5 needs the broker's name ("EURUSD.m"); the signal carries the watchlist name.
        symbol = spec.name if spec is not None and spec.name else signal.symbol
        loss = self._broker.loss_per_lot(symbol, signal.direction, signal.entry, signal.sl)
        commission, source = self._commission(account, symbol, settings)
        capital = picture.capital(settings)
        budget = budget_of(signal.features)  # a channel's own budget (docs/SIGNAL_DESK.md 3.4)
        percent = capped_percent(settings.risk_per_trade_percent, budget, capital)
        sizing = size_position(
            SizingInput(
                capital=capital,
                capital_basis=settings.capital_basis,
                risk_percent=percent * risk_share(signal),
                loss_per_lot=loss if loss is not None else math.nan,
                commission_per_lot=commission,
                volume=volume_rules(spec),
                currency=picture.currency,
            ),
        )
        if budget is not None:  # the decision trace says where the smaller risk came from
            line = (
                f"channel budget: {budget.money:,.2f} {picture.currency} per trade "
                f"({budget.percent:g}% of its {budget.equity:,.2f}) = {percent:.4g}% of the account"
            )
            sizing = replace(sizing, lines=(line, *sizing.lines))
        margin: float | None = None
        if sizing.ok:
            volume = sizing.volume
            margin = self._broker.margin(symbol, signal.direction, volume, signal.entry)
        base, quote = currencies(signal, spec)
        point = spec.point if spec is not None and spec.point > 0 else 10.0**-signal.digits
        candidate = Candidate(
            symbol=symbol,
            strategy=signal.strategy,
            direction=signal.direction,
            risk_money=sizing.risk_money,
            base=base,
            quote=quote,
            stop_points=signal.risk / point,
            stops_level=spec.stops_level if spec is not None else 0,
            margin_required=margin,
        )
        checks = check_trade(candidate, picture, state, settings)
        if not sizing.ok:
            checks = [check for check in checks if check.name != "margin"]
        blocked = failed(checks)
        why = sizing.reason
        if not sizing.ok and budget is not None and spec is not None and loss is not None:
            cost = max(0.0, commission) if math.isfinite(commission) else 0.0
            smallest = spec.volume_min * (loss + cost)
            share = risk_share(signal)
            limit = Budget(budget.money * share, budget.equity, budget.percent)
            why = small_budget_reason(limit, smallest, spec.volume_min, picture.currency) or why
        reasons = ([why] if not sizing.ok else []) + [check.name for check in blocked]
        ok = sizing.ok and not blocked
        decision = RiskDecision(
            ok=ok,
            volume=sizing.volume if ok else 0.0,
            risk_money=sizing.risk_money,
            reason="; ".join(reasons) if reasons else "within every limit",
            sizing=sizing,
            checks=tuple(checks),
            currency=picture.currency,
            equity=picture.equity,
            loss_per_lot=loss,
            commission_per_lot=commission,
            commission_source=source,
            margin_required=margin,
        )
        self._report(signal, decision, blocked, account, now)
        self._publish(usage(picture, state, settings), account, "Limits checked")
        return decision

    # Internals --------------------------------------------------------------------------
    def _clock(self) -> BrokerClock:
        source = self.clock_source
        if source is not None:
            with contextlib.suppress(Exception):
                return source()
        return BrokerClock.assumed()

    def _picture(self, clock: BrokerClock, now: float) -> AccountPicture | None:
        day = clock.broker_date(now).isoformat()
        return self._broker.picture(day=day, day_start_server=server_midnight(day), read_at=now)

    def _load_state(self, account: str) -> LimitsState:
        if self._state is not None and self._state_account == account:
            return self._state
        state = LimitsState()
        if self._states is not None and account != NO_ACCOUNT:
            state = self._states.load(account)
        self._state, self._state_account = state, account
        return state

    def _roll(self, picture: AccountPicture, settings: RiskSettings, now: float) -> LimitsState:
        known = self._account()
        account = known or NO_ACCOUNT
        old = self._load_state(account)
        state, events = advance(old, picture.money, settings, now)
        with self._lock:
            requests, self._requests = self._requests, []
        for kind, reason in requests:
            if kind == "stop":
                state = stop_trading(state, reason, now)
                events.append(RiskEvent("kill_switch", f"New entries stopped: {reason}"))
            else:
                state = re_enable(state, picture.equity, now)
                text = f"Trading re-enabled by the user; drawdown restarts at {picture.equity:,.2f}"
                events.append(RiskEvent("re_enabled", text))
        self._state = state
        if self._states is not None and known is not None and state != old:
            self._states.save(account, state)
        for event in events:
            self._record(event.type, event.message, event.details, now)
        return state

    def _commission(
        self,
        account: str | None,
        symbol: str,
        settings: RiskSettings,
    ) -> tuple[float, str]:
        if self._store is not None and account is not None:
            with contextlib.suppress(Exception):
                total, lots = self._store.commission_stats(account, symbol)
                estimate = commission_per_lot(total, lots)
                if estimate is not None and estimate > 0:
                    return estimate, f"from your {lots:g} traded lots of {symbol}"
        return settings.commission_per_lot, "from the risk settings"

    def _report(
        self,
        signal: Signal,
        decision: RiskDecision,
        blocked: Sequence[LimitCheck],
        account: str | None,
        now: float,
    ) -> None:
        verdict = "passed" if decision.ok else f"rejected ({decision.reason})"
        calculation = decision.sizing.text() if decision.sizing is not None else ""
        self._log("INFO", f"Risk {verdict} for {signal.summary()}: {calculation}")
        for check in blocked:
            if check.event in BLOCK_EVENTS:
                details = {
                    "signal_id": signal.id,
                    "symbol": signal.symbol,
                    "check": check.name,
                    "value": check.value,
                    "threshold": check.threshold,
                }
                message = f"{signal.summary()} blocked: {check.name} ({check.detail})"
                self._record(check.event, message, details, now)
                break

    def _record(self, kind: str, message: str, details: Mapping[str, Any], now: float) -> None:
        self._log("WARNING", f"Risk event {kind}: {message}")
        if self._store is None:
            return
        try:
            self._store.record_event(self._account(), kind, message, details, now)
        except Exception as error:
            self._log("WARNING", f"Risk event could not be saved: {type(error).__name__}")

    def _events(self, account: str | None) -> tuple[ShownEvent, ...]:
        if self._store is None:
            return ()
        try:
            saved = self._store.recent_events(account)
        except Exception:
            return ()
        return tuple(ShownEvent(item.time, item.type, item.message) for item in saved)

    def _publish(self, found: RiskUsage | None, account: str | None, message: str) -> None:
        snapshot = RiskSnapshot(
            config=self._config(),
            usage=found,
            events=self._events(account),
            account=account,
            message=message,
            updated_at=self._now(),
        )
        with self._lock:
            self._snapshot = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(snapshot)
