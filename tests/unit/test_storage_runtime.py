import tempfile
from datetime import date
from pathlib import Path

import pytest

from app.core.credentials import MemoryStore
from app.storage.runtime import StorageError, open_storage
from app.storage.sqlite_db import database_path
from tests.fakes.fake_supabase import FakeSupabase


def test_opening_storage_migrates_backs_up_and_records_the_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        runtime = open_storage(
            "demo",
            folder,
            "s-1",
            credentials=MemoryStore(),
            client_factory=lambda url, key: FakeSupabase(),
        )
        try:
            runtime.start_session(app_version="0.1.0", mode="paper", settings={"risk": 0.5})
            assert database_path(folder).exists()
            assert len(list((folder / "backups").glob("workstation-*.db"))) == 1
            runtime.maintenance(date(2030, 1, 2))
            assert (folder / "backups" / "workstation-20300102.db").exists()
            assert runtime.engine.run_once().pending == 1
        finally:
            runtime.close()
        assert runtime.db.closed


def test_a_damaged_database_stops_the_start_with_a_clear_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = database_path(folder)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"this is not a database" * 100)
        with pytest.raises(StorageError) as error:
            open_storage(
                "demo",
                folder,
                "s-1",
                credentials=MemoryStore(),
                client_factory=lambda url, key: FakeSupabase(),
            )
        assert "workstation.db" in str(error.value)
