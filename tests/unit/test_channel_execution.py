"""A channel's orders and follow-ups in the execution engine (docs/SIGNAL_DESK.md 3.4 and
3.6, phases 21d and 21e): a confirmed channel signal goes out with the channel's magic, and a
follow-up moves the stop loss only to a tighter place."""

from tests.unit.execution_helpers import NOW, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store

CHANNEL = "channel:26071001"


def test_a_channel_order_has_its_magic_and_its_stop_moves_only_tighter() -> None:
    fake = connected()
    with temporary_store() as store, rig(fake, store) as setup:
        engine = setup.engine
        record = eurusd_record(strategy=CHANNEL, features={"confirm": "user"})
        updates = engine.execute(record, NOW)
        assert not any("no magic number" in update.reason for update in updates)
        engine.cycle(NOW + 1, {record.id: record})
        (view,) = [p for p in engine.snapshot.positions if p.signal_id == record.id]
        assert view.strategy == CHANNEL and not view.pending
        engine.request_stop(view.mode, view.ticket, 1.07)  # wider than the 1.08252 stop
        engine.cycle(NOW + 2, {record.id: record})
        assert any("is not tighter" in text for text in engine.snapshot.messages)
        engine.request_stop(view.mode, view.ticket, 1.083)  # tighter: done
        engine.cycle(NOW + 3, {record.id: record})
        (moved,) = [p for p in engine.snapshot.positions if p.signal_id == record.id]
        assert moved.sl == 1.083, engine.snapshot.messages
        assert any("new SL (the channel)" in text for text in engine.snapshot.messages)
