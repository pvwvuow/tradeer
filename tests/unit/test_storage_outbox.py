import pytest

from app.storage import outbox
from app.storage.ids import new_id
from tests.unit.storage_helpers import temporary_store


def audit_row(action: str, row_id: str | None = None) -> dict[str, object]:
    return {"id": row_id or new_id(), "time": "2026-10-01T10:00:00.000Z", "action": action}


def test_a_write_and_its_outbox_entry_are_saved_together_or_not_at_all() -> None:
    with temporary_store() as store:
        store.upsert("audit_log", audit_row("saved"))
        with pytest.raises(RuntimeError), store.db.transaction() as connection:
            store.upsert_in(connection, "audit_log", audit_row("rolled back"))
            raise RuntimeError("crash before the commit")
        assert store.count("audit_log") == 1
        assert store.outbox_counts().pending == 1


def test_a_second_change_replaces_the_waiting_payload() -> None:
    with temporary_store() as store:
        row_id = new_id()
        store.upsert("audit_log", audit_row("first", row_id))
        store.upsert("audit_log", audit_row("second", row_id))
        items = outbox.pending(store.db.connection(), 10)
        assert [(item.row_id, item.version) for item in items] == [(row_id, 2)]
        assert items[0].payload["action"] == "second"


def test_an_unchanged_row_is_not_written_or_uploaded_again() -> None:
    with temporary_store() as store:
        row = audit_row("same")
        assert store.upsert("audit_log", row)
        connection = store.db.connection()
        with store.db.transaction() as tx:
            outbox.mark_sent(tx, outbox.pending(connection, 10))
        assert not store.upsert("audit_log", row)
        assert store.outbox_counts().pending == 0


def test_a_change_during_an_upload_is_not_lost() -> None:
    with temporary_store() as store:
        row_id = new_id()
        store.upsert("audit_log", audit_row("old", row_id))
        uploading = outbox.pending(store.db.connection(), 10)
        store.upsert("audit_log", audit_row("new", row_id))
        with store.db.transaction() as connection:
            assert outbox.mark_sent(connection, uploading) == 0
        waiting = outbox.pending(store.db.connection(), 10)
        assert waiting[0].payload["action"] == "new"


def test_refused_rows_are_parked_and_can_be_retried() -> None:
    with temporary_store() as store:
        store.upsert("audit_log", audit_row("bad"))
        item = outbox.pending(store.db.connection(), 10)[0]
        with store.db.transaction() as connection:
            outbox.mark_failed(connection, item, "HTTP 400: invalid")
        assert store.outbox_counts() == outbox.OutboxCounts(pending=0, failed=1)
        assert outbox.failures(store.db.connection())[0][2] == "HTTP 400: invalid"
        assert store.retry_failed() == 1
        assert store.outbox_counts() == outbox.OutboxCounts(pending=1, failed=0)


def test_payloads_carry_every_column_of_the_table() -> None:
    with temporary_store() as store:
        store.upsert("audit_log", audit_row("full"))
        payload = outbox.pending(store.db.connection(), 1)[0].payload
        assert set(payload) == {
            "id",
            "account_id",
            "session_id",
            "time",
            "source",
            "action",
            "before_json",
            "after_json",
            "created_at",
            "updated_at",
        }


def test_only_synced_tables_go_to_the_outbox() -> None:
    with temporary_store() as store, pytest.raises(ValueError):
        outbox.enqueue(store.db.connection(), "mt5_deals", {"id": "x"})
