"""Per-user data locations. Nothing is ever written next to the installed program."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "MT5TradingWorkstation"
LOGS_DIR_NAME = "logs"
CRASH_REPORTS_DIR_NAME = "crash_reports"


def safe_profile_name(profile: str) -> str:
    cleaned = "".join(char for char in profile if char.isalnum() or char in "-_")
    return cleaned or "default"


def _config_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def profiles_root() -> Path:
    return _config_root() / APP_DIR_NAME / "profiles"


def app_data_dir(profile: str = "default") -> Path:
    return profiles_root() / safe_profile_name(profile)


def logs_dir(profile: str = "default") -> Path:
    return app_data_dir(profile) / LOGS_DIR_NAME


def crash_reports_dir(profile: str = "default") -> Path:
    return app_data_dir(profile) / CRASH_REPORTS_DIR_NAME
