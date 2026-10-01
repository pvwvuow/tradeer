"""Per-user data locations. Nothing is ever written next to the installed program."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "MT5TradingWorkstation"


def safe_profile_name(profile: str) -> str:
    cleaned = "".join(char for char in profile if char.isalnum() or char in "-_")
    return cleaned or "default"


def _config_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def app_data_dir(profile: str = "default") -> Path:
    return _config_root() / APP_DIR_NAME / "profiles" / safe_profile_name(profile)
