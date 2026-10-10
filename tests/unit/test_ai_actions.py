"""What the AI Lab chat may change (0.43.1): every proposal is checked against the current
values and the rules, changes nothing by itself, and its apply refuses stale values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.ai.actions import NEVER, STALE, ActionData, find_channel, propose
from app.channels.policy import ChannelMode, ChannelPolicy
from app.core.strategy_settings import StrategySettings
from app.domain.modes import OperatingMode
from app.engine.filters import FilterSettings
from app.storage.channel_store import ChannelSource
from app.strategies.registry import STRATEGIES, on_by_default

GOLD = ChannelSource(-1001, "Gold Room", "goldroom", "channel", 26_071_001, True, True, 1.0)


class Strategies:
    def __init__(self) -> None:
        self.settings = StrategySettings()
        self.saves = 0

    def read(self) -> StrategySettings:
        return self.settings

    def save(self, settings: StrategySettings) -> None:
        self.settings = settings
        self.saves += 1


class Channels:
    def __init__(self, policy: ChannelPolicy) -> None:
        self.saved = policy
        self.enabled = GOLD.enabled

    def source(self, channel_id: int) -> ChannelSource | None:
        return GOLD if channel_id == GOLD.channel_id else None

    def policy(self, channel_id: int) -> ChannelPolicy:
        return self.saved

    def set_policy(self, channel_id: int, policy: ChannelPolicy, now: float) -> bool:
        self.saved = policy
        return True

    def set_enabled(self, channel_id: int, enabled: bool, now: float) -> bool:
        self.enabled = enabled
        return True


@dataclass(frozen=True)
class Position:
    mode: str = "paper"
    ticket: int = 1234
    symbol: str = "XAUUSD"
    direction: str = "long"
    volume: float = 0.05
    entry: float = 4190.0
    sl: float = 4180.0
    pending: bool = False


@dataclass
class Engine:
    positions: list[Position] = field(default_factory=lambda: [Position()])
    closes: list[tuple[str, int]] = field(default_factory=list)
    stops: list[tuple[str, int, float | None]] = field(default_factory=list)

    def close(self, mode: str, ticket: int) -> None:
        self.closes.append((mode, ticket))

    def stop(self, mode: str, ticket: int, sl: float | None) -> None:
        self.stops.append((mode, ticket, sl))


def data(
    strategies: Strategies,
    channels: Channels | None = None,
    engine: Engine | None = None,
    mode: OperatingMode = OperatingMode.PAPER,
) -> ActionData:
    found = engine or Engine()
    rows = [(GOLD, channels.saved)] if channels is not None else []
    return ActionData(
        strategies=strategies.read,
        save_strategies=strategies.save,
        mode=lambda: mode,
        channels=rows,
        store=channels,
        positions=lambda: found.positions,
        close=found.close,
        stop=found.stop,
        now=lambda: 100.0,
    )


def test_a_channel_change_waits_for_the_hold_and_refuses_stale_values() -> None:
    channels = Channels(ChannelPolicy())
    args: dict[str, Any] = {
        "kind": "channel",
        "target": "gold room",
        "changes": {"budget": 100, "mode": "live"},
    }
    proposal = propose(data(Strategies(), channels), args)
    assert proposal.ok, proposal.problem
    assert "budget: 0 \u2192 100" in proposal.lines and "mode: paper \u2192 live" in proposal.lines
    assert channels.saved == ChannelPolicy()  # nothing before the hold
    assert "hold" in proposal.summary()
    assert proposal.apply is not None and proposal.apply() == "Saved."
    assert channels.saved.mode is ChannelMode.LIVE and channels.saved.budget == 100.0
    assert proposal.apply() == STALE  # the values moved since the proposal
    again = propose(data(Strategies(), channels), {**args, "changes": {"on": False}})
    assert again.ok and again.apply is not None
    again.apply()
    assert channels.enabled is False


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({"target": "Gold Room", "changes": {"mode": "live"}}, "Live needs a budget"),
        ({"target": "Silver", "changes": {"budget": 5}}, "no channel"),
        ({"target": "Gold Room", "changes": {"risk_percent": 50}}, "out of range"),
        ({"target": "Gold Room", "changes": {"password": "x"}}, "unknown or no field"),
        ({"target": "Gold Room", "changes": {"budget": 0}}, "nothing would change"),
    ],
)
def test_bad_channel_changes_are_refused(args: dict[str, Any], problem: str) -> None:
    proposal = propose(data(Strategies(), Channels(ChannelPolicy())), {"kind": "channel", **args})
    assert not proposal.ok and problem in proposal.problem
    assert proposal.summary().startswith("Not proposed:")


def test_channels_are_found_by_title_username_magic_or_a_part() -> None:
    rows = [(GOLD, ChannelPolicy())]
    for name in ("Gold Room", "@goldroom", "26071001", "gold"):
        assert find_channel(rows, name) is not None, name
    assert find_channel(rows, "silver") is None


def off_strategy() -> str:
    return next(name for name in STRATEGIES if not on_by_default(name))


def test_a_strategy_turns_on_only_without_real_orders_and_off_always() -> None:
    name = off_strategy()
    strategies = Strategies()
    on = {"kind": "strategy", "target": name, "changes": {"on": True}}
    refused = propose(data(strategies, mode=OperatingMode.SEMI_AUTO), on)
    assert not refused.ok and "Semi-auto" in refused.problem
    proposal = propose(data(strategies), on)
    assert proposal.ok and proposal.apply is not None
    assert not strategies.settings.entry(name).enabled
    proposal.apply()
    assert strategies.settings.entry(name).enabled and strategies.saves == 1
    off = {"kind": "strategy", "target": name, "changes": {"on": False}}
    allowed = propose(data(strategies, mode=OperatingMode.AUTO), off)
    assert allowed.ok and allowed.apply is not None
    allowed.apply()
    assert not strategies.settings.entry(name).enabled


def numeric_changes(name: str) -> list[tuple[str, float]]:
    """Parameters of `name` with a schema bound other than their default."""
    model = STRATEGIES[name].params_model
    schema = model.model_json_schema().get("properties", {})
    defaults = model().model_dump(mode="json")
    found: list[tuple[str, float]] = []
    for key, spec in schema.items():
        if spec.get("type") not in ("integer", "number") or isinstance(defaults[key], bool):
            continue
        for bound in ("maximum", "minimum"):
            value = spec.get(bound)
            if isinstance(value, int | float) and value != defaults[key]:
                found.append((key, value))
    return found


def test_a_parameter_change_is_checked_against_its_schema() -> None:
    name = next(iter(STRATEGIES))
    strategies = Strategies()
    for key, value in numeric_changes(name):
        args = {"kind": "strategy", "target": name, "changes": {key: value}}
        proposal = propose(data(strategies), args)
        if proposal.ok:
            break
    else:
        pytest.skip(f"{name} has no bounded numeric parameter")
    assert proposal.lines[0].startswith(f"{name}.{key}: ")
    assert proposal.apply is not None
    proposal.apply()
    assert strategies.settings.entry(name).params[key] == value
    bad = propose(data(strategies), {**args, "changes": {key: "lots"}})
    assert not bad.ok and "out of range" in bad.problem
    unknown = propose(data(strategies), {**args, "changes": {"no_such_thing": 1}})
    assert not unknown.ok and "has no parameter" in unknown.problem
    real = propose(data(Strategies(), mode=OperatingMode.AUTO), args)
    assert not real.ok and "Paper first" in real.problem


def test_a_filter_change_and_its_stale_check() -> None:
    defaults = FilterSettings().model_dump(mode="json")
    flags = [key for key, value in defaults.items() if isinstance(value, bool)]
    if not flags:
        pytest.skip("no on/off filter")
    strategies = Strategies()
    changes = {flags[0]: not defaults[flags[0]]}
    args = {"kind": "filter", "changes": changes}
    proposal = propose(data(strategies), args)
    assert proposal.ok and proposal.apply is not None
    strategies.save(strategies.settings.with_filters(FilterSettings.model_validate(changes)))
    assert proposal.apply() == STALE
    assert not propose(data(strategies, mode=OperatingMode.SEMI_AUTO), args).ok


def test_close_and_a_tighter_stop_go_to_the_engine_only_after_the_hold() -> None:
    engine = Engine()
    closing = propose(data(Strategies(), engine=engine), {"kind": "close", "target": "xauusd"})
    assert closing.ok and closing.lines == ("close XAUUSD long 0.05 lot position #1234 (paper)",)
    assert engine.closes == []
    assert closing.apply is not None
    assert "1 trade(s)" in closing.apply()
    assert engine.closes == [("paper", 1234)]
    to_entry = {"kind": "stop", "target": "1234", "value": "entry"}
    entry = propose(data(Strategies(), engine=engine), to_entry)
    assert entry.ok and entry.apply is not None
    entry.apply()
    assert engine.stops == [("paper", 1234, None)]
    wider = {"kind": "stop", "target": "1234", "value": 4170}
    assert "not tighter" in propose(data(Strategies(), engine=engine), wider).problem
    none = propose(data(Strategies(), engine=engine), {"kind": "close", "target": "EURUSD"})
    assert not none.ok and "no bot position" in none.problem


def test_what_can_never_be_proposed() -> None:
    proposal = propose(data(Strategies()), {"kind": "mode", "value": "auto"})
    assert not proposal.ok and NEVER in proposal.problem
    assert "kill switch" in NEVER and "new orders" in NEVER
