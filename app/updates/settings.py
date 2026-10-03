"""Update settings (spec J2, J4), saved per profile in `updates.json`."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

UPDATES_FILE_NAME = "updates.json"


class UpdateSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auto_check: bool = Field(default=True, description="Look for a new version in the background")
    check_hours: int = Field(default=6, ge=1, le=48, description="Hours between two checks")
    auto_download: bool = Field(
        default=True,
        description="Download a new version in the background (never a major one)",
    )
    paused: bool = Field(default=False, description="No checks and no prompts while paused")


def load_update_settings(directory: Path) -> tuple[UpdateSettings, str]:
    path = directory / UPDATES_FILE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return UpdateSettings(), ""
    except (OSError, ValueError):
        return UpdateSettings(), f"{UPDATES_FILE_NAME} could not be read: using defaults"
    try:
        return UpdateSettings.model_validate(raw), ""
    except ValidationError as error:
        return UpdateSettings(), f"{UPDATES_FILE_NAME}: {error.error_count()} invalid value(s)"


def save_update_settings(directory: Path, settings: UpdateSettings) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / UPDATES_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class UpdateSettingsSource:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._lock = threading.Lock()
        self._settings, self.note = load_update_settings(directory)

    @property
    def settings(self) -> UpdateSettings:
        with self._lock:
            return self._settings

    def save(self, settings: UpdateSettings) -> None:
        save_update_settings(self._directory, settings)
        with self._lock:
            self._settings = settings
