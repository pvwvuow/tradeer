import base64
import json
import tempfile
from pathlib import Path

import pytest

from app.core.credentials import MemoryStore
from app.observability.masking import MASKER
from app.storage.cloud import (
    CLOUD_FILE_NAME,
    CloudAccount,
    CloudSetupError,
    check_key,
    check_url,
    load_cloud,
    token_name,
)
from app.storage.remote import RemoteError
from app.storage.sync import SyncEngine, SyncState
from tests.fakes.fake_supabase import EMAIL, PASSWORD, FakeSupabase
from tests.unit.storage_helpers import temporary_store

URL = "https://abcdefgh.supabase.co"


def jwt(role: str) -> str:
    def part(data: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'role': role, 'iss': 'supabase'})}.signature"


def test_the_project_url_must_be_https_or_local() -> None:
    assert check_url(" https://abcdefgh.supabase.co/ ") == URL
    assert check_url("http://localhost:54321") == "http://localhost:54321"
    with pytest.raises(CloudSetupError):
        check_url("abcdefgh.supabase.co")


def test_the_anon_key_is_accepted_and_the_service_key_refused() -> None:
    assert check_key(f" {jwt('anon')} ") == jwt("anon")
    assert check_key("sb_publishable_abc123") == "sb_publishable_abc123"
    for secret in (jwt("service_role"), "sb_secret_abc123", ""):
        with pytest.raises(CloudSetupError):
            check_key(secret)


def account_for(
    folder: Path,
    cloud: FakeSupabase,
    secrets: MemoryStore,
    engine: SyncEngine,
) -> CloudAccount:
    return CloudAccount("demo", folder, engine, secrets, lambda url, key: cloud)


def test_signing_in_saves_the_settings_and_token_but_never_the_password() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        cloud, secrets = FakeSupabase(), MemoryStore()
        engine = SyncEngine(store.db)
        account = account_for(Path(tmp), cloud, secrets, engine)
        session = account.sign_in(URL, jwt("anon"), EMAIL, PASSWORD)
        text = (Path(tmp) / CLOUD_FILE_NAME).read_text(encoding="utf-8")
        assert PASSWORD not in text and session.refresh_token not in text
        assert load_cloud(Path(tmp)).enabled
        assert secrets.get(token_name("demo")) == session.refresh_token
        assert engine.session == session
        assert MASKER.mask(f"token {session.refresh_token}") == "token ***"
        assert engine.run_once().state is SyncState.UP_TO_DATE


def test_a_wrong_password_raises_and_changes_nothing() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        engine = SyncEngine(store.db)
        account = account_for(Path(tmp), FakeSupabase(), MemoryStore(), engine)
        with pytest.raises(RemoteError):
            account.sign_in(URL, jwt("anon"), EMAIL, "wrong")
        assert not (Path(tmp) / CLOUD_FILE_NAME).exists()
        assert engine.session is None


def test_after_a_restart_the_saved_token_is_used_without_a_network_call() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        cloud, secrets = FakeSupabase(), MemoryStore()
        account_for(Path(tmp), cloud, secrets, SyncEngine(store.db)).sign_in(
            URL,
            jwt("anon"),
            EMAIL,
            PASSWORD,
        )
        engine = SyncEngine(store.db)
        restarted = account_for(Path(tmp), cloud, secrets, engine)
        assert restarted.restore()
        assert cloud.refreshes == 0
        session = engine.session
        assert session is not None and session.access_token == ""
        store.upsert("audit_log", {"id": "6c1f0a5e-0000-4000-8000-000000000001", "action": "x"})
        assert engine.run_once().state is SyncState.UP_TO_DATE
        assert cloud.refreshes == 1
        refreshed = engine.session
        assert refreshed is not None
        assert secrets.get(token_name("demo")) == refreshed.refresh_token


def test_signing_out_forgets_the_token_and_turns_sync_off() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        secrets = MemoryStore()
        engine = SyncEngine(store.db)
        account = account_for(Path(tmp), FakeSupabase(), secrets, engine)
        account.sign_in(URL, jwt("anon"), EMAIL, PASSWORD)
        account.sign_out()
        assert secrets.get(token_name("demo")) is None
        assert not load_cloud(Path(tmp)).enabled
        assert engine.run_once().state is SyncState.DISABLED
        assert not account.restore()


def test_a_new_account_that_needs_an_email_confirmation_is_not_signed_in() -> None:
    with temporary_store() as store, tempfile.TemporaryDirectory() as tmp:
        engine = SyncEngine(store.db)
        account = account_for(Path(tmp), FakeSupabase(confirm_email=True), MemoryStore(), engine)
        assert account.sign_up(URL, jwt("anon"), EMAIL, PASSWORD) is None
        assert engine.session is None
        assert load_cloud(Path(tmp)).email == EMAIL
        with pytest.raises(CloudSetupError):
            account.sign_up(URL, jwt("anon"), "not-an-email", PASSWORD)
