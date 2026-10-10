"""What the AI Lab's chat may change, always through a card you hold (0.43.1).

The agent's `propose` tool turns a wish ("turn on ema_momentum", "give gg 100 dollars and
set it to Live", "move the stop of 1234 to the entry") into a `Proposal`: what changes,
from which value to which. The page shows it as a card with Hold to apply; nothing changes
before that, and `apply` reads the settings again and refuses when they moved since.

Never here: new orders (only the Signal desk's order card sends one), the operating mode,
Go-Live, the kill switch, the account's risk limits, passwords and keys. Turning a strategy
on, its parameters and the filters change only in Paper or Analysis-only mode, like an AI
suggestion; turning a strategy off, closing a bot trade or a tighter stop are always fine.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.channels.policy import ChannelMode, ChannelPolicy
from app.core.strategy_settings import StrategyEntry, StrategySettings
from app.domain.modes import OperatingMode
from app.engine.filters import FilterSettings
from app.storage.channel_store import ChannelSource
from app.strategies.registry import STRATEGIES

KINDS = ("channel", "strategy", "filter", "close", "stop")
NEVER = (
    "new orders (paste the signal in the chat: the Signal desk makes an order card), the "
    "operating mode, Go-Live, the kill switch, the account's risk limits, passwords and keys"
)
CHANNEL_FIELDS = (
    "on",
    "mode",
    "budget",
    "risk_percent",
    "max_open",
    "daily_loss_percent",
    "drawdown_percent",
    "symbols",
    "aliases",
    "follow_updates",
    "break_even",
    "market_minutes",
    "pending_minutes",
)
MAX_TRADES = 20
STALE = "Not applied: this changed since the proposal. Ask again."
ARROW = "\u2192"


class ChannelStore(Protocol):
    """The channels' table as a proposal applies it (`ChannelRepository`)."""

    def source(self, channel_id: int) -> ChannelSource | None: ...

    def policy(self, channel_id: int) -> ChannelPolicy: ...

    def set_policy(self, channel_id: int, policy: ChannelPolicy, now: float) -> bool: ...

    def set_enabled(self, channel_id: int, enabled: bool, now: float) -> bool: ...


class Position(Protocol):
    """A bot position or order as the engine shows it (`PositionView`)."""

    @property
    def mode(self) -> str: ...

    @property
    def ticket(self) -> int: ...

    @property
    def symbol(self) -> str: ...

    @property
    def direction(self) -> str: ...

    @property
    def volume(self) -> float: ...

    @property
    def entry(self) -> float: ...

    @property
    def sl(self) -> float: ...

    @property
    def pending(self) -> bool: ...


def _no_positions() -> Sequence[Position]:
    return ()


@dataclass(frozen=True)
class ActionData:
    """What a proposal reads (in the agent's thread) and changes (in the UI thread)."""

    strategies: Callable[[], StrategySettings]
    save_strategies: Callable[[StrategySettings], None]
    mode: Callable[[], OperatingMode]
    channels: Sequence[tuple[ChannelSource, ChannelPolicy]] = ()  # read in the UI thread
    store: ChannelStore | None = None
    positions: Callable[[], Sequence[Position]] = field(default=_no_positions)
    close: Callable[[str, int], None] | None = None
    stop: Callable[[str, int, float | None], None] | None = None
    after_channel: Callable[[], object] | None = None  # read the folder again
    now: Callable[[], float] = field(default=time.time)


@dataclass(frozen=True)
class Proposal:
    kind: str
    title: str  # what it is about, e.g. "Channel gg"
    lines: tuple[str, ...] = ()  # each change, e.g. "mode: paper -> live"
    problem: str = ""  # why it cannot be proposed, "" when it can
    apply: Callable[[], str] | None = None  # after the hold (UI thread): the result line

    @property
    def ok(self) -> bool:
        return not self.problem and self.apply is not None

    def summary(self) -> str:
        """The tool's answer to the agent."""
        if not self.ok:
            return f"Not proposed: {self.problem}"
        changes = "; ".join(self.lines)
        return (
            f"Proposed ({self.title}): {changes}. Nothing changed yet: a card with these "
            "changes is shown under your answer and the user must hold its button to apply "
            "it. Tell the user to hold the button on the card if they agree."
        )


def refused(kind: str, problem: str) -> Proposal:
    return Proposal(kind, "", problem=problem)


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value) or "(none)"
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={target}" for key, target in value.items()) or "(none)"
    if value is None or value == "":
        return "(none)"
    return str(value)


def _flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "on", "true", "yes", "enabled", "enable"):
        return True
    if text in ("0", "off", "false", "no", "disabled", "disable"):
        return False
    return None


def _changes(args: Mapping[str, Any]) -> dict[str, Any]:
    """The wanted changes: `changes` {field: value} and/or one `field` with its `value`."""
    found: dict[str, Any] = {}
    raw = args.get("changes")
    if isinstance(raw, Mapping):
        found.update({str(key).strip().lower(): value for key, value in raw.items()})
    name = str(args.get("field") or "").strip().lower()
    if name:
        found[name] = args.get("value")
    return found


def _errors(error: ValidationError) -> str:
    parts = []
    for item in error.errors()[:3]:
        where = ".".join(str(part) for part in item.get("loc", ()))
        parts.append(f"{where}: {item.get('msg', 'invalid')}")
    return "; ".join(parts)


def _real_block(data: ActionData) -> str:
    mode = data.mode()
    if not mode.places_real_orders:
        return ""
    return (
        f"the operating mode is {mode.label}, which places real orders; the user must switch "
        "to Paper first (Positions page), then ask again"
    )


def propose(data: ActionData, args: Mapping[str, Any]) -> Proposal:
    """One proposal from the agent's arguments (never raises, never changes anything)."""
    kind = str(args.get("kind") or "").strip().lower()
    try:
        if kind == "channel":
            return channel_proposal(data, args)
        if kind == "strategy":
            return strategy_proposal(data, args)
        if kind == "filter":
            return filter_proposal(data, args)
        if kind == "close":
            return close_proposal(data, args)
        if kind == "stop":
            return stop_proposal(data, args)
    except Exception as error:  # a broken argument must not end the turn
        return refused(kind, f"{type(error).__name__}: {error}")
    known = ", ".join(KINDS)
    return refused(kind, f"kind must be one of {known}. Never possible here: {NEVER}.")


# Channels ----------------------------------------------------------------------------------
def find_channel(
    channels: Sequence[tuple[ChannelSource, ChannelPolicy]],
    name: str,
) -> tuple[ChannelSource, ChannelPolicy] | None:
    wanted = name.strip().lower().lstrip("@")
    for source, policy in channels:
        names = {source.title.strip().lower(), source.username.strip().lower(), str(source.magic)}
        if wanted in names:
            return source, policy
    for source, policy in channels:  # a part of the title, when only one channel has it
        if wanted and wanted in source.title.lower():
            return source, policy
    return None


def channel_proposal(data: ActionData, args: Mapping[str, Any]) -> Proposal:
    if data.store is None:
        return refused("channel", "the Telegram channel reader is not part of this app run")
    target = str(args.get("target") or args.get("channel") or "")
    found = find_channel(data.channels, target)
    if found is None:
        names = ", ".join(source.title for source, _policy in data.channels) or "none"
        return refused("channel", f"no channel {target!r}; the channels are: {names}")
    source, old = found
    wanted = _changes(args)
    unknown = sorted(set(wanted) - set(CHANNEL_FIELDS))
    if not wanted or unknown:
        fields = ", ".join(CHANNEL_FIELDS)
        return refused("channel", f"unknown or no field {unknown or ''}; use: {fields}")
    enabled = source.enabled
    lines: list[str] = []
    if "on" in wanted:
        flag = _flag(wanted.pop("on"))
        if flag is None:
            return refused("channel", "on must be true or false")
        if flag != source.enabled:
            lines.append(f"read the channel: {_text(source.enabled)} {ARROW} {_text(flag)}")
        enabled = flag
    try:
        new = ChannelPolicy.model_validate({**old.model_dump(), **wanted})
    except ValidationError as error:
        return refused("channel", f"a value is out of range: {_errors(error)}")
    if new.mode is ChannelMode.LIVE and new.budget <= 0:
        return refused("channel", "Live needs a budget > 0: say how much money it may use")
    before, after = old.model_dump(mode="json"), new.model_dump(mode="json")
    lines += [
        f"{name.replace('_', ' ')}: {_text(before[name])} {ARROW} {_text(after[name])}"
        for name in after
        if after[name] != before[name]
    ]
    if not lines:
        return refused("channel", "nothing would change: the channel already has these values")
    if new.mode is ChannelMode.LIVE:
        lines.append("Live: each signal becomes an order card that still needs your hold")
    store = data.store
    channel_id = source.channel_id

    def apply() -> str:
        current = store.source(channel_id)
        if current is None:
            return "Not applied: the channel is gone."
        if current.enabled != source.enabled or store.policy(channel_id) != old:
            return STALE
        now = data.now()
        if new != old:
            store.set_policy(channel_id, new, now)
        if enabled != source.enabled:
            store.set_enabled(channel_id, enabled, now)
            if data.after_channel is not None:
                data.after_channel()
        return "Saved."

    return Proposal("channel", f"Channel {source.title}", tuple(lines), apply=apply)


# Strategies and filters ------------------------------------------------------------------
def find_strategy(name: str) -> str:
    wanted = name.strip().lower().replace(" ", "_").replace("-", "_")
    for known, kind in STRATEGIES.items():
        if wanted in (known.lower(), kind.title.strip().lower().replace(" ", "_")):
            return known
    return ""


def strategy_proposal(data: ActionData, args: Mapping[str, Any]) -> Proposal:
    target = str(args.get("target") or args.get("strategy") or "")
    name = find_strategy(target)
    if not name:
        return refused("strategy", f"no strategy {target!r}; use one of {', '.join(STRATEGIES)}")
    settings = data.strategies()
    entry = settings.entry(name)
    wanted = _changes(args)
    if not wanted:
        return refused("strategy", "say what to change: on (true/false) or a parameter")
    model = STRATEGIES[name].params_model
    enabled = entry.enabled
    params = dict(entry.params)
    lines: list[str] = []
    for key, value in wanted.items():
        if key in ("on", "enabled"):
            flag = _flag(value)
            if flag is None:
                return refused("strategy", "on must be true or false")
            if flag != entry.enabled:
                lines.append(f"{name}: {_text(entry.enabled)} {ARROW} {_text(flag)}")
            enabled = flag
        elif key in model.model_fields:
            params[key] = value
        else:
            known = ", ".join(model.model_fields)
            return refused("strategy", f"{name} has no parameter {key}; it has: {known}")
    try:
        checked = model.model_validate(params).model_dump(mode="json")
    except ValidationError as error:
        return refused("strategy", f"a value is out of range: {_errors(error)}")
    defaults = model().model_dump(mode="json")
    for key in wanted:
        if key in model.model_fields:
            old = entry.params.get(key, defaults.get(key))
            if checked[key] != old:
                lines.append(f"{name}.{key}: {_text(old)} {ARROW} {_text(checked[key])}")
            params[key] = checked[key]
    if not lines:
        return refused("strategy", "nothing would change: it already has these values")
    riskier = enabled or params != dict(entry.params)
    block = _real_block(data) if riskier else ""
    if block:
        return refused("strategy", block)
    new = StrategyEntry(enabled=enabled, params=params)

    def apply() -> str:
        current = data.strategies()
        if current.entry(name) != entry:
            return STALE
        block = _real_block(data) if riskier else ""
        if block:
            return f"Not applied: {block}."
        data.save_strategies(current.with_entry(name, new))
        return "Saved: the analysis uses it from its next cycle."

    return Proposal("strategy", f"Strategy {name}", tuple(lines), apply=apply)


def filter_proposal(data: ActionData, args: Mapping[str, Any]) -> Proposal:
    settings = data.strategies()
    old = settings.filters
    wanted = _changes(args)
    fields = FilterSettings.model_fields
    unknown = sorted(set(wanted) - set(fields))
    if not wanted or unknown:
        return refused("filter", f"unknown or no field {unknown or ''}; use: {', '.join(fields)}")
    try:
        new = FilterSettings.model_validate({**old.model_dump(), **wanted})
    except ValidationError as error:
        return refused("filter", f"a value is out of range: {_errors(error)}")
    before, after = old.model_dump(mode="json"), new.model_dump(mode="json")
    lines = tuple(
        f"{name}: {_text(before[name])} {ARROW} {_text(after[name])}"
        for name in after
        if after[name] != before[name]
    )
    if not lines:
        return refused("filter", "nothing would change: the filters already have these values")
    block = _real_block(data)
    if block:
        return refused("filter", block)

    def apply() -> str:
        current = data.strategies()
        if current.filters != old:
            return STALE
        block = _real_block(data)
        if block:
            return f"Not applied: {block}."
        data.save_strategies(current.with_filters(new))
        return "Saved: the filters apply from the next cycle."

    return Proposal("filter", "Signal filters", lines, apply=apply)


def schema_text(model: type[BaseModel], values: Mapping[str, Any]) -> list[str]:
    """One line per field: name = value (description)."""
    lines = []
    for name, info in model.model_fields.items():
        line = f"{name} = {_text(values.get(name))}"
        if info.description:
            line += f" ({info.description})"
        lines.append(line)
    return lines


# Bot trades ------------------------------------------------------------------------------
def _ticket(value: object) -> int | None:
    try:
        number = int(str(value).strip().lstrip("#"))
    except ValueError:
        return None
    return number if number > 0 else None


def _picked(data: ActionData, target: str) -> list[Position]:
    positions = list(data.positions())
    ticket = _ticket(target)
    if ticket is not None:
        return [position for position in positions if position.ticket == ticket]
    symbol = target.strip().upper()
    if symbol in ("ALL", "*"):
        return positions
    return [position for position in positions if position.symbol.upper() == symbol]


def _line(position: Position) -> str:
    what = "order" if position.pending else "position"
    return (
        f"{position.symbol} {position.direction} {position.volume:g} lot {what} "
        f"#{position.ticket} ({position.mode})"
    )


def close_proposal(data: ActionData, args: Mapping[str, Any]) -> Proposal:
    if data.close is None:
        return refused("close", "the execution engine is not part of this app run")
    target = str(args.get("target") or args.get("ticket") or args.get("symbol") or "")
    picked = _picked(data, target)
    if not picked:
        return refused("close", f"no bot position or order {target!r} (use the positions tool)")
    if len(picked) > MAX_TRADES:
        return refused("close", f"{len(picked)} trades: at most {MAX_TRADES} per card")
    lines = tuple(
        ("cancel " if position.pending else "close ") + _line(position) for position in picked
    )
    close = data.close
    keys = [(position.mode, position.ticket) for position in picked]

    def apply() -> str:
        open_now = {(position.mode, position.ticket) for position in data.positions()}
        sent = [key for key in keys if key in open_now]
        for mode, ticket in sent:
            close(mode, ticket)
        gone = len(keys) - len(sent)
        text = f"Sent to the engine: {len(sent)} trade(s) close on its next cycle."
        return text + (f" {gone} had closed already." if gone else "")

    return Proposal("close", "Close bot trades", lines, apply=apply)


def stop_proposal(data: ActionData, args: Mapping[str, Any]) -> Proposal:
    if data.stop is None:
        return refused("stop", "the execution engine is not part of this app run")
    target = str(args.get("target") or args.get("ticket") or "")
    picked = [position for position in _picked(data, target) if not position.pending]
    if not picked:
        return refused("stop", f"no open bot position {target!r} (use the positions tool)")
    raw = str(args.get("value") if args.get("value") is not None else args.get("sl") or "")
    to_entry = raw.strip().lower() in ("entry", "break-even", "break_even", "breakeven", "be")
    price: float | None = None
    if not to_entry:
        try:
            price = float(raw)
        except ValueError:
            return refused("stop", "value is a price or entry (break-even)")
        if not math.isfinite(price) or price <= 0:
            return refused("stop", "the price must be above 0")
    lines: list[str] = []
    for position in picked:
        level = position.entry if price is None else price
        long = position.direction.lower() in ("long", "buy")
        tighter = position.sl <= 0 or (level > position.sl if long else level < position.sl)
        if not tighter:
            return refused("stop", f"{level:g} is not tighter than the stop {position.sl:g}")
        lines.append(f"{_line(position)}: stop loss {_text(position.sl)} {ARROW} {level:g}")
    stop = data.stop
    keys = [(position.mode, position.ticket) for position in picked]

    def apply() -> str:
        open_now = {(p.mode, p.ticket) for p in data.positions() if not p.pending}
        sent = [key for key in keys if key in open_now]
        for mode, ticket in sent:
            stop(mode, ticket, price)
        return f"Sent to the engine: {len(sent)} stop(s) move on its next cycle (never wider)."

    return Proposal("stop", "Move the stop loss", tuple(lines), apply=apply)
