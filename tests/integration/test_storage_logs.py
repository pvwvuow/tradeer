"""WARNING+ entries and audit entries reach the database through the real loguru pipeline."""

import tempfile
from pathlib import Path

from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.files import LogFilePolicy, LogFileWriter
from app.observability.levels import LevelRegistry
from app.observability.logger import LogPipeline, audit, get_logger
from app.observability.masking import SecretMasker
from app.storage.log_store import LogStore, keep_entry
from app.storage.migrate import migrate
from app.storage.repositories import Store
from app.storage.sqlite_db import Database, database_path

SECRET = "Hunter2-Secret-998"


def test_warnings_and_audit_entries_are_stored_masked_and_info_stays_in_the_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = Database(database_path(root))
        migrate(db)
        store = Store(db)
        masker = SecretMasker()
        masker.register(SECRET)
        writer = LogFileWriter(root / "logs", LogFilePolicy(retention_days=100_000))
        pipeline = LogPipeline(writer, RecentLogBuffer(), LevelRegistry(), masker)
        pipeline.start()
        sink = LogStore(store, account=lambda: "acc-1")
        pipeline.add_entry_sink(sink, keep_entry)
        try:
            log = get_logger(LogCategory.SYNC)
            log.info("Only in the log files")
            log.warning("Upload failed for {}", SECRET)
            get_logger(LogCategory.MT5).bind(symbol="EURUSD").error("Order check failed")
            audit("cloud_sign_in", before=None, after={"email": "trader@example.com"})
        finally:
            pipeline.flush()
            pipeline.stop()
        columns = "level, category, message, symbol, account_id"
        logs = db.query(f"SELECT {columns} FROM app_logs ORDER BY rowid")
        audits = db.query("SELECT action, source, after_json FROM audit_log")
        waiting = store.outbox_counts().pending
        db.close()
    levels = [(row["level"], row["category"]) for row in logs]
    assert levels == [("WARNING", "sync"), ("ERROR", "mt5")]
    assert SECRET not in logs[0]["message"]
    assert (logs[1]["symbol"], logs[1]["account_id"]) == ("EURUSD", "acc-1")
    assert [(row["action"], row["source"]) for row in audits] == [("cloud_sign_in", "user")]
    assert waiting == 3
    assert sink.failures == 0
