"""Everything about one closed trade in plain text (spec F3 page 4, the detail drawer): the
story, the reasoning and features of its signal, the decision trace, the events timeline and
the journal notes. Pure; `SignalDetails` reads the signal parts from the database."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.analytics.trades import TradeRecord
from app.journal.narrative import TradeEvent, narrative
from app.journal.store import JournalEntry
from app.storage.repositories import Store

MAX_FEATURES = 40
MAX_STEPS = 60


@dataclass(frozen=True)
class SignalParts:
    features: Mapping[str, Any] = field(default_factory=dict)
    steps: tuple[Mapping[str, Any], ...] = ()


def _load(text: Any, default: Any) -> Any:
    if not isinstance(text, str) or not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class SignalDetails:
    def __init__(self, store: Store) -> None:
        self.store = store

    def parts(self, signal_id: str) -> SignalParts:
        if not signal_id:
            return SignalParts()
        signal = self.store.get("signals", signal_id)
        features = _load(signal.get("features_json"), {}) if signal is not None else {}
        rows = self.store.db.query(
            "SELECT steps_json FROM decision_traces WHERE signal_id = ? ORDER BY updated_at DESC",
            (signal_id,),
        )
        steps = _load(rows[0]["steps_json"], []) if rows else []
        kept = tuple(s for s in steps if isinstance(s, dict)) if isinstance(steps, list) else ()
        return SignalParts(features if isinstance(features, dict) else {}, kept)


def _clock(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S")


def _mark(passed: Any) -> str:
    return "\u2713" if passed is True else "\u2717" if passed is False else "\u00b7"


def detail_text(
    trade: TradeRecord,
    events: Sequence[TradeEvent] = (),
    parts: SignalParts | None = None,
    entry: JournalEntry | None = None,
) -> str:
    found = parts or SignalParts()
    lines = [narrative(trade, events), ""]
    lines.append(
        f"Strategy {trade.strategy}, {trade.source}, {trade.mode}; config {trade.config or 'none'}"
    )
    if found.features:
        shown = [
            f"  {name}: {value}"
            for name, value in sorted(found.features.items())
            if not str(name).startswith("ml.")
        ][:MAX_FEATURES]
        lines.append("Features at the signal:")
        lines.extend(shown)
    if found.steps:
        lines.append("Decision trace:")
        for step in found.steps[:MAX_STEPS]:
            value = step.get("value")
            text = f" = {value}" if value not in (None, "") else ""
            detail = f" ({step.get('detail')})" if step.get("detail") else ""
            name = f"{step.get('stage')}: {step.get('name')}"
            lines.append(f"  {_mark(step.get('passed'))} {name}{text}{detail}")
    if events:
        lines.append("Events:")
        lines.extend(
            f"  {_clock(e.time)} {e.type}"
            + (f" {e.old_value} -> {e.new_value}" if e.old_value or e.new_value else "")
            + (f" ({e.reason})" if e.reason else "")
            for e in events
        )
    if entry is not None and (entry.notes or entry.tags or entry.rating or entry.emotion):
        lines.append("Journal:")
        if entry.rating:
            lines.append(f"  rating {entry.rating}/5")
        if entry.tags:
            lines.append(f"  tags: {', '.join(entry.tags)}")
        if entry.emotion:
            lines.append(f"  emotion: {entry.emotion}")
        if entry.notes:
            lines.append(f"  {entry.notes}")
    return "\n".join(lines)
