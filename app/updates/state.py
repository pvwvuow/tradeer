"""Which versions ran on this PC and whether they started properly (spec J4).

One file for the whole install (`update_state.json` next to the profiles), because an
update replaces the program for every profile. A launch is "healthy" once it ran for
`HEALTHY_SECONDS` or closed normally; two unhealthy launches in a row of a version that
came from an update mean it crashes early, and the app offers to go back.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.paths import profiles_root

STATE_FILE_NAME = "update_state.json"
HEALTHY_SECONDS = 600.0
CRASH_LAUNCHES = 2
KEEP_LAUNCHES = 10


def default_state_path() -> Path:
    return profiles_root().parent / STATE_FILE_NAME


@dataclass(frozen=True)
class StartInfo:
    version: str
    updated_from: str  # the version before this one when this is its first launch, else ""
    previous: str  # the version that ran before this one (the rollback target), "" if none
    crash_loop: bool  # the last launches of this version ended before they were healthy


class LaunchTracker:
    def __init__(
        self,
        version: str,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._version = version
        self._path = path or default_state_path()
        self._clock = clock
        self._lock = threading.Lock()
        self._started = 0.0
        self._healthy = False

    def start(self) -> StartInfo:
        with self._lock:
            state = self._load()
            current = str(state.get("current", ""))
            previous = str(state.get("previous", ""))
            updated_from = ""
            if current and current != self._version:
                updated_from = current
                previous = current
            launches = [entry for entry in _launches(state) if entry.get("version")]
            mine = [entry for entry in launches if entry["version"] == self._version]
            recent = mine[-CRASH_LAUNCHES:]
            crash_loop = (
                bool(previous)
                and len(recent) == CRASH_LAUNCHES
                and not any(bool(entry.get("healthy")) for entry in recent)
            )
            self._started = self._clock()
            launches.append({"version": self._version, "started": self._started, "healthy": False})
            state.update(
                {
                    "current": self._version,
                    "previous": previous,
                    "launches": launches[-KEEP_LAUNCHES:],
                },
            )
            if updated_from:
                state["updated_at"] = self._started
            self._save(state)
            return StartInfo(self._version, updated_from, previous, crash_loop)

    def mark_healthy(self) -> None:
        """Called after `HEALTHY_SECONDS` of running and on a normal close."""
        with self._lock:
            if self._healthy or not self._started:
                return
            state = self._load()
            launches = _launches(state)
            for entry in reversed(launches):
                if entry.get("version") == self._version and entry.get("started") == self._started:
                    entry["healthy"] = True
                    break
            state["launches"] = launches
            self._save(state)
            self._healthy = True

    @property
    def healthy(self) -> bool:
        return self._healthy

    def _load(self) -> dict[str, object]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, state: dict[str, object]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temporary.replace(self._path)
        except OSError:
            return  # never stop the app because this note could not be written


def _launches(state: dict[str, object]) -> list[dict[str, object]]:
    raw = state.get("launches")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]
