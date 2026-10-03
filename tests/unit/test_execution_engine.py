"""The execution engine end to end on a FakeMT5 behind the real gateway (spec C5-C8, G3 row 8):
approval re-check, live and paper fills with SL/TP, management, deal sync, OCO, the kill
switch and crash recovery."""

from __future__ import annotations

from app.domain.management import ManagementSettings
from app.domain.modes import OperatingMode
from app.domain.signals import Direction, OrderType, SignalState
from app.engine.execution import SignalUpdate
from app.mt5 import api
from app.strategies.registry import MAGIC_NUMBERS
from tests.fakes.fake_mt5 import make_position
from tests.unit.execution_helpers import NOW, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store

MAGIC = MAGIC_NUMBERS["trend_pullback"]


def states(updates: list[SignalUpdate]) -> list[SignalState]:
    return [update.state for update in updates]


def test_an_approved_signal_opens_with_sl_tp_and_magic_and_syncs_the_closed_deal() -> None:
    fake = connected()
    fake.commission_per_lot = 3.5
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store) as r:
        updates = r.engine.execute(record, NOW)
        assert states(updates) == [
            SignalState.APPROVED,
            SignalState.SENT,
            SignalState.FILLED,
            SignalState.MANAGED,
        ], [u.reason for u in updates]
        [position] = fake.positions
        assert position.magic == MAGIC and position.sl == 1.08252 and position.tp == 1.08552
        assert position.comment.startswith("tw-") and position.volume == 0.5
        assert fake.trading_calls == ["order_check", "order_send"]
        fake.set_bid("EURUSD.m", 1.08250)  # the stop loss is hit on the server
        closed = r.engine.cycle(NOW + 60, {record.id: record})
        assert states(closed) == [SignalState.CLOSED]
        assert "stop loss" in closed[0].reason and "-1." in closed[0].reason
        [row] = store.db.query("SELECT * FROM trades")
        assert row["mode"] == "live" and row["source"] == "bot"
        assert row["signal_id"] == record.id and row["exit_reason"] == "stop loss"
        assert row["commission"] == -3.5 and row["net_profit"] == -53.5
        assert row["sl_initial"] == 1.08252 and row["requested_price"] == 1.08352
        requests = store.db.query("SELECT action, retcode FROM mt5_requests ORDER BY attempt")
        assert {(item["action"], item["retcode"]) for item in requests} == {
            ("order_check", 0),
            ("order_send", 10009),
        }
        events = [item["type"] for item in store.db.query("SELECT type FROM trade_events")]
        assert sorted(events) == ["close", "open"]


def test_a_requote_is_retried_with_a_fresh_price_and_invalid_stops_never_are() -> None:
    fake = connected()
    fake.send_script = [10004]
    with temporary_store() as store, rig(fake, store) as r:
        updates = r.engine.execute(eurusd_record(), NOW)
        assert states(updates)[-1] is SignalState.MANAGED
        assert fake.trading_calls == ["order_check", "order_send", "order_send"]
        fake.calls.clear()
        fake.send_script = [10016]
        other = eurusd_record(
            id="other-signal",
            symbol="GBPUSD.m",
            entry=1.27019,
            sl=1.26919,
            tp=1.27219,
        )
        failed = r.engine.execute(other, NOW)
        assert states(failed) == [SignalState.APPROVED, SignalState.SENT, SignalState.FAILED]
        assert "10016 INVALID_STOPS" in failed[-1].reason
        assert fake.trading_calls == ["order_check", "order_send"]


def test_a_lost_reply_is_found_by_its_comment_instead_of_sending_twice() -> None:
    fake = connected()
    fake.lost_replies = 1
    with temporary_store() as store, rig(fake, store) as r:
        updates = r.engine.execute(eurusd_record(), NOW)
        assert states(updates)[-1] is SignalState.MANAGED
        assert len(fake.positions) == 1
        assert fake.trading_calls == ["order_check", "order_send"]


def test_the_re_check_expires_a_signal_when_the_price_ran_away() -> None:
    fake = connected()
    fake.set_bid("EURUSD.m", 1.08400)  # 0.48 R above the entry
    with temporary_store() as store, rig(fake, store) as r:
        updates = r.engine.execute(eurusd_record(), NOW)
        assert states(updates) == [SignalState.EXPIRED]
        assert "entry tolerance" in updates[0].reason
        assert fake.trading_calls == [] and fake.positions == []


def test_analysis_only_mode_never_sends() -> None:
    fake = connected()
    with temporary_store() as store, rig(fake, store, OperatingMode.ANALYSIS_ONLY) as r:
        assert r.engine.approval_block() == "Analysis-only mode places no orders"
        updates = r.engine.execute(eurusd_record(), NOW)
        assert states(updates) == [SignalState.EXPIRED]
        assert fake.trading_calls == []


def test_paper_mode_fills_on_live_prices_and_never_calls_mt5_trading() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        updates = r.engine.execute(record, NOW)
        assert states(updates)[-1] is SignalState.MANAGED
        [view] = r.engine.cycle(NOW + 1, {record.id: record}) or r.engine.snapshot.positions
        assert view.mode == "paper" and view.volume == 0.5
        assert abs(view.entry - 1.08353) < 1e-9  # the ask plus 1 point of paper slippage
        fake.set_bid("EURUSD.m", 1.08560)  # through the take profit
        closed = r.engine.cycle(NOW + 120, {record.id: record})
        assert states(closed) == [SignalState.CLOSED] and "take profit" in closed[0].reason
        [row] = store.db.query("SELECT * FROM trades")
        assert row["mode"] == "paper" and row["exit_reason"] == "take profit"
        assert round(row["net_profit"], 2) == 99.5
        assert fake.trading_calls == [] and fake.positions == []
        assert round(r.paper.balance, 2) == 10_099.5


def test_break_even_moves_the_stop_and_mfe_is_tracked() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store) as r:
        rules = {"trend_pullback": ManagementSettings(break_even_at_r=1.0)}
        r.config.config = r.config.config.model_copy(update={"management": rules})
        r.engine.execute(record, NOW)
        fake.set_bid("EURUSD.m", 1.08460)  # +1.08 R at the bid
        r.engine.cycle(NOW + 60, {record.id: record})
        [position] = fake.positions
        assert position.sl == 1.08352
        [view] = r.engine.snapshot.positions
        assert view.best_r > 1.0 and view.sl == 1.08352
        events = [item["type"] for item in store.db.query("SELECT type FROM trade_events")]
        assert "modify_sl" in events


def test_the_kill_switch_closes_bot_positions_cancels_orders_and_spares_manual_trades() -> None:
    fake = connected()
    fake.positions.append(make_position(5, "GBPUSD.m", price_open=1.27012, magic=0))
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store) as r:
        r.engine.execute(record, NOW)
        breakout = eurusd_record(
            id="breakout",
            symbol="XAUUSD.m",
            order_type=OrderType.STOP,
            entry=2390.0,
            sl=2380.0,
            tp=2410.0,
            digits=2,
            strategy="london_breakout",
        )
        placed = r.engine.execute(breakout, NOW)
        assert states(placed) == [SignalState.APPROVED, SignalState.SENT]
        assert len(fake.pending_orders) == 1
        r.engine.request_kill("test")
        updates = r.engine.cycle(NOW + 5, {record.id: record, breakout.id: breakout})
        assert [p.ticket for p in fake.positions] == [5]  # the manual trade is untouched
        assert fake.pending_orders == []
        assert sorted(states(updates)) == [SignalState.CLOSED, SignalState.FAILED]
        assert "kill switch" in next(u.reason for u in updates if u.signal_id == "breakout")
        r.risk.refresh(NOW + 6, force=True)  # the risk stop applies on its next refresh
        usage = r.risk.snapshot.usage
        assert usage is not None and usage.halted == "manual"
        assert usage.halted_reason == "kill switch: test"
        assert r.engine.approval_block() == "" and r.engine.snapshot.stopped == "test"
        again = r.engine.execute(eurusd_record(id="after-kill"), NOW + 20)
        assert states(again) == [SignalState.EXPIRED] and "stopped" in again[0].reason


def test_a_filled_breakout_cancels_its_other_side() -> None:
    fake = connected()
    group = {"oco_group": "EURUSD:london_breakout:1"}
    long = eurusd_record(
        id="long",
        order_type=OrderType.STOP,
        entry=1.08500,
        sl=1.08300,
        tp=1.08900,
        strategy="london_breakout",
        features=group,
    )
    short = eurusd_record(
        id="short",
        order_type=OrderType.STOP,
        direction=Direction.SHORT,
        entry=1.08200,
        sl=1.08400,
        tp=1.07800,
        strategy="london_breakout",
        features=group,
    )
    signals = {long.id: long, short.id: short}
    with temporary_store() as store, rig(fake, store) as r:
        r.engine.execute(long, NOW)
        r.engine.execute(short, NOW)
        assert len(fake.pending_orders) == 2
        fake.set_bid("EURUSD.m", 1.08510)
        updates = r.engine.cycle(NOW + 60, signals)
        assert [(u.signal_id, u.state) for u in updates] == [
            ("long", SignalState.FILLED),
            ("long", SignalState.MANAGED),
            ("short", SignalState.FAILED),
        ]
        assert fake.pending_orders == [] and len(fake.positions) == 1


def test_crash_recovery_resumes_managing_and_adopts_unknown_bot_positions() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store:
        with rig(fake, store) as first:
            first.engine.execute(record, NOW)
        fake.initialize()  # the app restarts: the gateway connects again
        fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
        # A bot position the database does not know (opened before a crash).
        fake.positions.append(make_position(77, "GBPUSD.m", price_open=1.27, sl=1.26, magic=MAGIC))
        with rig(fake, store) as second:
            updates = second.engine.cycle(NOW + 30, {record.id: record})
            assert updates == []  # the known trade is managed again, nothing re-sent
            tickets = sorted(view.ticket for view in second.engine.snapshot.positions)
            assert tickets == sorted([77, fake.positions[0].ticket])
            assert any("Adopted bot position 77" in text for _, text in second.logs)
            assert any("Resumed managing 1" in text for _, text in second.logs)
            fake.set_bid("EURUSD.m", 1.08250)
            closed = second.engine.cycle(NOW + 90, {record.id: record})
            assert states(closed) == [SignalState.CLOSED]
            assert fake.trading_calls == ["order_check", "order_send"]


def test_a_failed_positions_read_never_closes_a_trade() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store) as r:
        r.engine.execute(record, NOW)
        fake.terminal_running = False
        assert r.engine.cycle(NOW + 30, {record.id: record}) == []
        fake.terminal_running = True
        assert r.engine.cycle(NOW + 60, {record.id: record}) == []
        assert len(r.engine.snapshot.positions) == 1


def test_xauusd_with_ioc_only_is_sent_with_ioc() -> None:
    fake = connected()
    gold = next(symbol for symbol in fake.symbols if symbol.name == "XAUUSD.m")
    gold.filling_flags = 2
    record = eurusd_record(
        id="gold",
        symbol="XAUUSD.m",
        entry=2385.67,
        sl=2375.67,
        tp=2405.67,
        digits=2,
    )
    with temporary_store() as store, rig(fake, store) as r:
        updates = r.engine.execute(record, NOW)
        assert states(updates)[-1] is SignalState.MANAGED, [u.reason for u in updates]
        [sent] = store.db.query("SELECT request_json FROM mt5_requests WHERE action='order_send'")
        assert '"type_filling":1' in sent["request_json"]
        assert api.ORDER_FILLING_IOC == 1
