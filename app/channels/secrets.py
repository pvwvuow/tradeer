"""The Telegram secrets in Windows Credential Manager (docs/SIGNAL_DESK.md 1.6 and 3.1):
the api_hash, the Telethon session and the proxy password or MTProto secret.

Every value read or saved goes to the log masker first (`read_password`, `save_password`),
so it can never show up in a log line, a crash report or the support bundle.
"""

from __future__ import annotations

import contextlib

from app.channels.settings import api_hash_name, proxy_secret_name, session_name
from app.core.credentials import CredentialError, CredentialStore, read_password, save_password


class CredentialSecrets:
    def __init__(self, store: CredentialStore, profile: str) -> None:
        self._store = store
        self._profile = profile

    def _read(self, name: str) -> str:
        try:
            return read_password(self._store, name) or ""
        except CredentialError:
            return ""

    def _forget(self, name: str) -> None:
        with contextlib.suppress(CredentialError):
            self._store.delete(name)

    def api_hash(self) -> str:
        return self._read(api_hash_name(self._profile))

    def save_api_hash(self, value: str) -> None:
        save_password(self._store, api_hash_name(self._profile), value.strip())

    def proxy_secret(self) -> str:
        return self._read(proxy_secret_name(self._profile))

    def save_proxy_secret(self, value: str) -> None:
        save_password(self._store, proxy_secret_name(self._profile), value)

    def session(self) -> str:
        return self._read(session_name(self._profile))

    def save_session(self, value: str) -> None:
        if value:
            save_password(self._store, session_name(self._profile), value)

    def forget_session(self) -> None:
        self._forget(session_name(self._profile))
