"""A small channel budget no longer refuses every signal (0.50.1, 12 October 2026): a gold
buy of the Araz Forex channel (100 USD, 2% = 2 USD) was refused because the smallest lot risks
5.25 USD, then by the one-bet check against a bot's EURUSD pending order. Now the smallest
lot may use the channel's floor, and a channel's signal skips the one-bet check."""

from __future__ import annotations

import pytest

from app.channels.policy import (
    ChannelMode,
    ChannelMoney,
    ChannelPolicy,
    budget_state,
    channel_budget,
    policy_from_json,
)
from app.domain.probability import baseline
from app.engine.filters import SAME_BET, FilterInput, FilterSettings, run_filters
from app.mt5.models import SymbolTradeMode
from app.risk.budget import Budget, budget_features, budget_of
from tests.unit.risk_helpers import CLOCK, connected, risk_manager, spec_of
from tests.unit.signal_helpers import MORNING, make_signal

NOW = float(MORNING)


def test_a_budget_carries_its_floor_but_compares_without_it() -> None:
    budget = Budget(2.0, 100.0, 2.0, 8.0)
    assert budget == Budget(2.0, 100.0, 2.0)
    found = budget_of(budget_features(budget))
    assert found is not None and found.floor == 8.0
    assert "budget_floor" not in budget_features(Budget(2.0, 100.0, 2.0))
    plain = budget_of({"budget_risk": 2.0, "budget_equity": 100.0, "budget_percent": 2.0})
    assert plain is not None and plain.floor == 0.0
    broken = budget_of({"budget_risk": 2.0, "budget_floor": "x"})
    assert broken is not None and broken.floor == 0.0


def test_a_channel_policy_sets_the_floor_from_its_equity() -> None:
    live = ChannelPolicy(mode=ChannelMode.LIVE, budget=100.0)
    assert live.floor_percent == 8.0
    state = budget_state(live, ChannelMoney(closed=20.0))
    assert channel_budget(live, state).floor == pytest.approx(9.6)  # 8% of 120
    assert budget_features(channel_budget(live, state))["budget_floor"] == 9.6
    off = live.model_copy(update={"floor_percent": 0.0})
    assert channel_budget(off, state).floor == 0.0
    assert policy_from_json('{"floor_percent": 40}') == ChannelPolicy()  # out of range


def test_the_smallest_lot_may_use_the_floor_but_not_more() -> None:
    fake = connected()
    with risk_manager(fake) as manager:
        spec = spec_of(fake, "EURUSD.m")

        def decide(budget: Budget) -> object:
            signal = make_signal(
                symbol="EURUSD.m",
                entry=1.1,
                sl=1.099,
                tp=1.102,
                features=budget_features(budget),
            )
            return manager.evaluate(signal, spec, CLOCK, NOW)

        refused = decide(Budget(0.5, 25.0, 2.0))  # no floor: the smallest lot risks 1.00
        assert not refused.ok and refused.reason.startswith("budget too small")  # type: ignore[attr-defined]
        floor = decide(Budget(0.5, 25.0, 2.0, 2.0))
        assert floor.ok and floor.volume == 0.01, floor.reason  # type: ignore[attr-defined]
        assert floor.sizing is not None  # type: ignore[attr-defined]
        assert any("floor" in line for line in floor.sizing.lines)  # type: ignore[attr-defined]
        low = decide(Budget(0.5, 25.0, 2.0, 0.8))  # the floor is below the smallest lot
        assert not low.ok and low.reason.startswith("budget too small")  # type: ignore[attr-defined]


def bet(strategy: str, same_bet: int) -> bool | None:
    values = FilterInput(
        signal=make_signal(strategy=strategy),
        now=NOW,
        clock=CLOCK,
        atr=0.0008,
        spread=0.00002,
        probability=baseline(0, 0),
        expected_value=None,
        duplicate=False,
        open_positions=0,
        losses_in_row=0,
        bars_since_loss=None,
        sessions=("London", "New York"),
        events=(),
        data_ok=True,
        data_text="fresh",
        trade_mode=SymbolTradeMode.FULL,
        same_bet=same_bet,
        same_bet_of="london_breakout",
    )
    found = [step for step in run_filters(values, FilterSettings()) if step.name == SAME_BET]
    assert len(found) == 1
    return found[0].passed


def test_a_channel_signal_is_not_held_back_by_the_one_bet_check() -> None:
    assert bet("trend_pullback", 0) is True
    assert bet("trend_pullback", 1) is False
    assert bet("channel:26071002", 1) is True
    assert bet("manual_signal", 1) is False
