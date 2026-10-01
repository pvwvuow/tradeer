"""One app instance per profile (spec D3.8), with an operating-system file lock.

The lock disappears with the process, even after a crash, so a stale lock never blocks a
restart. The file holds the owner's process id. Windows locks are mandatory, so the locked
byte lies far past that text and anyone can still read the process id.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import IO

LOCK_FILE_NAME = "instance.lock"
LOCK_OFFSET = 1 << 20


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Kept open on purpose: the lock lives as long as this handle.
        handle = self.path.open("a+b")  # noqa: SIM115
        try:
            _lock(handle)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        with contextlib.suppress(OSError):
            _unlock(handle)
        handle.close()

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()


def _lock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
