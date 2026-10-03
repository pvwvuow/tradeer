"""The signal pipeline with the execution engine (spec C5): approve, re-check, send, close;
every state change is in the signal's history and the decision trace."""

from app.domain.signals import SignalState
from app.engine.filters import FilterSettings
from app.engine.signal_pipeline import NO_EXECUTION_NOTE, SignalPipeline
from app.storage.signal_store import SignalRepository
from tests.unit.execution_helpers import NOW, Rig, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.signal_helpers import pipeline
from tests.unit.storage_helpers import temporary_store


def with_engine(r: Rig, store: SignalRepository | None = None) -> SignalPipeline:
    return SignalPipeline(
        lambda: [],
        FilterSettings,
        store=store,
        executor=r.engine,
        utc_now=lambda: r.clock[0],
    )


def test_approve_sends_and_the_close_comes_back_as_closed() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as db, rig(fake, db) as r:
        signals = with_engine(r, SignalRepository(db))
        signals._remember(record)  # a pending signal, as `on_analysis` leaves it
        signals.on_cycle(NOW)
        assert signals.snapshot.approval_block == ""
        signals.approve(record.id)
        signals.on_cycle(NOW + 2)
        found = {item.id: item for item in signals.snapshot.signals}[record.id]
        assert found.signal.state is SignalState.MANAGED
        names = [change.new for change in found.signal.history]
        assert names[-4:] == [
            SignalState.APPROVED,
            SignalState.SENT,
            SignalState.FILLED,
            SignalState.MANAGED,
        ]
        steps = [step.name for step in found.trace.steps if step.stage == "execution"]
        assert {"entry tolerance", "spread", "stops", "risk at approval"} <= set(steps)
        fake.set_bid("EURUSD.m", 1.08250)
        signals.on_cycle(NOW + 60)
        closed = {item.id: item for item in signals.snapshot.signals}[record.id]
        assert closed.signal.state is SignalState.CLOSED
        saved = SignalRepository(db).recent()
        assert saved[0].signal.state is SignalState.CLOSED


def test_a_filled_breakout_expires_its_unapproved_twin() -> None:
    fake = connected()
    group = {"oco_group": "EURUSD:london_breakout:1"}
    long = eurusd_record(id="long", entry=1.08352, features=group, strategy="london_breakout")
    short = eurusd_record(id="short", features=group, strategy="london_breakout")
    with temporary_store() as db, rig(fake, db) as r:
        signals = with_engine(r)
        signals._remember(long)
        signals._remember(short)
        signals.approve("long")
        signals.on_cycle(NOW)
        states = {item.id: item.signal.state for item in signals.snapshot.signals}
        assert states == {"long": SignalState.MANAGED, "short": SignalState.EXPIRED}


def test_without_an_engine_approval_is_refused_honestly() -> None:
    signals = pipeline()
    record = eurusd_record()
    signals._remember(record)
    signals.approve(record.id)
    signals.on_cycle(NOW)
    found = signals.snapshot.signals[0]
    assert found.signal.state is SignalState.EXPIRED
    assert signals.snapshot.approval_block == NO_EXECUTION_NOTE
