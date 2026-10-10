"""Channel cards, phase 21d (docs/SIGNAL_DESK.md 3.4 and 3.5): each channel's settings, its
budget and magic, where its signals go, and the budget in the risk manager and the desk."""

import math

import pytest

from app.channels.folder import Peer, PeerKind
from app.channels.policy import (
    Action,
    BudgetState,
    ChannelMode,
    ChannelMoney,
    ChannelPolicy,
    alias_pairs,
    alias_text,
    budget_state,
    channel_budget,
    policy_from_json,
    route,
)
from app.engine.signal_desk import MANUAL, new_request, too_many_open
from app.engine.signal_pipeline import SignalPipeline
from app.risk.budget import (
    Budget,
    budget_features,
    budget_of,
    capped_percent,
    small_budget_reason,
)
from app.signals.parse import parse
from app.signals.prefilter import Kind
from app.storage.channel_store import ChannelRepository
from app.storage.signal_store import iso_time
from app.strategies.manual_signal import (
    NAME,
    channel_magic,
    channel_strategy,
    with_channels,
)
from app.strategies.registry import MAGIC_NUMBERS, strategy_for_magic
from tests.unit.risk_helpers import CLOCK, connected, risk_manager, spec_of
from tests.unit.signal_helpers import MORNING, make_signal, pipeline
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import analysis_at, london_days

LIVE = ChannelPolicy(mode=ChannelMode.LIVE, budget=100.0)
NOW = float(MORNING)


def test_a_new_channel_is_a_paper_trial_with_the_spec_defaults() -> None:
    policy = policy_from_json("")
    assert policy.mode is ChannelMode.PAPER and policy.budget == 0.0
    assert (policy.risk_percent, policy.max_open) == (2.0, 2)
    assert (policy.daily_loss_percent, policy.drawdown_percent) == (5.0, 30.0)
    assert policy.symbols == () and policy.aliases == {}
    assert policy_from_json("not json") == ChannelPolicy()
    assert policy_from_json('{"budget": -5}') == ChannelPolicy()  # out of range: defaults
    saved = ChannelPolicy.model_validate(
        {
            "mode": "live",
            "budget": 100,
            "symbols": "xauusd; eurusd, XAUUSD",
            "aliases": "gold=xauusd, طلا=XAUUSD",
        },
    )
    assert saved.symbols == ("XAUUSD", "EURUSD")
    assert saved.aliases == {"GOLD": "XAUUSD", "طلا": "XAUUSD"}
    assert policy_from_json(saved.to_json()) == saved
    assert alias_pairs("a=b\nc = d, broken") == {"a": "b", "c": "d"}
    assert alias_text({"GOLD": "XAUUSD"}) == "GOLD=XAUUSD"


def test_the_channel_budget_grows_and_shrinks_with_its_closed_trades() -> None:
    state = budget_state(LIVE, ChannelMoney(closed=20.0, today=-1.0))
    assert state.equity == 120.0 and math.isclose(state.risk_money, 2.4)
    assert not state.daily_stop and not state.drawdown_stop
    assert "equity 120.00" in state.text
    daily = budget_state(LIVE, ChannelMoney(closed=-10.0, today=-4.5))  # 5% of 90 = 4.5
    assert daily.daily_stop and not daily.drawdown_stop
    drawdown = budget_state(LIVE, ChannelMoney(closed=-30.0))
    assert drawdown.drawdown_stop and drawdown.equity == 70.0
    none = budget_state(ChannelPolicy(), ChannelMoney())
    assert none.risk_money == 0.0 and none.text == "no budget yet"
    assert channel_budget(LIVE, state) == Budget(state.risk_money, 120.0, 2.0)


def test_signals_go_to_a_card_only_in_live_with_money_left() -> None:
    fine = budget_state(LIVE, ChannelMoney())
    assert route(LIVE, Kind.SIGNAL, "XAUUSD", fine).action is Action.CARD
    assert route(LIVE, Kind.NOISE, "", fine).action is Action.SKIP
    assert route(LIVE, Kind.UPDATE, "XAUUSD", fine).action is Action.SKIP
    paper = ChannelPolicy()
    assert route(paper, Kind.SIGNAL, "XAUUSD", fine).action is Action.PAPER
    off = ChannelPolicy(mode=ChannelMode.OFF)
    assert route(off, Kind.SIGNAL, "XAUUSD", fine).action is Action.SKIP
    no_money = ChannelPolicy(mode=ChannelMode.LIVE)
    found = route(no_money, Kind.SIGNAL, "XAUUSD", budget_state(no_money, ChannelMoney()))
    assert found.action is Action.PAPER and "needs a budget" in found.reason
    gold_only = LIVE.model_copy(update={"symbols": ("GOLD",), "aliases": {"GOLD": "XAUUSD"}})
    assert route(gold_only, Kind.SIGNAL, "XAUUSD.r", fine, ("XAUUSD.r",)).action is Action.CARD
    skipped = route(gold_only, Kind.SIGNAL, "EURUSD", fine)
    assert skipped.action is Action.SKIP and "not in this channel's symbols" in skipped.reason
    stopped = BudgetState(70.0, 1.4, False, True, "")
    back = route(LIVE, Kind.SIGNAL, "XAUUSD", stopped)
    assert back.action is Action.PAPER and back.back_to_paper
    today = BudgetState(90.0, 1.8, True, False, "")
    assert "daily loss stop" in route(LIVE, Kind.SIGNAL, "XAUUSD", today).reason


def test_every_channel_has_its_own_magic_and_strategy_name() -> None:
    assert channel_strategy(26_071_001) == "channel:26071001"
    assert channel_magic("channel:26071001") == 26_071_001
    assert channel_magic("channel:12") == 0 and channel_magic(NAME) == 0
    assert channel_magic("channel:abc") == 0
    with pytest.raises(ValueError):
        channel_strategy(26_070_007)
    assert strategy_for_magic(26_071_005) == "channel:26071005"
    assert strategy_for_magic(26_070_007) == NAME and strategy_for_magic(0) == ""
    magics = with_channels(MAGIC_NUMBERS)
    assert magics["channel:26071999"] == 26_071_999 and magics[NAME] == 26_070_007
    assert len(set(magics.values())) == len(magics)
    assert with_channels({"trend_pullback": 1}) == {"trend_pullback": 1}
    assert MANUAL == NAME


def test_the_budget_caps_the_risk_and_explains_a_too_small_one() -> None:
    budget = Budget(2.0, 100.0, 2.0)
    assert budget_of(budget_features(budget)) == budget
    assert budget_of({}) is None and budget_features(None) == {}
    assert budget_of({"budget_risk": "x"}) == Budget(0.0, 0.0, 0.0)
    assert capped_percent(0.5, budget, 10_000.0) == pytest.approx(0.02)
    assert capped_percent(0.5, Budget(500.0, 10_000.0, 5.0), 10_000.0) == 0.5
    assert capped_percent(0.5, None, 10_000.0) == 0.5
    assert small_budget_reason(budget, 1.5, 0.01, "USD") == ""
    reason = small_budget_reason(budget, 9.4, 0.01, "USD")
    assert reason.startswith("budget too small: the smallest lot 0.01 risks 9.40 USD")
    assert "= 9.4% of this channel" in reason and "raise the channel's budget" in reason


def test_the_risk_manager_sizes_a_channel_signal_with_its_budget() -> None:
    fake = connected()
    plain = make_signal(symbol="EURUSD.m", entry=1.10000, sl=1.09900, tp=1.10200)
    with risk_manager(fake) as manager:
        spec = spec_of(fake, "EURUSD.m")
        assert manager.evaluate(plain, spec, CLOCK, NOW).volume == 0.5  # 0.5% of 10,000
        features = budget_features(Budget(20.0, 1_000.0, 2.0))
        channel = make_signal(
            symbol="EURUSD.m",
            entry=1.10000,
            sl=1.09900,
            tp=1.10200,
            features=features,
        )
        decision = manager.evaluate(channel, spec, CLOCK, NOW)
        assert decision.ok and decision.volume == 0.2, decision.reason  # 20 / 100 per lot
        small = budget_features(Budget(0.5, 25.0, 2.0))
        tiny = make_signal(symbol="EURUSD.m", entry=1.1, sl=1.099, tp=1.102, features=small)
        refused = manager.evaluate(tiny, spec, CLOCK, NOW)
        assert not refused.ok
        assert refused.reason.startswith("budget too small: the smallest lot 0.01 risks 1.00")
        assert "4.0% of this channel" in refused.reason


def watching() -> tuple[SignalPipeline, float, float]:
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    signals = pipeline(())
    signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
    return signals, now, analysis.price


def test_a_channel_signal_is_booked_to_its_magic_and_keeps_its_limit() -> None:
    signals, now, price = watching()
    texts = {
        "buy": f"EURUSD buy sl {price - 0.002:.5f} tp {price + 0.003:.5f}",
        "sell": f"EURUSD sell sl {price + 0.002:.5f} tp {price - 0.003:.5f}",
    }
    strategy = channel_strategy(26_071_001)
    features = budget_features(Budget(2.0, 100.0, 2.0))

    def request(at: float, side: str = "buy") -> None:
        signals.submit(
            new_request(
                parse(texts[side]),
                at,
                strategy,
                strategy=strategy,
                features={**features, "confirm": "auto"},
                max_open=1,
                market_minutes=5,
            ),
        )
        signals.on_cycle(at + 1)

    request(now)
    first = signals.snapshot.desk[0]
    assert first.ok, first.message
    leg = next(r for r in signals.snapshot.signals if r.id in first.signal_ids).signal
    assert leg.strategy == strategy and leg.features["budget_risk"] == 2.0
    assert leg.features["confirm"] == "user"  # a channel text can never skip your hold
    assert leg.expires_at == pytest.approx(now + 1 + 300.0)  # 5 minutes
    assert leg.reason.startswith(f"{strategy}: EURUSD buy")
    request(now + 10, "sell")
    second = next(r for r in signals.snapshot.desk if r.request_id != first.request_id)
    assert not second.ok
    assert second.message == too_many_open(1, 1)
    assert "its limit is 1" in second.message
    assert too_many_open(1, 0) == "" and too_many_open(0, 2) == ""


def test_the_store_keeps_each_channels_settings_and_money() -> None:
    gold = Peer(-1001, "Gold Room", PeerKind.CHANNEL)
    with temporary_store() as store:
        repository = ChannelRepository(store)
        (source,) = repository.sync_folder([gold], NOW)
        assert repository.source(gold.id) == source and repository.source(5) is None
        assert repository.policy(gold.id) == ChannelPolicy()
        assert repository.set_policy(gold.id, LIVE, NOW)
        assert repository.policy(gold.id) == LIVE
        assert not repository.set_policy(5, LIVE, NOW)
        rows = [
            ("t1", source.magic, NOW - 2 * 86_400, 12.5),
            ("t2", source.magic, NOW - 60, -4.0),
            ("t3", 26_070_007, NOW - 60, 99.0),  # pasted signals are not this channel's
        ]
        with store.db.transaction() as connection:
            for trade_id, magic, closed, profit in rows:
                connection.execute(
                    "INSERT INTO trades (id, magic, close_time, net_profit, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (trade_id, magic, iso_time(closed), profit, iso_time(NOW), iso_time(NOW)),
                )
        money = repository.money(source.magic, NOW - 3_600)
        assert money == ChannelMoney(closed=8.5, today=-4.0)
        assert repository.money(26_071_999, NOW) == ChannelMoney()
