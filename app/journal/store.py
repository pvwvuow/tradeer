"""Journal entries (spec C12, table `journal`): the story, your notes, tags, a 1 to 5 rating
and, for manual trades, an emotion. One entry per trade (a stable id), synced like every row."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.journal.narrative import TradeEvent
from app.storage.ids import stable_id
from app.storage.repositories import Store
from app.storage.signal_store import epoch

EMOTIONS = ("", "calm", "confident", "hesitant", "fearful", "greedy", "frustrated", "bored")


class JournalError(ValueError):
    pass


@dataclass(frozen=True)
class JournalEntry:
    trade_id: str
    narrative: str = ""
    notes: str = ""
    tags: tuple[str, ...] = ()
    rating: int | None = None  # 1..5
    emotion: str = ""
    snapshots: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return journal_id(self.trade_id)


def journal_id(trade_id: str) -> str:
    return stable_id("journal", trade_id)


def parse_tags(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for part in text.replace(";", ",").split(","):
        tag = part.strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


class JournalRepository:
    def __init__(self, store: Store, account: Callable[[], str | None] | None = None) -> None:
        self.store = store
        self._account = account or (lambda: None)

    def entry(self, trade_id: str) -> JournalEntry | None:
        row = self.store.get("journal", journal_id(trade_id))
        if row is None:
            return None
        tags = _json(row.get("tags"), [])
        rating = row.get("rating")
        return JournalEntry(
            trade_id=trade_id,
            narrative=str(row.get("narrative") or ""),
            notes=str(row.get("notes") or ""),
            tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else (),
            rating=int(rating) if rating is not None else None,
            emotion=str(row.get("emotion") or ""),
            snapshots=dict(_json(row.get("snapshots"), {}) or {}),
        )

    def save(self, entry: JournalEntry) -> bool:
        if entry.rating is not None and not 1 <= entry.rating <= 5:
            raise JournalError("the rating is 1 to 5 stars")
        if entry.emotion not in EMOTIONS:
            raise JournalError(f"unknown emotion: {entry.emotion}")
        return self.store.upsert(
            "journal",
            {
                "id": entry.id,
                "account_id": self._account(),
                "trade_id": entry.trade_id,
                "narrative": entry.narrative,
                "notes": entry.notes,
                "tags": list(entry.tags),
                "rating": entry.rating,
                "emotion": entry.emotion,
                "snapshots": entry.snapshots,
            },
        )

    def tagged(self) -> dict[str, tuple[str, ...]]:
        """trade id -> tags, for filtering the journal list."""
        rows = self.store.db.query("SELECT trade_id, tags FROM journal")
        found: dict[str, tuple[str, ...]] = {}
        for row in rows:
            tags = _json(row["tags"], [])
            if isinstance(tags, list) and tags:
                found[str(row["trade_id"])] = tuple(str(tag) for tag in tags)
        return found

    def events(self, trade_id: str) -> list[TradeEvent]:
        rows = self.store.db.query(
            "SELECT time, type, old_value, new_value, reason FROM trade_events "
            "WHERE trade_id = ? ORDER BY time",
            (trade_id,),
        )
        return [
            TradeEvent(
                epoch(row["time"]),
                str(row["type"] or ""),
                str(row["old_value"] or ""),
                str(row["new_value"] or ""),
                str(row["reason"] or ""),
            )
            for row in rows
        ]


def snapshot_data(bars: Sequence[tuple[float, float, float, float, float]]) -> dict[str, Any]:
    """Chart data kept with an entry: (time, open, high, low, close) rows."""
    return {"bars": [[round(value, 6) for value in row] for row in bars]}
