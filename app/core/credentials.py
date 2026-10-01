"""Account passwords in Windows Credential Manager through `keyring` (spec C1, D5).

Every password that is read or saved is registered with the log masker at once, so it can
never appear in a log line, crash report or diagnostics report.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Protocol

from app.core.paths import safe_profile_name
from app.observability.masking import MASKER

SERVICE_NAME = "MT5TradingWorkstation"


class CredentialError(Exception):
    """The credential store could not be used."""


class CredentialStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, secret: str) -> None: ...

    def delete(self, name: str) -> None: ...


def credential_name(profile: str, login: int, server: str) -> str:
    return f"{safe_profile_name(profile)}/{login}@{server}"


class KeyringStore:
    """Windows Credential Manager. `keyring` is imported on first use."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self.service = service
        self._ready = False

    def _keyring(self) -> ModuleType:
        import keyring

        if not self._ready and sys.platform == "win32":
            # Choose the Windows backend directly: frozen builds can miss backend metadata.
            from keyring.backends import Windows

            keyring.set_keyring(Windows.WinVaultKeyring())  # type: ignore[no-untyped-call]
        self._ready = True
        module: ModuleType = keyring
        return module

    def get(self, name: str) -> str | None:
        try:
            value = self._keyring().get_password(self.service, name)
        except Exception as error:
            raise CredentialError(f"Windows Credential Manager failed: {error}") from error
        return str(value) if value is not None else None

    def set(self, name: str, secret: str) -> None:
        try:
            self._keyring().set_password(self.service, name, secret)
        except Exception as error:
            raise CredentialError(f"Windows Credential Manager failed: {error}") from error

    def delete(self, name: str) -> None:
        try:
            self._keyring().delete_password(self.service, name)
        except Exception as error:
            raise CredentialError(f"Windows Credential Manager failed: {error}") from error


class MemoryStore:
    """In-memory store for tests and for runs without a usable keyring."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, secret: str) -> None:
        self._values[name] = secret

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


def read_password(store: CredentialStore, name: str) -> str | None:
    value = store.get(name)
    MASKER.register(value)
    return value


def save_password(store: CredentialStore, name: str, secret: str) -> None:
    MASKER.register(secret)
    store.set(name, secret)
