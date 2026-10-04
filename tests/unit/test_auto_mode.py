"""Auto mode in the execution engine (spec B3, C9): pending signals are sent without a click,
once, and only when the Go-Live gate allows it; the kill switch stops Auto too."""

from pathlib import Path

from app.core.execution_settings import (
    ExecutionConfig,
    load_execution_config,
    save_execution_config,
)
from app.domain.modes import OperatingMode
from app.domain.signals import Signal, SignalState
from app.engine.execution import SignalUpdate
from tests.unit.execution_helpers import NOW, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store

BLOCK = "trend_pullback has no Go-Live approval on this REAL account"


def states(updates: list[SignalUpdate]) -> list[SignalState]:
    return [update.state for update in updates]


def test_auto_without_the_gate_sends_nothing() -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store, OperatingMode.AUTO) as r:
        assert "Go-Live gate" in r.engine.approval_block()
        assert r.engine.cycle(NOW, {record.id: record}) == []
        assert states(r.engine.execute(record, NOW)) == [SignalState.EXPIRED]
        assert fake.trading_calls == []


def test_auto_sends_a_pending_signal_once_when_the_gate_allows_it() -> None:
    fake = connected()
    record = eurusd_record()
    asked: list[str] = []

    def gate(signal: Signal) -> str:
        asked.append(signal.id)
        return ""

    with temporary_store() as store, rig(fake, store, OperatingMode.AUTO, auto_gate=gate) as r:
        assert r.engine.approval_block() == ""
        updates = r.engine.cycle(NOW, {record.id: record})
        assert states(updates) == [
            SignalState.APPROVED,
            SignalState.SENT,
            SignalState.FILLED,
            SignalState.MANAGED,
        ], [u.reason for u in updates]
        assert f"({OperatingMode.AUTO.label})" in updates[1].reason and asked == [record.id]
        assert len(fake.positions) == 1
        assert r.engine.cycle(NOW + 1, {record.id: record}) == []
        assert fake.trading_calls == ["order_check", "order_send"]
        assert any("Auto: sending" in text for _, text in r.logs)


def test_a_blocked_strategy_expires_with_the_reason_and_is_not_retried() -> None:
    fake = connected()
    record = eurusd_record()
    with (
        temporary_store() as store,
        rig(fake, store, OperatingMode.AUTO, auto_gate=lambda signal: BLOCK) as r,
    ):
        updates = r.engine.cycle(NOW, {record.id: record})
        assert states(updates) == [SignalState.EXPIRED] and BLOCK in updates[0].reason
        assert r.engine.cycle(NOW + 1, {record.id: record}) == []
        assert fake.trading_calls == []


def test_the_kill_switch_and_other_modes_never_auto_send() -> None:
    fake = connected()
    record = eurusd_record()
    with (
        temporary_store() as store,
        rig(fake, store, OperatingMode.AUTO, auto_gate=lambda signal: "") as r,
    ):
        r.engine.request_kill("test")
        r.engine.cycle(NOW)
        assert r.engine.cycle(NOW + 1, {record.id: record}) == []
        r.config.mode(OperatingMode.SEMI_AUTO)
        r.engine.resume()
        assert r.engine.cycle(NOW + 2, {record.id: record}) == []
        assert fake.trading_calls == []


def test_auto_is_saved_and_loaded(tmp_path: Path) -> None:
    save_execution_config(tmp_path, ExecutionConfig(mode=OperatingMode.AUTO))
    config, note = load_execution_config(tmp_path)
    assert config.mode is OperatingMode.AUTO and note == ""
