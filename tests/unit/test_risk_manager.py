"""The risk manager against a FakeMT5 behind the real gateway (spec C6, G3 row 7)."""

import math

from app.domain.signals import Direction, SignalState
from app.mt5 import api
from app.mt5.history_sync import deal_from_mt5
from app.mt5.risk_reads import read_picture
from app.risk.limits import AccountPicture
from app.risk.limits_state import DAILY_LIMIT, MANUAL_STOP
from app.risk.risk_manager import server_midnight
from app.risk.settings import RiskSettings
from app.storage.risk_store import RiskRepository
from app.storage.signal_store import SignalRepository
from app.strategies.registry import MAGIC_NUMBERS, strategy_for_magic
from tests.fakes.fake_mt5 import FakeMT5, FakeSymbol, make_position, make_trade_deal
from tests.unit.risk_helpers import ACCOUNT, CLOCK, connected, risk_manager, spec_of
from tests.unit.signal_helpers import MORNING, make_signal, pipeline
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import analysis_at, london_days

EURUSD = make_signal(symbol="EURUSD.m", entry=1.10000, sl=1.09900, tp=1.10200)
NOW = float(MORNING)


def test_a_signal_within_every_limit_is_sized_from_order_calc_profit() -> None:
    fake = connected()
    logs: list[tuple[str, str]] = []
    with risk_manager(fake, logs=logs) as manager:
        decision = manager.evaluate(EURUSD, spec_of(fake, "EURUSD.m"), CLOCK, NOW)
    assert decision.ok and decision.volume == 0.5, decision.reason
    assert math.isclose(decision.risk_money, 50.0) and decision.loss_per_lot == 100.0
    assert decision.margin_required is not None and decision.failed_names() == []
    assert "order_calc_profit" in fake.calls and "order_calc_margin" in fake.calls
    assert fake.trading_calls == []
    assert any("Risk passed" in text and "allowed risk" in text for _, text in logs)
    usage = manager.snapshot.usage
    assert usage is not None and usage.open_trades == 0 and usage.halted == ""


def test_currency_exposure_across_open_positions_rejects_and_is_recorded() -> None:
    fake = connected()
    fake.positions = [
        make_position(1, "EURUSD.m", price_open=1.08, sl=1.079, volume=0.5, magic=0),
        make_position(2, "GBPUSD.m", price_open=1.27012, sl=1.26912, volume=0.5, magic=0),
    ]
    gold = make_signal(symbol="XAUUSD.m", entry=2385.42, sl=2375.42, tp=2405.42, digits=2)
    with temporary_store() as store, risk_manager(fake, store) as manager:
        decision = manager.evaluate(gold, spec_of(fake, "XAUUSD.m"), CLOCK, NOW)
        assert not decision.ok and decision.failed_names() == ["currency exposure"]
        assert decision.volume == 0.0 and "currency exposure" in decision.reason
        events = RiskRepository(store).recent_events(ACCOUNT)
        assert [event.type for event in events] == ["exposure_block"]
        usage = manager.snapshot.usage
        assert usage is not None and usage.open_trades == 2
        assert round(usage.exposure_percent["USD"], 2) == -1.0


def test_a_hit_daily_limit_survives_a_restart() -> None:
    fake = connected()
    spec = spec_of(fake, "EURUSD.m")
    with temporary_store() as store:
        with risk_manager(fake, store) as manager:
            manager.refresh(NOW, force=True)
            fake.accounts[0].balance = 9_790.0  # 2.1% down today
            manager.refresh(NOW + 60, force=True)
            assert manager.snapshot.halted == DAILY_LIMIT
        fake.initialize()  # the first gateway shut MT5 down when it stopped
        fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
        with risk_manager(fake, store) as restarted:
            decision = restarted.evaluate(EURUSD, spec, CLOCK, NOW + 120)
        assert not decision.ok and "trading allowed" in decision.failed_names()
        types = [event.type for event in RiskRepository(store).recent_events(ACCOUNT)]
        assert types.count(DAILY_LIMIT) == 1


def test_stop_and_re_enable_go_through_the_analysis_thread() -> None:
    fake = connected()
    with temporary_store() as store, risk_manager(fake, store) as manager:
        manager.request_stop("testing")
        assert manager.snapshot.halted == ""  # applied on the next refresh only
        manager.refresh(NOW)
        assert manager.snapshot.halted == MANUAL_STOP
        manager.request_enable()
        manager.refresh(NOW + 1)
        assert manager.snapshot.halted == ""
        types = [event.type for event in RiskRepository(store).recent_events(ACCOUNT)]
        assert sorted(types) == ["kill_switch", "re_enabled"]


def test_no_account_rejects_and_errors_never_escape() -> None:
    fake = connected()
    with risk_manager(fake) as manager:
        fake.terminal_running = False
        decision = manager.evaluate(EURUSD, None, CLOCK, NOW)
        assert not decision.ok and "no account" in decision.reason
        fake.terminal_running = True
        decision = manager.evaluate(make_signal(symbol="NOPE"), None, CLOCK, NOW)
        assert not decision.ok and "could not calculate the loss" in decision.reason


def trade(ticket: int, position: int, entry: int, time_s: int, **changes: object) -> object:
    deal_type = api.DEAL_TYPE_BUY if entry == api.DEAL_ENTRY_IN else api.DEAL_TYPE_SELL
    values: dict[str, object] = {"entry": entry, "deal_type": deal_type, "time_s": time_s}
    values.update(changes)
    return make_trade_deal(ticket, position, **values)  # type: ignore[arg-type]


def picture_now(fake: FakeMT5) -> AccountPicture:
    day = CLOCK.broker_date(NOW).isoformat()
    found = read_picture(
        fake,
        day=day,
        day_start_server=server_midnight(day),
        read_at=NOW,
        strategy_of=strategy_for_magic,
        cache={},
    )
    assert found is not None
    return found


def test_the_commission_estimate_comes_from_the_history() -> None:
    fake = connected()
    raw = [
        trade(1, 7, api.DEAL_ENTRY_IN, 1, volume=1.0, commission=-3.5, symbol="EURUSD.m"),
        trade(2, 7, api.DEAL_ENTRY_OUT, 2, volume=1.0, commission=-3.5, symbol="EURUSD.m"),
    ]
    with temporary_store() as store, risk_manager(fake, store) as manager:
        store.upsert_deals(ACCOUNT, [deal_from_mt5(deal) for deal in raw])
        decision = manager.evaluate(EURUSD, spec_of(fake, "EURUSD.m"), CLOCK, NOW)
    assert decision.commission_per_lot == 7.0 and "traded lots" in decision.commission_source
    assert decision.volume == 0.46  # 50 / 107


def test_todays_deals_count_entries_realized_and_money_moves() -> None:
    fake = connected()
    midnight = server_midnight(CLOCK.broker_date(NOW).isoformat())
    bot = MAGIC_NUMBERS["trend_pullback"]
    deposit: dict[str, object] = {"deal_type": api.DEAL_TYPE_BALANCE, "profit": 500.0}
    fake.deals = [
        trade(1, 5, api.DEAL_ENTRY_IN, midnight - 600, magic=bot),  # yesterday
        trade(2, 6, api.DEAL_ENTRY_IN, midnight + 600, magic=bot),
        trade(3, 7, api.DEAL_ENTRY_IN, midnight + 700),
        trade(4, 6, api.DEAL_ENTRY_OUT, midnight + 900, profit=-30.0, commission=-3.5, magic=bot),
        trade(5, 0, api.DEAL_ENTRY_IN, midnight + 1000, **deposit),
    ]
    found = picture_now(fake)
    assert (found.bot_entries_today, found.manual_entries_today) == (1, 1)
    assert found.money.realized_today == -33.5 and found.money.deposits_today == 500.0
    assert fake.trading_calls == []


def test_positions_get_their_risk_and_strategy_from_mt5() -> None:
    fake = connected()
    breakout = MAGIC_NUMBERS["london_breakout"]
    fake.positions = [
        make_position(1, "XAUUSD.m", long=False, price_open=2385.0, sl=2390.0, magic=breakout),
        make_position(2, "EURUSD.m", profit=-12.0),
    ]
    gold, manual = picture_now(fake).positions
    assert gold.direction is Direction.SHORT and gold.strategy == "london_breakout"
    assert gold.risk_money is not None and math.isclose(gold.risk_money, 50.0)
    assert (gold.base, gold.quote) == ("XAU", "USD")
    assert manual.manual and manual.risk_money is None and manual.counted_risk == 12.0


def test_the_pipeline_sizes_passing_signals_and_saves_the_lot() -> None:
    fake = connected()
    fake.symbols.append(FakeSymbol("EURUSD", 1.1))
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15), spread=0.00002)
    with temporary_store() as store, risk_manager(fake, store) as manager:
        signals = pipeline(store=SignalRepository(store), risk=manager)
        spec = spec_of(fake, "EURUSD")
        created = signals.on_analysis(analysis, clock=CLOCK, spec=spec, spread=2e-5, now=now)
        assert len(created) == 2
        for record in created:
            assert record.signal.state is SignalState.PENDING_APPROVAL, record.reject_reason
            assert record.trace.complete
            assert record.volume is not None and record.volume > 0
            risk_steps = [step.name for step in record.trace.steps if step.stage == "risk"]
            assert "lot size" in risk_steps and "daily loss" in risk_steps
        saved = {item.id: item for item in SignalRepository(store).recent()}
        assert saved[created[0].id].volume == created[0].volume


def test_the_pipeline_rejects_by_risk_with_the_reason() -> None:
    fake = connected()
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15), spread=0.00002)
    with risk_manager(fake, settings=RiskSettings(max_open_trades=1)) as manager:
        signals = pipeline(risk=manager)  # "EURUSD" is unknown to this MT5: no loss per lot
        created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
    assert created and all(r.signal.state is SignalState.RISK_REJECTED for r in created)
    for record in created:
        assert record.trace.final_decision == "RISK_REJECTED" and record.trace.complete
        assert "loss per lot" in record.reject_reason and record.volume is None
