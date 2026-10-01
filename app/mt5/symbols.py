"""Find a broker's name for a symbol: brokers add suffixes such as `EURUSD.m` or `EURUSDm`."""

from __future__ import annotations

from collections.abc import Iterable


def resolve_symbol(wanted: str, available: Iterable[str]) -> str | None:
    """The exact name if it exists (any case), else the shortest name that starts with it."""
    target = wanted.strip().casefold()
    if not target:
        return None
    names = list(available)
    for name in names:
        if name.casefold() == target:
            return name
    candidates = [name for name in names if name.casefold().startswith(target)]
    return min(candidates, key=lambda name: (len(name), name)) if candidates else None
