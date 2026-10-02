"""The watchlist (spec F3): up to 10 symbols, saved per profile as `watchlist.json`."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

WATCHLIST_FILE_NAME = "watchlist.json"
MAX_SYMBOLS = 10
DEFAULT_SYMBOLS: tuple[str, ...] = ("EURUSD", "GBPUSD", "XAUUSD")


def clean_symbols(values: Iterable[str]) -> list[str]:
    """Upper case, no blanks, no repeats, at most MAX_SYMBOLS."""
    found: list[str] = []
    for value in values:
        name = "".join(str(value).split()).upper()
        if name and name not in found:
            found.append(name)
    return found[:MAX_SYMBOLS]


class Watchlist(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbols: list[str] = list(DEFAULT_SYMBOLS)

    @field_validator("symbols")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return clean_symbols(value)

    def with_symbol(self, name: str) -> Watchlist:
        return Watchlist(symbols=[*self.symbols, name])

    def without(self, name: str) -> Watchlist:
        return Watchlist(symbols=[symbol for symbol in self.symbols if symbol != name.upper()])


def load_watchlist(directory: Path) -> Watchlist:
    try:
        raw = json.loads((directory / WATCHLIST_FILE_NAME).read_text(encoding="utf-8"))
        return Watchlist.model_validate(raw)
    except (OSError, ValueError):
        return Watchlist()


def save_watchlist(directory: Path, watchlist: Watchlist) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / WATCHLIST_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(watchlist.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class WatchlistSource:
    """The saved watchlist, read again only when its file changed (polled every cycle)."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / WATCHLIST_FILE_NAME
        self._directory = directory
        self._stamp: float | None = None
        self._symbols: list[str] = list(DEFAULT_SYMBOLS)

    def __call__(self) -> list[str]:
        try:
            stamp: float | None = self._path.stat().st_mtime
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._stamp = stamp
            self._symbols = load_watchlist(self._directory).symbols
        return list(self._symbols)
