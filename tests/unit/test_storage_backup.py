import sqlite3
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.storage.backup import daily_backup, prune, upgrade_backup
from app.storage.cleanup import cleanup
from app.storage.ids import new_id
from app.storage.sqlite_db import iso_utc
from tests.unit.storage_helpers import temporary_store


def test_one_backup_per_day_and_the_newest_seven_are_kept() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        store.upsert("audit_log", {"id": new_id(), "action": "kept"})
        first = daily_backup(store.db, folder, date(2026, 9, 1))
        assert first is not None
        assert daily_backup(store.db, folder, date(2026, 9, 1)) is None
        for day in range(2, 12):
            daily_backup(store.db, folder, date(2026, 9, day))
        names = sorted(path.name for path in folder.glob("workstation-*.db"))
        assert names[0] == "workstation-20260905.db"
        assert len(names) == 7
        copy = sqlite3.connect(folder / names[-1])
        try:
            assert copy.execute("SELECT COUNT(*) FROM audit_log").fetchone() == (1,)
        finally:
            copy.close()


def test_upgrade_backups_are_separate_and_limited() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for index in range(5):
            target = upgrade_backup(store.db, folder, 1)
            target.rename(folder / f"workstation-before-schema-1-2026010{index}.db")
        upgrade_backup(store.db, folder, 1)
        assert len(list(folder.glob("workstation-before-schema-*.db"))) == 3
        assert prune(folder, "workstation-????????.db", 7) == []


def test_cleanup_removes_old_uploaded_logs_but_never_trades_or_waiting_rows() -> None:
    with temporary_store() as store:
        now = datetime(2026, 10, 1, tzinfo=UTC)
        old = iso_utc(now - timedelta(days=120))
        uploaded, waiting = new_id(), new_id()
        for row_id in (uploaded, waiting):
            store.upsert("app_logs", {"id": row_id, "time": old, "level": "ERROR"})
        store.upsert("trades", {"id": new_id(), "open_time": old, "symbol": "EURUSD"})
        with store.db.transaction() as connection:
            connection.execute("DELETE FROM outbox WHERE row_id = ?", (uploaded,))
        assert cleanup(store.db, now)["app_logs"] == 1
        assert store.get("app_logs", uploaded) is None
        assert store.get("app_logs", waiting) is not None
        assert store.count("trades") == 1
