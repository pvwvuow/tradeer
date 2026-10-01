"""What the cloud sync needs to know about Supabase, without any network code.

`RemoteError` sorts every failure for the sync engine: offline, paused project, expired
sign-in, missing tables (setup), refused rows, rate limits and server errors. `classify()` maps
an HTTP answer to one of them. The HTTP client itself is in `supabase_client.py`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

PAUSED_STATUSES = frozenset({540})
SETUP_CODES = frozenset({"PGRST204", "PGRST205", "42P01", "42703"})


class RemoteErrorKind(StrEnum):
    OFFLINE = "offline"
    PAUSED = "paused"
    AUTH = "auth"
    SETUP = "setup"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    SERVER = "server"


class RemoteError(Exception):
    def __init__(
        self,
        kind: RemoteErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    email: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float = 0.0

    def expires_soon(self, now: float, margin_seconds: float = 60.0) -> bool:
        return not self.access_token or self.expires_at - margin_seconds <= now


def _details(body: str) -> tuple[str, str]:
    """(code, message) from a PostgREST or Auth error body."""
    try:
        data = json.loads(body)
    except ValueError:
        return "", body[:300]
    if not isinstance(data, dict):
        return "", body[:300]
    code = str(data.get("code") or data.get("error_code") or "")
    for key in ("message", "msg", "error_description", "error"):
        if data.get(key):
            return code, str(data[key])[:300]
    return code, body[:300]


def classify(status: int, body: str, headers: Mapping[str, str]) -> RemoteError:
    code, message = _details(body)
    text = f"HTTP {status}" + (f" {code}" if code else "") + (f": {message}" if message else "")
    if status in PAUSED_STATUSES or (status >= 500 and "paused" in body.lower()):
        return RemoteError(RemoteErrorKind.PAUSED, text, status=status)
    if status == 429:
        try:
            retry_after: float | None = float(headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        kind = RemoteErrorKind.RATE_LIMITED
        return RemoteError(kind, text, status=status, retry_after=retry_after)
    if status == 401:
        return RemoteError(RemoteErrorKind.AUTH, text, status=status)
    if status == 404 or code in SETUP_CODES:
        return RemoteError(RemoteErrorKind.SETUP, text, status=status)
    if status >= 500:
        return RemoteError(RemoteErrorKind.SERVER, text, status=status)
    return RemoteError(RemoteErrorKind.REJECTED, text, status=status)
