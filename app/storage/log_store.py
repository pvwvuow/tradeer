"""Audit entries and WARNING+ log entries go to the database, and from there to the cloud.

Spec E1: Supabase receives business data and WARNING+ logs; DEBUG, TRACE and INFO stay in the
local log files. The sink runs in its own loguru worker thread. It never logs anything itself
(a failing write could otherwise log itself in a loop); failures are only counted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.observability.categories import LogCategory
from app.storage.repositories import Store

WARNING_LEVEL = 30


def keep_entry(level_no: int, category: str) -> bool:
    """Which log entries are stored in the database (and uploaded)."""
    return category == LogCategory.AUDIT.value or level_no >= WARNING_LEVEL


class LogStore:
    def __init__(self, store: Store, account: Callable[[], str | None] | None = None) -> None:
        self._store = store
        self.account = account
        self.written = 0
        self.failures = 0

    def __call__(self, entry: Mapping[str, Any]) -> None:
        try:
            account = self.account() if self.account is not None else None
            if entry.get("category") == LogCategory.AUDIT.value:
                self._store.record_audit(entry, account)
            else:
                self._store.record_log(entry, account)
            self.written += 1
        except Exception:
            self.failures += 1
