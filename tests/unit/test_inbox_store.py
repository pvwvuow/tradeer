"""The Telegram signals tab is kept in the local database (0.49.1): entries are added, get
their final state, are deleted when cleared, and the oldest go after the limit."""

from __future__ import annotations

from app.storage.inbox_store import KEEP_ROWS, InboxStore
from tests.unit.storage_helpers import temporary_store


def test_entries_are_kept_updated_and_deleted() -> None:
    with temporary_store() as store:
        inbox = InboxStore(store)
        first = inbox.add("note", "Gold Room", 1000.0, "waiting for the rest")
        second = inbox.add("order", "Gold Room", 2000.0, "XAUUSD buy", "waiting")
        assert inbox.set_state(second, "sent") and not inbox.set_state(9999, "sent")
        rows = inbox.recent(10)
        assert [(r.id, r.kind, r.channel, r.at, r.text, r.state) for r in rows] == [
            (first, "note", "Gold Room", 1000.0, "waiting for the rest", ""),
            (second, "order", "Gold Room", 2000.0, "XAUUSD buy", "sent"),
        ]
        assert [r.id for r in inbox.recent(1)] == [second]  # the newest one
        assert inbox.delete([first, 9999]) == 1
        assert [r.id for r in InboxStore(store).recent(10)] == [second]  # also after a restart


def test_the_oldest_rows_go_after_the_limit() -> None:
    with temporary_store() as store:
        inbox = InboxStore(store)
        for number in range(KEEP_ROWS + 5):
            inbox.add("note", "Gold Room", float(number), str(number))
        rows = inbox.recent(KEEP_ROWS + 10)
        assert len(rows) == KEEP_ROWS
        assert rows[0].text == "5" and rows[-1].text == str(KEEP_ROWS + 4)
