"""What the update service needs from an update framework (Velopack in the app, a fake in
tests): check the release feed, download (deltas when possible), apply after the app exits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

Progress = Callable[[int], None]


class UpdateError(Exception):
    """A check, download or apply step failed; the message is shown to the user."""


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    notes: str = ""  # Markdown release notes
    size: int = 0  # bytes to download
    delta: bool = False  # True when only the changes are downloaded
    downgrade: bool = False  # a rollback to an older version
    handle: object = field(default=None, compare=False)  # the framework's own objects


class UpdateBackend(Protocol):
    def unavailable_reason(self) -> str:
        """Empty when updates work here; else why not (for example a run from source)."""
        ...

    def current_version(self) -> str: ...

    def check(self) -> UpdateOffer | None:
        """The newest release, or None when this is the newest."""
        ...

    def check_version(self, version: str) -> UpdateOffer | None:
        """Exactly this (older) release, for a rollback."""
        ...

    def download(self, offer: UpdateOffer, progress: Progress) -> None: ...

    def apply_after_exit(self, offer: UpdateOffer) -> None:
        """Start the installer, which waits for this process to exit, then restarts the app."""
        ...
