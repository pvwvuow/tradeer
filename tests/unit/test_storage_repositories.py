from types import SimpleNamespace

from app.mt5 import api
from app.mt5.models import AccountSnapshot
from app.storage import outbox
from app.storage.ids import account_id, stable_id, trade_id
from app.storage.log_store import LogStore, keep_entry
from tests.unit.storage_helpers import temporary_store


def snapshot(**changes: object) -> AccountSnapshot:
    values: dict[str, object] = {
        "login": 51234567,
        "name": "Test Trader",
        "server": "Broker-Demo",
        "company": "Broker Ltd",
        "currency": "USD",
        "balance": 10_000.0,
        "equity": 10_120.0,
        "margin": 200.0,
        "margin_free": 9_920.0,
        "margin_level": 5060.0,
        "leverage": 100,
        "trade_mode": api.ACCOUNT_TRADE_MODE_DEMO,
        "margin_mode": api.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
        "trade_allowed": True,
    }
    values.update(changes)
    return AccountSnapshot.from_mt5(SimpleNamespace(**values))


def test_ids_are_stable_for_the_same_account_and_position() -> None:
    assert account_id("Broker-Demo", 1) == account_id(" broker-demo ", 1)
    assert account_id("Broker-Demo", 1) != account_id("Broker-Demo", 2)
    assert trade_id("a", 5) == trade_id("a", 5) != trade_id("b", 5)
    assert stable_id("x", 1) == stable_id("x", "1")


def test_the_account_row_is_saved_once_and_updated_on_change() -> None:
    with temporary_store() as store:
        key = store.upsert_account(snapshot())
        assert key == account_id("Broker-Demo", 51234567)
        store.upsert_account(snapshot())
        assert store.outbox_counts().pending == 1
        row = store.get("accounts", key)
        assert row is not None
        assert (row["broker"], row["type"], row["margin_mode"]) == ("Broker Ltd", "DEMO", "hedging")
        store.upsert_account(snapshot(leverage=500))
        assert store.get("accounts", key)["leverage"] == 500  # type: ignore[index]


def test_a_session_is_started_linked_and_ended() -> None:
    with temporary_store() as store:
        store.start_session(
            "s-1",
            app_version="0.1.0",
            profile="demo",
            mode="paper",
            settings={"a": 1},
        )
        store.set_session_account("s-1", "acc")
        store.end_session("s-1")
        row = store.get("sessions", "s-1")
        assert row is not None
        assert (row["profile"], row["mode"], row["account_id"]) == ("demo", "paper", "acc")
        assert row["started_at"] and row["ended_at"]
        payload = outbox.pending(store.db.connection(), 10)[0].payload
        assert payload["settings_json"] == {"a": 1}


def test_account_snapshots_hold_margin_values() -> None:
    with temporary_store() as store:
        row_id = store.record_snapshot("acc", snapshot(), positions=2)
        row = store.get("account_snapshots", row_id)
        assert row is not None
        assert (row["equity"], row["margin"], row["margin_level"], row["open_positions"]) == (
            10_120.0,
            200.0,
            5060.0,
            2,
        )


def test_log_and_audit_entries_become_rows() -> None:
    with temporary_store() as store:
        entry = {
            "time": "2026-10-01T10:00:00.000Z",
            "level": "ERROR",
            "category": "mt5",
            "message": "MT5 login failed",
            "module": "app.mt5.checklist",
            "function": "run_checklist",
            "line": 42,
            "session_id": "s-1",
            "symbol": "EURUSD",
            "context": {"retcode": 10004},
        }
        log_id = store.record_log(entry, "acc")
        row = store.get("app_logs", log_id)
        assert row is not None
        assert (row["level"], row["line"], row["symbol"], row["account_id"]) == (
            "ERROR",
            42,
            "EURUSD",
            "acc",
        )
        audit = {
            "time": "2026-10-01T10:01:00.000Z",
            "category": "audit",
            "message": "Audit: log_level",
            "context": {"action": "log_level", "before": "INFO", "after": "DEBUG"},
        }
        audit_id = store.record_audit(audit)
        saved = store.get("audit_log", audit_id)
        assert saved is not None
        assert (saved["action"], saved["source"]) == ("log_level", "user")
        payloads = {item.row_id: item.payload for item in outbox.pending(store.db.connection(), 10)}
        assert payloads[audit_id]["after_json"] == "DEBUG"
        assert payloads[log_id]["context_json"] == {"retcode": 10004}


def test_only_audit_entries_and_warnings_or_worse_are_stored() -> None:
    assert keep_entry(30, "app")
    assert keep_entry(50, "mt5")
    assert keep_entry(20, "audit")
    assert not keep_entry(20, "app")
    assert not keep_entry(10, "sync")


def test_the_log_store_counts_failures_and_never_raises() -> None:
    with temporary_store() as store:
        sink = LogStore(store, account=lambda: "acc")
        sink({"time": "2026-10-01T10:00:00.000Z", "level": "WARNING", "category": "app"})
        sink({"category": "audit", "context": {"action": "x", "before": object()}})
        store.db.close()
        sink({"time": "t", "level": "ERROR", "category": "app"})
        assert (sink.written, sink.failures) == (2, 1)
