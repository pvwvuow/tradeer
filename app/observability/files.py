"""Log files (spec E3): `logs/<category>/<date>.jsonl` plus a readable `logs/all.log`.

A file is rotated when the local date changes or when it would grow past `max_file_bytes`.
Rotated files are zipped. Housekeeping (on the first write, then every few minutes) zips
files left over from earlier runs, deletes files older than the retention period, and, when
the folder is larger than `max_total_bytes`, deletes the oldest closed files first. Files that
are open for writing are never touched.

loguru calls `write()` from its single writer thread (`enqueue=True`), so the UI never waits
for the disk. A lock keeps the writer safe for any other caller as well.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

ALL_LOG_NAME = "all.log"
JSONL_SUFFIX = ".jsonl"
TEXT_SUFFIX = ".log"
ZIP_SUFFIX = ".zip"
PARTIAL_SUFFIX = ".part"
MANAGED_SUFFIXES = frozenset({JSONL_SUFFIX, TEXT_SUFFIX, ZIP_SUFFIX, PARTIAL_SUFFIX})
MB = 1024 * 1024
_ALL = "*"


@dataclass(frozen=True)
class LogFilePolicy:
    max_file_bytes: int = 20 * MB
    retention_days: int = 30
    max_total_bytes: int = 500 * MB
    housekeeping_seconds: float = 600.0


def local_now() -> datetime:
    return datetime.now().astimezone()


def _modified_day(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime).date()


@dataclass
class _OpenFile:
    path: Path
    handle: TextIO
    day: date
    size: int


class LogFileWriter:
    def __init__(
        self,
        root: Path,
        policy: LogFilePolicy | None = None,
        clock: Callable[[], datetime] = local_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root
        self.policy = policy or LogFilePolicy()
        self.failed_writes = 0
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._files: dict[str, _OpenFile] = {}
        self._next_housekeeping = float("-inf")
        self._closed = False

    def __enter__(self) -> LogFileWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def write(self, category: str, json_line: str, text_line: str) -> None:
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            for key, line in ((_safe_category(category), json_line), (_ALL, text_line)):
                try:
                    self._append(key, line, now)
                except OSError:
                    self.failed_writes += 1
                    self._discard(key)
            if self._monotonic() >= self._next_housekeeping:
                self._next_housekeeping = self._monotonic() + self.policy.housekeeping_seconds
                self._housekeep(now)

    def housekeep(self) -> None:
        with self._lock:
            self._housekeep(self._clock())

    def open_paths(self) -> list[Path]:
        with self._lock:
            return [opened.path for opened in self._files.values()]

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for key in list(self._files):
                self._discard(key)

    def _target(self, key: str, day: date) -> Path:
        if key == _ALL:
            return self.root / ALL_LOG_NAME
        return self.root / key / f"{day.isoformat()}{JSONL_SUFFIX}"

    def _append(self, key: str, line: str, now: datetime) -> None:
        data = line if line.endswith("\n") else f"{line}\n"
        size = len(data.encode("utf-8"))
        day = now.date()
        current = self._files.get(key)
        if current is not None:
            too_big = current.size > 0 and current.size + size > self.policy.max_file_bytes
            if current.day != day or too_big:
                self._discard(key)
                self._archive(current.path, now)
                current = None
        if current is None:
            current = self._open(key, day, now)
        current.handle.write(data)
        current.handle.flush()
        current.size += size

    def _open(self, key: str, day: date, now: datetime) -> _OpenFile:
        path = self._target(key, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        if key == _ALL and path.exists() and _modified_day(path) != day:
            self._archive(path, now)
        handle = path.open("a", encoding="utf-8", newline="\n")
        opened = _OpenFile(path=path, handle=handle, day=day, size=path.stat().st_size)
        self._files[key] = opened
        return opened

    def _discard(self, key: str) -> None:
        opened = self._files.pop(key, None)
        if opened is not None:
            with contextlib.suppress(OSError):
                opened.handle.close()

    def _archive(self, path: Path, now: datetime) -> None:
        """Zip a closed file next to itself under a unique name, then delete the original."""
        stamp = now.strftime("%Y%m%d-%H%M%S-%f")
        member = f"{path.stem}.{stamp}{path.suffix}"
        counter = 1
        while path.with_name(f"{member}{ZIP_SUFFIX}").exists() or path.with_name(member).exists():
            member = f"{path.stem}.{stamp}-{counter}{path.suffix}"
            counter += 1
        target = path.with_name(f"{member}{ZIP_SUFFIX}")
        partial = path.with_name(f"{member}{ZIP_SUFFIX}{PARTIAL_SUFFIX}")
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(path, arcname=member)
            os.replace(partial, target)
            path.unlink()
            return
        except OSError:
            with contextlib.suppress(OSError):
                partial.unlink(missing_ok=True)
        # Could not compress: keep the data uncompressed under the rotated name.
        with contextlib.suppress(OSError):
            path.replace(path.with_name(member))

    def _housekeep(self, now: datetime) -> None:
        try:
            self._compress_leftovers(now)
            self._apply_retention(now)
            self._apply_size_cap()
        except OSError:
            self.failed_writes += 1

    def _closed_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        active = {opened.path for opened in self._files.values()}
        return [
            path
            for path in self.root.rglob("*")
            if path.suffix in MANAGED_SUFFIXES and path not in active and path.is_file()
        ]

    def _compress_leftovers(self, now: datetime) -> None:
        today = f"{now.date().isoformat()}{JSONL_SUFFIX}"
        for path in self._closed_files():
            if path.suffix == PARTIAL_SUFFIX:
                with contextlib.suppress(OSError):
                    path.unlink()
            elif path.suffix == JSONL_SUFFIX and path.name != today:
                self._archive(path, now)
            elif path.suffix == TEXT_SUFFIX and path.name != ALL_LOG_NAME:
                self._archive(path, now)

    def _apply_retention(self, now: datetime) -> None:
        cutoff = now.timestamp() - self.policy.retention_days * 86400
        for path in self._closed_files():
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()

    def _apply_size_cap(self) -> None:
        if not self.root.exists():
            return
        sizes = {path: path.stat().st_size for path in self.root.rglob("*") if path.is_file()}
        total = sum(sizes.values())
        if total <= self.policy.max_total_bytes:
            return
        for path in sorted(self._closed_files(), key=lambda item: item.stat().st_mtime):
            if total <= self.policy.max_total_bytes:
                break
            with contextlib.suppress(OSError):
                path.unlink()
                total -= sizes.get(path, 0)


def _safe_category(category: str) -> str:
    return category if category.replace("_", "").isalnum() else "app"
