"""Limit state that must survive a restart (spec C6): the trading day, its start equity, the
equity high-water mark and any trading stop.

`advance()` is pure: it rolls the trading day (broker date), raises the high-water mark and
stops new entries when a limit is hit. A daily-loss stop ends with the next trading day; a
drawdown stop and a manual stop stay until the user re-enables trading with a typed
confirmation. `LimitsStateStore` keeps the state as JSON in the local database, one key per
account, so a restart can never reset a hit limit.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Protocol

from app.risk.settings import RiskSettings

DAILY_LIMIT = "daily_limit"
DD_LIMIT = "dd_limit"
MANUAL_STOP = "manual"
LIVE_ROLLOVER_SECONDS = 900.0
STATE_KEY = "risk_state:{account}"
UNREADABLE = "the saved risk state could not be read: check the limits, then re-enable trading"


@dataclass(frozen=True)
class AccountMoney:
    """The account numbers `advance()` needs, from one MT5 read."""

    day: str  # broker trading day, ISO date
    balance: float
    equity: float
    realized_today: float  # profit + commission + swap + fee of today's trade deals
    deposits_today: float  # balance deals today: deposits positive, withdrawals negative


@dataclass(frozen=True)
class RiskEvent:
    type: str  # daily_limit, dd_limit, kill_switch, exposure_block, margin_block, ...
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LimitsState:
    day: str = ""
    day_start_equity: float = 0.0
    day_start_estimated: bool = False
    high_water_mark: float = 0.0
    first_equity: float = 0.0
    halted: str = ""  # "", DAILY_LIMIT, DD_LIMIT or MANUAL_STOP
    halted_at: float = 0.0
    halted_reason: str = ""
    updated_at: float = 0.0
    deposits_seen: float = 0.0  # today's money moves already applied to the drawdown basis

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> LimitsState | None:
        """The saved state, or None when it is damaged."""
        if not isinstance(raw, dict):
            return None
        known = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
        try:
            state = cls(**known)
        except TypeError:
            return None
        numbers = (state.day_start_equity, state.high_water_mark, state.first_equity)
        if not all(isinstance(v, int | float) and math.isfinite(v) for v in numbers):
            return None
        if not isinstance(state.halted, str) or not isinstance(state.day, str):
            return None
        return state

    def daily_loss(self, money: AccountMoney) -> float:
        """Today's loss in money (realized + floating), net of deposits; negative = profit."""
        return self.day_start_equity + money.deposits_today - money.equity

    def daily_loss_percent(self, money: AccountMoney) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return self.daily_loss(money) / self.day_start_equity * 100.0

    def drawdown_basis(self, settings: RiskSettings) -> float:
        return self.high_water_mark if settings.drawdown_mode == "trailing" else self.first_equity

    def drawdown_percent(self, money: AccountMoney, settings: RiskSettings) -> float:
        basis = self.drawdown_basis(settings)
        if basis <= 0:
            return 0.0
        return max(0.0, (basis - money.equity) / basis * 100.0)


def _start_of_day(state: LimitsState, money: AccountMoney, now: float) -> tuple[float, bool]:
    """Equity at the start of the trading day. Exact when the app saw the rollover; otherwise
    the balance before today's closed trades and money moves, which ignores positions that
    were already open (so it can only count more loss, never less)."""
    seen_rollover = bool(state.day) and now - state.updated_at <= LIVE_ROLLOVER_SECONDS
    if seen_rollover:
        return money.equity - money.realized_today - money.deposits_today, False
    return money.balance - money.realized_today - money.deposits_today, True


def advance(
    state: LimitsState,
    money: AccountMoney,
    settings: RiskSettings,
    now: float,
) -> tuple[LimitsState, list[RiskEvent]]:
    events: list[RiskEvent] = []
    if money.equity <= 0 or not math.isfinite(money.equity):
        return state, events
    new = state
    same_day = new.day == money.day
    moved = money.deposits_today - (new.deposits_seen if same_day else 0.0)
    if moved and new.first_equity > 0:
        # A deposit or withdrawal moves the drawdown basis with it: it is not a gain or loss.
        new = replace(
            new,
            high_water_mark=max(0.0, new.high_water_mark + moved),
            first_equity=max(0.0, new.first_equity + moved),
        )
    new = replace(new, deposits_seen=money.deposits_today)
    if new.first_equity <= 0:
        new = replace(new, first_equity=money.equity)
    if not same_day:
        start, estimated = _start_of_day(new, money, now)
        new = replace(new, day=money.day, day_start_equity=start, day_start_estimated=estimated)
        if new.halted == DAILY_LIMIT:
            new = replace(new, halted="", halted_at=0.0, halted_reason="")
            events.append(RiskEvent(DAILY_LIMIT, f"New trading day {money.day}: entries allowed"))
    new = replace(new, high_water_mark=max(new.high_water_mark, money.equity), updated_at=now)

    drawdown = new.drawdown_percent(money, settings)
    if new.halted != DD_LIMIT and drawdown >= settings.max_total_drawdown_percent:
        reason = (
            f"drawdown {drawdown:.2f}% reached the {settings.max_total_drawdown_percent:g}% "
            "limit: trading stopped until you re-enable it"
        )
        new = replace(new, halted=DD_LIMIT, halted_at=now, halted_reason=reason)
        details = {"drawdown_percent": round(drawdown, 3), "equity": money.equity}
        events.append(RiskEvent(DD_LIMIT, reason, details))
    daily = new.daily_loss_percent(money)
    if not new.halted and daily >= settings.max_daily_loss_percent:
        reason = (
            f"daily loss {daily:.2f}% reached the {settings.max_daily_loss_percent:g}% limit: "
            "no new entries until the next trading day"
        )
        new = replace(new, halted=DAILY_LIMIT, halted_at=now, halted_reason=reason)
        details = {"daily_loss_percent": round(daily, 3), "equity": money.equity}
        events.append(RiskEvent(DAILY_LIMIT, reason, details))
    return new, events


def stop_trading(state: LimitsState, reason: str, now: float) -> LimitsState:
    return replace(state, halted=MANUAL_STOP, halted_at=now, halted_reason=reason)


def re_enable(state: LimitsState, equity: float, now: float) -> LimitsState:
    """The user's typed re-enable: clears any stop and restarts the drawdown from today's
    equity."""
    basis = equity if equity > 0 else state.high_water_mark
    return replace(
        state,
        halted="",
        halted_at=0.0,
        halted_reason="",
        high_water_mark=basis,
        first_equity=basis,
        updated_at=now,
    )


class KeyValue(Protocol):
    def get_state(self, key: str) -> str | None: ...

    def set_state(self, key: str, value: str) -> None: ...


class LimitsStateStore:
    def __init__(self, store: KeyValue, log: Callable[[str, str], None] | None = None) -> None:
        self._store = store
        self._log = log

    def load(self, account: str) -> LimitsState:
        raw = self._store.get_state(STATE_KEY.format(account=account))
        if raw is None:
            return LimitsState()
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            parsed = None
        state = LimitsState.from_dict(parsed)
        if state is None:
            # Never let a damaged value clear a hit limit: stop until the user checks.
            if self._log is not None:
                self._log("WARNING", f"Saved risk state of {account} was unreadable")
            return stop_trading(LimitsState(), UNREADABLE, 0.0)
        return state

    def save(self, account: str, state: LimitsState) -> None:
        self._store.set_state(STATE_KEY.format(account=account), json.dumps(state.to_dict()))
