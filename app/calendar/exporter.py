"""The MT5 calendar exporter (spec C3).

The MetaTrader5 Python package cannot read the MQL5 economic calendar, so the app ships a
small MQL5 service, `CalendarExporter.mq5`. Once compiled and started in MT5 (Navigator >
Services), it writes the events of the last day and the next 7 days to
`Common/Files/tradeer_calendar.csv` every 5 minutes, with times in UTC. The app reads that
file every few minutes when it changed.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from app.calendar.csv_import import CsvResult, decode, parse_calendar_csv

EXPORTER_FILE_NAME = "CalendarExporter.mq5"
CSV_FILE_NAME = "tradeer_calendar.csv"
SOURCE = "mt5"


def bundled_exporter() -> Path:
    """The .mq5 source shipped with the app (also inside the PyInstaller build)."""
    return Path(__file__).resolve().parent / "mql5" / EXPORTER_FILE_NAME


def common_files_dir() -> Path:
    """MT5's shared data folder: %APPDATA%/MetaQuotes/Terminal/Common/Files."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path.home() / ".wine" / "drive_c" / "users" / "Public" / "AppData" / "Roaming"
    return root / "MetaQuotes" / "Terminal" / "Common" / "Files"


def calendar_csv_path() -> Path:
    return common_files_dir() / CSV_FILE_NAME


def services_dir(data_path: str) -> Path:
    """Where MT5 looks for services of this terminal: <data folder>/MQL5/Services."""
    return Path(data_path) / "MQL5" / "Services"


def install_exporter(data_path: str, source: Path | None = None) -> Path:
    """Copy the exporter into the terminal's MQL5/Services folder. Returns the new path."""
    if not data_path:
        raise OSError("The MT5 data folder is unknown. Connect to MT5 first.")
    target_dir = services_dir(data_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / EXPORTER_FILE_NAME
    shutil.copyfile(source or bundled_exporter(), target)
    return target


@dataclass(frozen=True)
class FileState:
    exists: bool
    modified: float = 0.0
    size: int = 0


class CalendarFileWatcher:
    """Reads the exporter's CSV again only when its time stamp or size changed."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or calendar_csv_path()
        self._seen: FileState | None = None

    def state(self) -> FileState:
        try:
            info = self.path.stat()
        except OSError:
            return FileState(False)
        return FileState(True, info.st_mtime, info.st_size)

    def check(self) -> CsvResult | None:
        """The parsed file if it changed since the last call, else None."""
        current = self.state()
        if not current.exists or current == self._seen:
            return None
        try:
            data = self.path.read_bytes()
        except OSError:
            return None
        self._seen = current
        return parse_calendar_csv(decode(data), source=SOURCE)

    def reset(self) -> None:
        """Read the file again on the next check, even if it did not change."""
        self._seen = None

    @property
    def last_modified(self) -> float | None:
        return self._seen.modified if self._seen is not None and self._seen.exists else None
