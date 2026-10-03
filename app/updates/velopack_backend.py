"""The update backend on Velopack (ADR 113): the only module that talks to `velopack`.

Releases live on GitHub (one full package and one delta per release). Velopack downloads
the deltas between the installed and the newest version (binary patches of the changed
files), rebuilds the new package next to the installed one, checks its SHA-256 and swaps it
in after the app exits. A run from source or from a build folder has no Velopack install;
then every call reports why instead of failing.
"""

from __future__ import annotations

import importlib
from typing import Any

from app.updates.backend import Progress, UpdateError, UpdateOffer
from app.updates.versions import tag_for

REPO_URL = "https://github.com/pvwvuow/tradeer"
MAX_DELTAS = 10
NOT_INSTALLED = (
    "Updates work in the installed app only. Install it once with "
    "MT5TradingWorkstation-win-Setup.exe from the latest release."
)


class VelopackBackend:
    def __init__(self, repo_url: str = REPO_URL, module: Any = None) -> None:
        self._repo_url = repo_url.rstrip("/")
        self._velopack: Any = module
        self._manager: Any = None
        self._reason = ""
        self._current = ""
        if self._velopack is None:
            try:
                self._velopack = importlib.import_module("velopack")
            except Exception as error:
                self._reason = f"The update library could not load: {error}"
                return
        try:
            self._manager = self._velopack.UpdateManager(self._source())
            self._current = str(self._manager.get_current_version())
        except Exception as error:
            self._manager = None
            self._reason = f"{NOT_INSTALLED} ({error})"

    def unavailable_reason(self) -> str:
        return self._reason

    def current_version(self) -> str:
        return self._current

    def check(self) -> UpdateOffer | None:
        manager = self._require()
        try:
            info = manager.check_for_updates()
        except Exception as error:
            raise UpdateError(f"The release feed could not be read: {error}") from error
        return None if info is None else _offer(manager, info)

    def check_version(self, version: str) -> UpdateOffer | None:
        self._require()
        feed = f"{self._repo_url}/releases/download/{tag_for(version)}"
        try:
            options = self._velopack.UpdateOptions(True, MAX_DELTAS)
            manager = self._velopack.UpdateManager(feed, options)
            info = manager.check_for_updates()
        except Exception as error:
            raise UpdateError(f"Version {version} could not be found: {error}") from error
        return None if info is None else _offer(manager, info)

    def download(self, offer: UpdateOffer, progress: Progress) -> None:
        manager, info = _parts(offer)
        try:
            manager.download_updates(info, progress)
        except Exception as error:
            raise UpdateError(f"The download failed: {error}") from error

    def apply_after_exit(self, offer: UpdateOffer) -> None:
        manager, info = _parts(offer)
        try:
            manager.wait_exit_then_apply_updates(info)
        except Exception as error:
            raise UpdateError(f"The update could not be started: {error}") from error

    def _source(self) -> Any:
        source = getattr(self._velopack, "GithubSource", None)
        if source is not None:
            return source(self._repo_url)
        # Older bindings have no GithubSource: the newest release's files by their name.
        return f"{self._repo_url}/releases/latest/download"

    def _require(self) -> Any:
        if self._manager is None:
            raise UpdateError(self._reason or NOT_INSTALLED)
        return self._manager


def _offer(manager: Any, info: Any) -> UpdateOffer:
    target = getattr(info, "TargetFullRelease", None)
    deltas = list(getattr(info, "DeltasToTarget", None) or [])
    full_size = int(getattr(target, "Size", 0) or 0)
    delta_size = sum(int(getattr(delta, "Size", 0) or 0) for delta in deltas)
    return UpdateOffer(
        version=str(getattr(target, "Version", "") or ""),
        notes=str(getattr(target, "NotesMarkdown", "") or ""),
        size=delta_size if deltas else full_size,
        delta=bool(deltas),
        downgrade=bool(getattr(info, "IsDowngrade", False)),
        handle=(manager, info),
    )


def _parts(offer: UpdateOffer) -> tuple[Any, Any]:
    handle = offer.handle
    if not isinstance(handle, tuple) or len(handle) != 2:
        raise UpdateError("This update was not found by the update library")
    return handle[0], handle[1]
