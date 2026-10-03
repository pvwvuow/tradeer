"""Supabase over plain HTTPS (spec E1, D5): Auth with email and password, PostgREST upserts.

Only the project's anon (publishable) key is used. Row-level security limits every request to
the signed-in user's rows. Failures are raised as `RemoteError` (see `remote.py`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from app.storage.remote import AuthSession, RemoteError, RemoteErrorKind, classify

TIMEOUT_SECONDS = 20.0
UPSERT_PREFER = "resolution=merge-duplicates,return=minimal"


class SupabaseClient:
    def __init__(
        self,
        url: str,
        anon_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.url = url.rstrip("/")
        self._clock = clock
        self._http = httpx.Client(
            base_url=self.url,
            timeout=timeout,
            transport=transport,
            headers={"apikey": anon_key},
        )

    def close(self) -> None:
        self._http.close()

    def sign_in(self, email: str, password: str) -> AuthSession:
        body = {"email": email, "password": password}
        data = self._auth("/auth/v1/token", body, {"grant_type": "password"})
        return self._session(data, email)

    def sign_up(self, email: str, password: str) -> AuthSession | None:
        """Create the account. None means Supabase sent a confirmation email first."""
        data = self._auth("/auth/v1/signup", {"email": email, "password": password})
        return self._session(data, email) if data.get("access_token") else None

    def refresh(self, refresh_token: str) -> AuthSession:
        body = {"refresh_token": refresh_token}
        data = self._auth("/auth/v1/token", body, {"grant_type": "refresh_token"})
        return self._session(data, "")

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]], access_token: str) -> None:
        """Insert or update rows by id (idempotent: a retry never creates a duplicate)."""
        headers = {"Authorization": f"Bearer {access_token}", "Prefer": UPSERT_PREFER}
        self._send(
            "POST",
            f"/rest/v1/{table}",
            params={"on_conflict": "id"},
            json=[dict(row) for row in rows],
            headers=headers,
        )

    def _auth(
        self,
        path: str,
        body: Mapping[str, str],
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._send("POST", path, params=params, json=dict(body))
        except RemoteError as error:
            if error.kind is RemoteErrorKind.REJECTED:
                auth = RemoteErrorKind.AUTH
                raise RemoteError(auth, error.message, status=error.status) from None
            raise
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise RemoteError(RemoteErrorKind.SERVER, "Supabase Auth gave an unexpected answer")
        return data

    def _session(self, data: Mapping[str, Any], email: str) -> AuthSession:
        found = data.get("user")
        user: dict[str, Any] = found if isinstance(found, dict) else {}
        try:
            return AuthSession(
                user_id=str(user["id"]),
                email=str(user.get("email") or email),
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=self._clock() + float(data.get("expires_in") or 3600),
            )
        except (KeyError, TypeError, ValueError) as error:
            message = "Supabase Auth returned no session"
            raise RemoteError(RemoteErrorKind.SERVER, message) from error

    def _send(self, method: str, path: str, **options: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **options)
        except httpx.TimeoutException as error:
            raise RemoteError(RemoteErrorKind.OFFLINE, "Supabase did not answer in time") from error
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            message = f"No connection to Supabase ({type(error).__name__})"
            raise RemoteError(RemoteErrorKind.OFFLINE, message) from error
        if response.status_code >= 400:
            raise classify(response.status_code, response.text, response.headers)
        return response
