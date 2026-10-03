"""Trading mode and execution settings (spec B3, C7, C8), saved per profile in `execution.json`.

Paper is the default. Auto mode needs the Go-Live gate (Phase 13), so it cannot be saved yet:
a file that asks for it loads as Paper with a note. Position management rules are kept per
strategy and are all off until the user turns them on.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.management import ManagementSettings
from app.domain.modes import DEFAULT_MODE, OperatingMode

EXECUTION_FILE_NAME = "execution.json"
AUTO_NOTE = "Auto mode needs the Go-Live gate (Phase 13); using Paper"


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deviation_points: int = Field(
        default=10,
        ge=0,
        le=1000,
        description="Max price deviation for market orders (points)",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=5,
        description="Retries on requote, price changed, off quotes or timeout",
    )
    max_entry_move_r: float = Field(
        default=0.25,
        ge=0,
        le=1,
        description="Max price move from the signal's entry at approval (R)",
    )
    max_spread_sl_fraction: float = Field(
        default=0.2,
        gt=0,
        le=1,
        description="Max spread at approval (fraction of the SL distance)",
    )
    paper_slippage_points: int = Field(
        default=1,
        ge=0,
        le=1000,
        description="Paper fills: slippage (points)",
    )
    paper_commission_per_lot: float = Field(
        default=0.0,
        ge=0,
        le=1000,
        description="Paper fills: commission per lot, round turn (account currency)",
    )
    paper_start_balance: float = Field(
        default=0.0,
        ge=0,
        le=1e9,
        description="Paper start balance (0 = the MT5 account balance)",
    )


def _default_settings() -> ExecutionSettings:
    return ExecutionSettings()


def _no_management() -> dict[str, ManagementSettings]:
    return {}


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: OperatingMode = DEFAULT_MODE
    settings: ExecutionSettings = Field(default_factory=_default_settings)
    management: dict[str, ManagementSettings] = Field(default_factory=_no_management)

    @field_validator("mode")
    @classmethod
    def _no_auto(cls, mode: OperatingMode) -> OperatingMode:
        if mode is OperatingMode.AUTO:
            raise ValueError(AUTO_NOTE)
        return mode

    def management_for(self, strategy: str) -> ManagementSettings:
        return self.management.get(strategy) or ManagementSettings()


def load_execution_config(directory: Path) -> tuple[ExecutionConfig, str]:
    """The saved config and a note when the file was invalid (then Paper with defaults)."""
    path = directory / EXECUTION_FILE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ExecutionConfig(), ""
    except (OSError, ValueError):
        return ExecutionConfig(), f"{EXECUTION_FILE_NAME} could not be read: using Paper mode"
    try:
        return ExecutionConfig.model_validate(raw), ""
    except ValidationError as error:
        if isinstance(raw, dict) and raw.get("mode") == OperatingMode.AUTO.value:
            return ExecutionConfig(), AUTO_NOTE
        count = error.error_count()
        return ExecutionConfig(), f"{EXECUTION_FILE_NAME}: {count} invalid value(s), using Paper"


def save_execution_config(directory: Path, config: ExecutionConfig) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / EXECUTION_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class ExecutionSettingsSource:
    """The saved config, shared by the UI and the analysis thread; read again only when the
    file changed."""

    def __init__(self, directory: Path, note: Callable[[str], None] | None = None) -> None:
        self._directory = directory
        self._path = directory / EXECUTION_FILE_NAME
        self._note = note
        self._lock = threading.Lock()
        self._stamp: float | None = None
        self._loaded = False
        self._config = ExecutionConfig()

    @property
    def config(self) -> ExecutionConfig:
        self._reload()
        with self._lock:
            return self._config

    @property
    def mode(self) -> OperatingMode:
        return self.config.mode

    def save(self, config: ExecutionConfig) -> None:
        save_execution_config(self._directory, config)
        with self._lock:
            self._loaded = False
        self._reload()

    def _reload(self) -> None:
        try:
            stamp: float | None = self._path.stat().st_mtime_ns / 1e9
        except OSError:
            stamp = None
        with self._lock:
            if self._loaded and stamp == self._stamp:
                return
            self._loaded = True
            self._stamp = stamp
            config, note = load_execution_config(self._directory)
            self._config = config
        if note and self._note is not None:
            self._note(note)
