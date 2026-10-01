"""Persisted UI preferences (theme, Simple/Advanced view), stored per profile as JSON."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

PREFS_FILE_NAME = "ui_prefs.json"


class ThemeName(StrEnum):
    DARK = "dark"
    LIGHT = "light"


class ViewMode(StrEnum):
    SIMPLE = "simple"
    ADVANCED = "advanced"


class UiPrefs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: ThemeName = ThemeName.DARK
    view_mode: ViewMode = ViewMode.SIMPLE


def load_prefs(directory: Path) -> UiPrefs:
    try:
        raw = json.loads((directory / PREFS_FILE_NAME).read_text(encoding="utf-8"))
        return UiPrefs.model_validate(raw)
    except (OSError, ValueError):
        # Missing, unreadable, corrupt or invalid (pydantic errors are ValueErrors).
        return UiPrefs()


def save_prefs(directory: Path, prefs: UiPrefs) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / PREFS_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(prefs.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
