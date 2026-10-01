import threading
import uuid

import pytest

from app.observability.context import (
    SESSION_ID,
    current_fields,
    current_trace_id,
    propagate,
    snapshot,
    trace,
)


def test_session_id_is_a_uuid_and_always_in_the_snapshot() -> None:
    assert uuid.UUID(SESSION_ID)
    assert snapshot() == {"session_id": SESSION_ID}


def test_trace_sets_and_restores_the_context() -> None:
    assert current_trace_id() is None
    with trace(symbol="EURUSD") as trace_id:
        assert current_trace_id() == trace_id
        assert snapshot()["symbol"] == "EURUSD"
    assert current_trace_id() is None
    assert current_fields() == {}


def test_nested_trace_continues_the_signal_and_adds_fields() -> None:
    with trace(signal_id="s-1", symbol="XAUUSD") as outer:
        with trace(trade_id="t-9", ticket=123) as inner:
            assert inner == outer
            assert current_fields() == {
                "signal_id": "s-1",
                "symbol": "XAUUSD",
                "trade_id": "t-9",
                "ticket": "123",
            }
        assert "trade_id" not in current_fields()
    with trace("given-id") as explicit:
        assert explicit == "given-id"


def test_unknown_trace_fields_are_rejected() -> None:
    with pytest.raises(ValueError), trace(password="nope"):
        pass


def test_propagate_carries_the_context_into_other_threads() -> None:
    seen: list[dict[str, str]] = []

    def worker() -> None:
        seen.append(snapshot())

    with trace(symbol="GBPUSD") as trace_id:
        carried = propagate(worker)
        plain = threading.Thread(target=worker)
        plain.start()
        plain.join()
        threads = [threading.Thread(target=carried) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert "trace_id" not in seen[0]
    assert seen[1]["trace_id"] == seen[2]["trace_id"] == trace_id
    assert seen[1]["symbol"] == "GBPUSD"
