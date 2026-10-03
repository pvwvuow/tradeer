import pytest

from app.core.credentials import (
    CredentialError,
    KeyringStore,
    MemoryStore,
    credential_name,
    read_password,
    save_password,
)
from app.observability.masking import MASKER


def test_names_are_per_profile_login_and_server() -> None:
    assert credential_name("demo 1", 51234567, "Broker-Demo") == "demo1/51234567@Broker-Demo"


def test_saved_and_read_passwords_are_masked_everywhere() -> None:
    store = MemoryStore()
    secret = "Unique-Pass-7781"
    save_password(store, "p/1@S", secret)
    try:
        assert MASKER.mask(f"login with {secret}") == "login with ***"
        assert read_password(store, "p/1@S") == secret
        assert read_password(store, "missing") is None
        store.delete("p/1@S")
        assert store.get("p/1@S") is None
    finally:
        MASKER.forget(secret)


def test_keyring_failures_become_credential_errors() -> None:
    class Broken(KeyringStore):
        def _keyring(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("no backend")

    store = Broken()
    for action in (lambda: store.get("x"), lambda: store.set("x", "y"), lambda: store.delete("x")):
        with pytest.raises(CredentialError):
            action()
