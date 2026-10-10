"""Each channel's own settings and what happens to its signals (docs/SIGNAL_DESK.md 3.4 and
3.5, phase 21d). Pure: the numbers come in, a decision goes out.

A channel starts in **Paper trial**: its signals are read, parsed and counted, but no card
asks for money. In **Live** every signal that passes becomes an order card in the AI Lab,
sized with the channel's own budget (its risk per trade of its equity = budget + what its
trades made) and still waiting for your hold-to-confirm. **Off** reads nothing new. A
channel's daily loss stop pauses its cards for the day; its drawdown stop sends it back to
Paper trial. The account's limits always apply on top.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.risk.budget import Budget
from app.signals.prefilter import Kind
from app.signals.words import resolve_symbol

MARKET_MINUTES = 5  # a channel's market card is stale sooner than a pasted one
PENDING_MINUTES = 480


class ChannelMode(StrEnum):
    OFF = "off"
    PAPER = "paper"  # Paper trial: read and counted, no card asks for money
    LIVE = "live"  # an order card for each signal, every one needs your hold


class Ask(StrEnum):
    ASK = "ask"
    ALWAYS = "always"
    NEVER = "never"


class ChannelPolicy(BaseModel):
    """One channel's settings (table tg_channels, column settings_json)."""

    model_config = ConfigDict(extra="ignore")

    mode: ChannelMode = Field(default=ChannelMode.PAPER)
    budget: float = Field(default=0.0, ge=0, description="money the channel may use")
    risk_percent: float = Field(default=2.0, gt=0, le=10, description="of the channel's equity")
    max_open: int = Field(default=2, ge=1, le=20, description="legs of one signal count as one")
    daily_loss_percent: float = Field(default=5.0, gt=0, le=100)
    drawdown_percent: float = Field(default=30.0, gt=0, le=100, description="of the budget")
    symbols: tuple[str, ...] = Field(default=(), description="allowed symbols, empty = all")
    aliases: dict[str, str] = Field(default_factory=dict, description="GOLD -> XAUUSD")
    follow_updates: Ask = Field(default=Ask.ASK)
    break_even: Ask = Field(default=Ask.ASK)
    market_minutes: int = Field(default=MARKET_MINUTES, ge=1, le=60)
    pending_minutes: int = Field(default=PENDING_MINUTES, ge=5, le=1440)

    @field_validator("symbols", mode="before")
    @classmethod
    def _symbols(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = value.replace(";", ",").split(",")
        if not isinstance(value, list | tuple):
            return ()
        found = [str(item).strip().upper() for item in value]
        return tuple(dict.fromkeys(item for item in found if item))

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases(cls, value: object) -> dict[str, str]:
        if isinstance(value, str):
            value = alias_pairs(value)
        if not isinstance(value, Mapping):
            return {}
        found = {str(k).strip().upper(): str(v).strip().upper() for k, v in value.items()}
        return {key: target for key, target in found.items() if key and target}

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def alias_pairs(text: str) -> dict[str, str]:
    """Aliases from the page's text: GOLD=XAUUSD, طلا=XAUUSD (commas or new lines)."""
    found: dict[str, str] = {}
    for part in text.replace(";", ",").replace("\n", ",").split(","):
        name, _, target = part.partition("=")
        if name.strip() and target.strip():
            found[name.strip()] = target.strip()
    return found


def alias_text(aliases: Mapping[str, str]) -> str:
    return ", ".join(f"{name}={target}" for name, target in aliases.items())


def policy_from_json(text: str) -> ChannelPolicy:
    """The saved settings; a broken or empty text gives the defaults (Paper trial)."""
    try:
        raw = json.loads(text or "{}")
        return ChannelPolicy.model_validate(raw if isinstance(raw, dict) else {})
    except (ValueError, ValidationError):
        return ChannelPolicy()


@dataclass(frozen=True)
class ChannelMoney:
    """A channel's money from its closed trades (net profit, account currency)."""

    closed: float = 0.0  # every closed trade of the channel
    today: float = 0.0  # those closed today (UTC)


@dataclass(frozen=True)
class BudgetState:
    equity: float  # budget + closed
    risk_money: float  # what one trade may risk
    daily_stop: bool
    drawdown_stop: bool
    text: str


def budget_state(policy: ChannelPolicy, money: ChannelMoney) -> BudgetState:
    budget = policy.budget
    if budget <= 0:
        return BudgetState(0.0, 0.0, False, False, "no budget yet")
    equity = budget + money.closed
    risk = max(0.0, equity) * policy.risk_percent / 100.0
    daily = money.today < 0 and -money.today >= equity * policy.daily_loss_percent / 100.0
    drawdown = equity <= budget * (1.0 - policy.drawdown_percent / 100.0)
    text = (
        f"budget {budget:,.2f}, closed {money.closed:+,.2f}, equity {equity:,.2f}, "
        f"{policy.risk_percent:g}% = {risk:,.2f} per trade"
    )
    return BudgetState(equity, risk, daily, drawdown, text)


def channel_budget(policy: ChannelPolicy, state: BudgetState) -> Budget:
    return Budget(state.risk_money, state.equity, policy.risk_percent)


class Action(StrEnum):
    CARD = "card"  # an order card in the AI Lab (it still waits for your hold)
    PAPER = "paper"  # counted in the Paper trial, no card
    SKIP = "skip"  # not a signal for this channel, with the reason


@dataclass(frozen=True)
class Route:
    action: Action
    reason: str
    back_to_paper: bool = False  # the drawdown stop: save the channel as Paper trial


def allowed_symbol(policy: ChannelPolicy, symbol: str, symbols: Sequence[str] = ()) -> bool:
    if not policy.symbols:
        return True
    names = tuple(symbols)
    allowed = {resolve_symbol(name, names, policy.aliases) or name for name in policy.symbols}
    return symbol.upper() in {name.upper() for name in allowed}


def route(
    policy: ChannelPolicy,
    kind: Kind,
    symbol: str,
    state: BudgetState,
    symbols: Sequence[str] = (),
) -> Route:
    """What to do with one message of a channel that is read."""
    if kind is Kind.NOISE:
        return Route(Action.SKIP, "not a signal")
    if kind is Kind.UPDATE:
        return Route(Action.SKIP, "an update of an earlier signal (follow-ups come later)")
    if policy.mode is ChannelMode.OFF:
        return Route(Action.SKIP, "the channel is off")
    if symbol and not allowed_symbol(policy, symbol, symbols):
        return Route(Action.SKIP, f"{symbol} is not in this channel's symbols")
    if policy.mode is ChannelMode.PAPER:
        return Route(Action.PAPER, "Paper trial: counted, no order card")
    if policy.budget <= 0:
        return Route(Action.PAPER, "Live needs a budget: give the channel one first")
    if state.drawdown_stop:
        why = f"drawdown stop ({policy.drawdown_percent:g}% of the budget): back to Paper trial"
        return Route(Action.PAPER, why, back_to_paper=True)
    if state.daily_stop:
        why = f"daily loss stop ({policy.daily_loss_percent:g}%): no cards today"
        return Route(Action.PAPER, why)
    if not math.isfinite(state.risk_money) or state.risk_money <= 0:
        return Route(Action.PAPER, "the channel has no money left to risk")
    return Route(Action.CARD, "Live: an order card waits for your hold")
