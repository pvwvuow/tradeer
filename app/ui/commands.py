"""Command palette model: commands and a small, predictable fuzzy filter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    command_id: str
    title: str
    hint: str
    action: Callable[[], None]


def match_score(query: str, text: str) -> int | None:
    """Score a match (lower is better); None when the query characters are not in order."""
    needle = query.casefold().strip()
    haystack = text.casefold()
    if not needle or haystack.startswith(needle):
        return 0
    index = haystack.find(needle)
    if index >= 0:
        return 1 + index
    position = -1
    gaps = 0
    for char in needle:
        found = haystack.find(char, position + 1)
        if found < 0:
            return None
        gaps += found - position - 1
        position = found
    return 100 + gaps


def filter_commands(commands: Iterable[Command], query: str) -> list[Command]:
    scored: list[tuple[int, int, Command]] = []
    for order, command in enumerate(commands):
        score = match_score(query, f"{command.title} {command.hint}")
        if score is not None:
            scored.append((score, order, command))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [command for _, _, command in scored]
