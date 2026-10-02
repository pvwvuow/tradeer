"""Strategy settings per profile (spec F3 Strategies page): `strategies.json`.

For each built-in strategy: on or off and its parameters; plus the signal filters. A file
that cannot be read gives the defaults (both example strategies on, signal only), and
invalid parameters fall back to the strategy's defaults with a note, so a typo never stops
the analysis.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.engine.filters import FilterSettings
from app.strategies.base import Strategy
from app.strategies.registry import STRATEGIES, create_strategy

STRATEGIES_FILE_NAME = "strategies.json"


class StrategyEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


def _default_entries() -> dict[str, StrategyEntry]:
    return {name: StrategyEntry() for name in STRATEGIES}


class StrategySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategies: dict[str, StrategyEntry] = Field(default_factory=_default_entries)
    filters: FilterSettings = Field(default_factory=FilterSettings)

    def entry(self, name: str) -> StrategyEntry:
        return self.strategies.get(name) or StrategyEntry()

    def with_entry(self, name: str, entry: StrategyEntry) -> StrategySettings:
        strategies = {**self.strategies, name: entry}
        return self.model_copy(update={"strategies": strategies})

    def with_filters(self, filters: FilterSettings) -> StrategySettings:
        return self.model_copy(update={"filters": filters})

    def enabled(self) -> list[str]:
        return [name for name in STRATEGIES if self.entry(name).enabled]


def load_strategy_settings(directory: Path) -> StrategySettings:
    try:
        raw = json.loads((directory / STRATEGIES_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StrategySettings()
    if not isinstance(raw, dict):
        return StrategySettings()
    entries = _default_entries()
    for name, value in dict(raw.get("strategies") or {}).items():
        if name in STRATEGIES:
            try:
                entries[name] = StrategyEntry.model_validate(value)
            except ValidationError:
                continue
    try:
        filters = FilterSettings.model_validate(raw.get("filters") or {})
    except ValidationError:
        filters = FilterSettings()
    return StrategySettings(strategies=entries, filters=filters)


def save_strategy_settings(directory: Path, settings: StrategySettings) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / STRATEGIES_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


def build_strategies(settings: StrategySettings) -> tuple[list[Strategy], list[str]]:
    """The enabled strategies, and a note for each one whose saved params were invalid."""
    built: list[Strategy] = []
    notes: list[str] = []
    for name in settings.enabled():
        params = settings.entry(name).params
        try:
            built.append(create_strategy(name, params))
        except ValidationError as error:
            count = error.error_count()
            notes.append(f"{name}: {count} invalid setting(s), using the defaults")
            built.append(create_strategy(name))
    return built, notes


Note = Callable[[str], None]


class StrategySettingsSource:
    """The saved settings and the strategies built from them, shared by the UI and the
    analysis thread. The file is read again only when it changed."""

    def __init__(self, directory: Path, note: Note | None = None) -> None:
        self._directory = directory
        self._path = directory / STRATEGIES_FILE_NAME
        self._note = note
        self._lock = threading.Lock()
        self._stamp: float | None = None
        self._loaded = False
        self._settings = StrategySettings()
        self._strategies: list[Strategy] = []

    @property
    def settings(self) -> StrategySettings:
        self._reload()
        with self._lock:
            return self._settings

    def strategies(self) -> list[Strategy]:
        self._reload()
        with self._lock:
            return list(self._strategies)

    def filters(self) -> FilterSettings:
        return self.settings.filters

    def params(self, name: str) -> Mapping[str, Any]:
        for strategy in self.strategies():
            if strategy.name == name:
                return strategy.params.model_dump(mode="json")
        return {}

    def save(self, settings: StrategySettings) -> None:
        save_strategy_settings(self._directory, settings)
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
            settings = load_strategy_settings(self._directory)
            strategies, notes = build_strategies(settings)
            self._settings = settings
            self._strategies = strategies
        if self._note is not None:
            for text in notes:
                self._note(text)
