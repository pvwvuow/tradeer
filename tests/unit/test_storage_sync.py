"""Spec G3 phase 4: offline writes sync later without duplicates."""

import threading
import time

from app.storage import outbox
from app.storage.ids import new_id
from app.storage.remote import AuthSession
from app.storage.repositories import Store
from app.storage.sync import SyncBackoff, SyncEngine, SyncState, SyncStatus, SyncWorker
from tests.fakes.fake_supabase import EMAIL, USER_ID, FakeSupabase
from tests.unit.storage_helpers import temporary_store


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def connected(store: Store, cloud: FakeSupabase, **options: object) -> tuple[SyncEngine, Clock]:
    clock = Clock()
    engine = SyncEngine(store.db, clock=clock, **options)  # type: ignore[arg-type]
    engine.configure(cloud, cloud.session())
    return engine, clock


def write_rows(store: Store, count: int) -> list[str]:
    ids = [new_id() for _ in range(count)]
    for index, row_id in enumerate(ids):
        row = {"id": row_id, "time": "2026-10-01T10:00:00.000Z", "action": f"action {index}"}
        store.upsert("audit_log", row)
    return ids


def run_until_idle(engine: SyncEngine, clock: Clock, steps: int = 50) -> SyncStatus:
    status = engine.run_once()
    for _ in range(steps):
        if status.state is not SyncState.SYNCING:
            break
        clock.now += 1
        status = engine.run_once()
    return status


def test_offline_writes_are_uploaded_later_exactly_once() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase(online=False)
        engine, clock = connected(store, cloud, batch_size=7)
        ids = write_rows(store, 30)
        store.upsert("accounts", {"id": new_id(), "server": "Demo", "login": 1})
        status = engine.run_once()
        assert status.state is SyncState.OFFLINE
        assert status.pending == 31
        assert status.retry_in_seconds == 2.0
        clock.now += 1
        assert engine.run_once().retry_in_seconds == 1.0
        clock.now += 5
        assert engine.run_once().retry_in_seconds == 4.0
        cloud.online = True
        clock.now += 10
        status = run_until_idle(engine, clock)
        assert status.state is SyncState.UP_TO_DATE
        assert status.pending == 0
        assert sorted(cloud.rows["audit_log"]) == sorted(ids)
        assert len(cloud.received) == 31
        assert all(row["user_id"] == USER_ID for row in cloud.rows["audit_log"].values())


def test_a_lost_answer_is_retried_without_a_duplicate() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase(lose_answers=1)
        engine, clock = connected(store, cloud)
        ids = write_rows(store, 5)
        assert engine.run_once().state is SyncState.OFFLINE
        assert store.outbox_counts().pending == 5
        clock.now += 60
        assert run_until_idle(engine, clock).state is SyncState.UP_TO_DATE
        assert sorted(cloud.rows["audit_log"]) == sorted(ids)
        assert len(cloud.received) == 10


def test_a_refused_row_is_parked_and_the_others_are_uploaded() -> None:
    with temporary_store() as store:
        bad = new_id()
        cloud = FakeSupabase(refuse=lambda table, row: row["id"] == bad)
        engine, clock = connected(store, cloud)
        ids = write_rows(store, 9)
        store.upsert("audit_log", {"id": bad, "action": "broken"})
        status = run_until_idle(engine, clock)
        assert status.state is SyncState.UP_TO_DATE
        assert status.failed == 1
        assert sorted(cloud.rows["audit_log"]) == sorted(ids)
        assert outbox.failures(store.db.connection())[0][1] == bad
        assert "refused" in status.status_bar_text()


def test_rows_changed_after_an_upload_are_uploaded_again() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase()
        engine, clock = connected(store, cloud)
        row_id = new_id()
        store.upsert("audit_log", {"id": row_id, "action": "first"})
        run_until_idle(engine, clock)
        store.upsert("audit_log", {"id": row_id, "action": "second"})
        run_until_idle(engine, clock)
        assert cloud.count("audit_log") == 1
        assert cloud.rows["audit_log"][row_id]["action"] == "second"


def test_an_expired_sign_in_is_refreshed_once_and_reported() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase()
        engine, clock = connected(store, cloud)
        saved: list[AuthSession | None] = []
        session = cloud.session()
        cloud.expired.add(session.access_token)
        engine.configure(cloud, session, saved.append)
        write_rows(store, 3)
        assert run_until_idle(engine, clock).state is SyncState.UP_TO_DATE
        assert cloud.refreshes == 1
        assert saved and saved[0] is not None and saved[0].email == EMAIL
        assert cloud.count("audit_log") == 3


def test_a_saved_sign_in_without_an_access_token_is_refreshed_first() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase()
        engine, clock = connected(store, cloud)
        engine.configure(cloud, AuthSession("", EMAIL, access_token="", refresh_token="saved"))
        write_rows(store, 2)
        status = run_until_idle(engine, clock)
        assert status.state is SyncState.UP_TO_DATE
        assert status.email == EMAIL
        assert cloud.refreshes == 1


def test_a_revoked_sign_in_signs_out_and_keeps_the_rows() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase(revoked=True)
        engine, _ = connected(store, cloud)
        saved: list[AuthSession | None] = []
        engine.configure(cloud, AuthSession("", EMAIL, "", "old"), saved.append)
        write_rows(store, 4)
        status = engine.run_once()
        assert status.state is SyncState.SIGNED_OUT
        assert status.pending == 4
        assert saved == [None]
        assert engine.run_once().state is SyncState.SIGNED_OUT


def test_a_paused_project_waits_at_least_ten_minutes() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase(paused=True)
        engine, _ = connected(store, cloud)
        write_rows(store, 1)
        status = engine.run_once()
        assert status.state is SyncState.PAUSED
        assert status.retry_in_seconds == 600.0
        assert "paused" in status.message


def test_missing_cloud_tables_ask_for_the_setup_and_keep_the_rows() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase(tables_missing=True)
        engine, _ = connected(store, cloud)
        write_rows(store, 2)
        status = engine.run_once()
        assert status.state is SyncState.SETUP_NEEDED
        assert status.pending == 2
        assert status.failed == 0


def test_the_backoff_grows_to_five_minutes() -> None:
    backoff = SyncBackoff()
    assert [backoff.delay(n) for n in (1, 2, 3, 9, 20)] == [2.0, 4.0, 8.0, 300.0, 300.0]


def test_without_cloud_settings_sync_is_off_and_rows_wait() -> None:
    with temporary_store() as store:
        engine = SyncEngine(store.db)
        write_rows(store, 2)
        status = engine.run_once()
        assert status.state is SyncState.DISABLED
        assert status.pending == 2
        assert status.status_bar_text() == "Cloud: off \u00b7 2 waiting"
        engine.configure(FakeSupabase(), None)
        assert engine.run_once().state is SyncState.SIGNED_OUT


def test_the_worker_uploads_in_the_background_and_reports_changes() -> None:
    with temporary_store() as store:
        cloud = FakeSupabase()
        engine = SyncEngine(store.db)
        engine.configure(cloud, cloud.session())
        seen: list[SyncStatus] = []
        done = threading.Event()

        def listen(status: SyncStatus) -> None:
            seen.append(status)
            if status.state is SyncState.UP_TO_DATE and cloud.count("audit_log") == 3:
                done.set()

        worker = SyncWorker(engine, interval_seconds=0.05)
        worker.add_listener(listen)
        write_rows(store, 3)
        worker.start()
        try:
            worker.nudge()
            assert done.wait(5)
        finally:
            worker.stop()
        assert seen[-1].state is SyncState.UP_TO_DATE
        time.sleep(0.01)
