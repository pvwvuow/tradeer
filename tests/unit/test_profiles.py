import tempfile
from pathlib import Path

from app.core.profiles import (
    ACCOUNT_FILE_NAME,
    AccountProfile,
    list_profiles,
    load_account,
    save_account,
)


def test_accounts_round_trip_without_any_password() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "demo"
        account = AccountProfile(login=51234567, server="Broker-Demo", terminal_path="C:\\mt5")
        save_account(folder, account)
        text = (folder / ACCOUNT_FILE_NAME).read_text(encoding="utf-8")
        loaded = load_account(folder)
    assert loaded == account
    assert loaded.configured
    assert "password" not in text
    request = loaded.request("secret-value")
    assert (request.login, request.server) == (51234567, "Broker-Demo")
    assert request.password == "secret-value"
    assert request.symbols == ("EURUSD", "GBPUSD", "XAUUSD")


def test_missing_or_broken_files_give_an_empty_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        assert not load_account(folder).configured
        (folder / ACCOUNT_FILE_NAME).write_text("{broken", encoding="utf-8")
        assert load_account(folder) == AccountProfile()
        (folder / ACCOUNT_FILE_NAME).write_text('{"login": -5}', encoding="utf-8")
        assert load_account(folder).login is None


def test_used_profiles_are_listed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        save_account(root / "live", AccountProfile(login=1, server="S"))
        (root / "default").mkdir()
        (root / "default" / "ui_prefs.json").write_text("{}", encoding="utf-8")
        (root / "empty").mkdir()
        assert list_profiles(root) == ["default", "live"]
    assert list_profiles(Path(tmp) / "gone") == []
