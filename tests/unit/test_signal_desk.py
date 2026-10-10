"""The Signal desk in the pipeline and the engine (docs/SIGNAL_DESK.md 2.4 to 2.6): pasted
signals become one leg per target, run the normal filters and risk, and wait for the user;
Auto never sends them by itself."""

import pytest

from app.core.clock import BrokerClock
from app.domain.modes import OperatingMode
from app.domain.signals import Direction, SignalState
from app.engine.signal_desk import leg_shares, new_request, summary
from app.engine.signal_pipeline import SignalPipeline
from app.risk.risk_manager import risk_share
from app.signals.parse import parse
from app.strategies.manual_signal import ManualSignal, needs_confirmation
from app.strategies.registry import MAGIC_NUMBERS, STRATEGIES, strategy_for_magic
from tests.unit.execution_helpers import NOW, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.signal_helpers import make_signal, pipeline
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import analysis_at, context, frames, london_days

CLOCK = BrokerClock.assumed()


def watching() -> tuple[SignalPipeline, float, float]:
    """A pipeline that has analysed EURUSD at the London open, the moment and the bid."""
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    signals = pipeline(())
    signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
    return signals, now, analysis.price


def buy_text(price: float) -> str:
    return f"EURUSD buy sl {price - 0.002:.5f} tp {price + 0.0025:.5f} tp {price + 0.004:.5f}"


def test_a_pasted_signal_becomes_one_waiting_leg_per_target() -> None:
    signals, now, price = watching()
    request = new_request(parse(buy_text(price)), now)
    signals.submit(request)
    signals.on_cycle(now + 1)
    result = signals.snapshot.desk[0]
    assert result.ok, result.message
    assert result.message == "2 orders wait for your confirmation"
    assert result.plan is not None
    legs = [r for r in signals.snapshot.signals if r.id in result.signal_ids]
    assert len(legs) == 2
    assert sorted(r.signal.tp for r in legs) == sorted(result.plan.tps)
    for record in legs:
        signal = record.signal
        assert signal.state is SignalState.PENDING_APPROVAL
        assert (signal.strategy, signal.direction) == ("manual_signal", Direction.LONG)
        assert needs_confirmation(signal)
        assert signal.features["leg_group"] == request.id
        assert signal.features["risk_share"] == 0.5
        assert signal.reason.startswith("pasted: EURUSD buy")
        assert record.trace.complete, record.trace.missing_stages()
    assert signals.snapshot.symbols == ("EURUSD",)


def test_the_same_signal_twice_is_filtered_as_a_duplicate() -> None:
    signals, now, price = watching()
    signals.submit(new_request(parse(buy_text(price)), now))
    signals.on_cycle(now + 1)
    signals.submit(new_request(parse(buy_text(price)), now + 2))
    signals.on_cycle(now + 3)
    second = signals.snapshot.desk[0]
    assert not second.ok
    assert second.message == "one pending signal per symbol, strategy and side"


def test_unknown_symbols_missing_stops_and_wrong_prices_are_refused() -> None:
    signals, now, price = watching()
    texts = {
        "GBPUSD buy sl 1.2 tp 1.3": "GBPUSD is not on the watchlist yet",
        "EURUSD buy tp 1.2": "missing: stop loss",
        f"EURUSD buy limit {price + 0.001:.5f} sl {price - 0.002:.5f} tp {price + 0.004:.5f}": (
            "order side: a buy limit must be below the ask"
        ),
    }
    ids = {}
    for text in texts:
        request = new_request(parse(text), now)
        ids[request.id] = text
        signals.submit(request)
    signals.on_cycle(now + 1)
    results = {ids[result.request_id]: result for result in signals.snapshot.desk}
    for text, expected in texts.items():
        assert not results[text].ok
        assert expected in results[text].message, results[text].message
    assert signals.snapshot.signals == ()


def test_legs_share_one_trades_risk() -> None:
    assert leg_shares(0.04, 3, 0.01, 0.01) == pytest.approx((0.5, 0.25, 0.25))
    assert leg_shares(0.015, 2, 0.01, 0.01) == (1.0,)
    assert leg_shares(0.005, 2, 0.01, 0.01) == (1.0,)
    assert leg_shares(float("nan"), 2, 0.0, 0.0) == (0.5, 0.5)
    assert risk_share(make_signal()) == 1.0
    assert risk_share(make_signal(features={"risk_share": 0.25})) == 0.25
    assert risk_share(make_signal(features={"risk_share": "half"})) == 1.0
    assert risk_share(make_signal(features={"risk_share": 3.0})) == 1.0
    assert summary(["PENDING_APPROVAL"], [""]) == (True, "order waits for your confirmation")
    assert summary(["PENDING_APPROVAL", "RISK_REJECTED"], ["", "daily loss"]) == (
        False,
        "1 of 2 orders wait; the others: daily loss",
    )


def test_manual_signals_have_a_magic_number_but_no_rules() -> None:
    assert MAGIC_NUMBERS["manual_signal"] == 26_070_007
    assert strategy_for_magic(26_070_007) == "manual_signal"
    assert "manual_signal" not in STRATEGIES
    assert len(set(MAGIC_NUMBERS.values())) == len(MAGIC_NUMBERS)
    evaluation = ManualSignal().evaluate(context(frames(london_days())))
    assert evaluation.signals == ()
    assert evaluation.state.value == "none"


def test_auto_never_sends_a_signal_that_waits_for_you_but_your_confirm_does() -> None:
    fake = connected()
    record = eurusd_record(strategy="manual_signal", features={"confirm": "user"})
    with (
        temporary_store() as store,
        rig(fake, store, OperatingMode.AUTO, auto_gate=lambda signal: "no approval") as r,
    ):
        assert r.engine.cycle(NOW, {record.id: record}) == []
        assert r.engine.cycle(NOW + 1, {record.id: record}) == []
        assert fake.trading_calls == []
        updates = r.engine.execute(record, NOW + 2)
        assert [update.state for update in updates] == [
            SignalState.APPROVED,
            SignalState.SENT,
            SignalState.FILLED,
            SignalState.MANAGED,
        ], [update.reason for update in updates]
        assert len(fake.positions) == 1
