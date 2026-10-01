"""FakeSupabase: an in-memory stand-in for Supabase Auth and PostgREST, for tests only.

Rows are kept by table and id, like a primary key, so an upsert can never store a row twice.
Switches make it offline, paused, without tables, refuse rows, expire tokens or lose the answer
of a request that it did apply.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.storage.remote import AuthSession, RemoteError, RemoteErrorKind

USER_ID = "4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
EMAIL = "trader@example.com"
PASSWORD = "Cloud-Pass-789"


@dataclass
class FakeSupabase:
    online: bool = True
    paused: bool = False
    tables_missing: bool = False
    revoked: bool = False
    lose_answers: int = 0
    refuse: Callable[[str, Mapping[str, Any]], bool] | None = None
    confirm_email: bool = False
    expired: set[str] = field(default_factory=set)
    rows: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    received: list[tuple[str, str]] = field(default_factory=list)
    requests: int = 0
    refreshes: int = 0
    closed: bool = False
    _tokens: int = 0

    def session(self, email: str = EMAIL, lifetime: float = 3600.0) -> AuthSession:
        self._tokens += 1
        return AuthSession(
            user_id=USER_ID,
            email=email,
            access_token=f"access-token-{self._tokens}",
            refresh_token=f"refresh-token-{self._tokens}",
            expires_at=time.time() + lifetime,
        )

    def sign_in(self, email: str, password: str) -> AuthSession:
        self._check_online()
        if password != PASSWORD:
            raise RemoteError(RemoteErrorKind.AUTH, "HTTP 400: Invalid login credentials")
        return self.session(email)

    def sign_up(self, email: str, password: str) -> AuthSession | None:
        self._check_online()
        return None if self.confirm_email else self.session(email)

    def refresh(self, refresh_token: str) -> AuthSession:
        self.refreshes += 1
        self._check_online()
        if self.revoked:
            raise RemoteError(RemoteErrorKind.AUTH, "HTTP 400: Invalid Refresh Token")
        return self.session("")

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]], access_token: str) -> None:
        self.requests += 1
        self._check_online()
        if self.paused:
            raise RemoteError(RemoteErrorKind.PAUSED, "HTTP 540: project paused")
        if self.tables_missing:
            raise RemoteError(RemoteErrorKind.SETUP, f"HTTP 404 PGRST205: no table {table}")
        if access_token in self.expired:
            raise RemoteError(RemoteErrorKind.AUTH, "HTTP 401: JWT expired")
        if self.refuse is not None and any(self.refuse(table, row) for row in rows):
            raise RemoteError(RemoteErrorKind.REJECTED, "HTTP 400 22P02: invalid input syntax")
        keys = {tuple(sorted(row)) for row in rows}
        if len(keys) > 1:
            raise RemoteError(RemoteErrorKind.REJECTED, "HTTP 400 PGRST102: keys must match")
        stored = self.rows.setdefault(table, {})
        for row in rows:
            stored[str(row["id"])] = dict(row)
            self.received.append((table, str(row["id"])))
        if self.lose_answers:
            self.lose_answers -= 1
            raise RemoteError(RemoteErrorKind.OFFLINE, "The answer was lost")

    def close(self) -> None:
        self.closed = True

    def count(self, table: str) -> int:
        return len(self.rows.get(table, {}))

    def _check_online(self) -> None:
        if not self.online:
            raise RemoteError(RemoteErrorKind.OFFLINE, "No connection to Supabase (ConnectError)")
