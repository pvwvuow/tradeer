"""Persisted UI preferences (theme, language, Simple/Advanced view, first-run screen), per
profile.

0.33 (No Curve v2): the design opens in the light theme, so the default theme is light. A
preferences file written before 0.33 (no `look`, or an older one) is moved to the light
theme once; the owner can switch back to dark and that choice is kept from then on.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

PREFS_FILE_NAME = "ui_prefs.json"
LOOK = 2  # 1: the 0.13 to 0.32 looks; 2: No Curve v2 (light theme by default)


class ThemeName(StrEnum):
    DARK = "dark"
    LIGHT = "light"


class ViewMode(StrEnum):
    SIMPLE = "simple"
    ADVANCED = "advanced"


class Language(StrEnum):
    EN = "en"
    FA = "fa"  # Persian, right to left (spec A: UI language English + Persian)


class UiPrefs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: ThemeName = ThemeName.LIGHT
    view_mode: ViewMode = ViewMode.SIMPLE
    onboarded: bool = False  # the first-run "practice money" screen was answered (spec F0)
    language: Language = Language.EN  # used from the next start of the app
    look: int = LOOK  # the design the theme choice was made in (see `migrate`)


def migrate(prefs: UiPrefs) -> UiPrefs:
    """Preferences from an older look start once in the new design's light theme."""
    if prefs.look >= LOOK:
        return prefs
    return prefs.model_copy(update={"theme": ThemeName.LIGHT, "look": LOOK})


def load_prefs(directory: Path) -> UiPrefs:
    try:
        raw = json.loads((directory / PREFS_FILE_NAME).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "look" not in raw:
            raw = {**raw, "look": 1}  # written before 0.33
        return migrate(UiPrefs.model_validate(raw))
    except (OSError, ValueError):
        # Missing, unreadable, corrupt or invalid (pydantic errors are ValueErrors).
        return UiPrefs()


def save_prefs(directory: Path, prefs: UiPrefs) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / PREFS_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(prefs.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
