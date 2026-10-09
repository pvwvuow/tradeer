"""The AI Lab's usage ledger (docs/NOCURVE_V2.md 20e3, the inspector's Usage panel).

One line per AI request in `ai_usage.jsonl` in the data folder: when, what kind (a chat turn
or an Ask AI request), the model, the input, cached and output tokens, the number of calls
and the cost (null when no price is set). No question, answer or key is written. The panel
sums the month, today against the daily cost cap, and the requests of the last 7 days.
Lines older than `KEEP_DAYS` are dropped when the file is rewritten; a broken line is
skipped, never fatal.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

USAGE_FILE = "ai_usage.jsonl"
KEEP_DAYS = 400
DAY = 86_400
COMPACT_EVERY = 500  # rewrite the file (dropping old lines) after this many new lines
WEEKDAYS = ("M", "T", "W", "T", "F", "S", "S")
WEEKDAYS_FA = ("د", "س", "چ", "پ", "ج", "ش", "ی")


@dataclass(frozen=True)
class UsageRecord:
    time: float
    kind: str  # "chat" or "ask"
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    calls: int = 1
    cost: float = math.nan  # USD; NaN when no price is set

    def to_json(self) -> dict[str, Any]:
        return {
            "time": round(self.time, 3),
            "kind": self.kind,
            "model": self.model,
            "input": self.input_tokens,
            "cached": self.cached_tokens,
            "output": self.output_tokens,
            "calls": self.calls,
            "cost": round(self.cost, 6) if math.isfinite(self.cost) else None,
        }


def _int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(int(value), 0)


def record_from(data: Any) -> UsageRecord | None:
    if not isinstance(data, dict):
        return None
    moment = data.get("time")
    if isinstance(moment, bool) or not isinstance(moment, int | float):
        return None
    cost = data.get("cost")
    price = float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None
    model = data.get("model")
    kind = data.get("kind")
    return UsageRecord(
        time=float(moment),
        kind=kind if isinstance(kind, str) else "chat",
        model=model if isinstance(model, str) else "",
        input_tokens=_int(data, "input"),
        cached_tokens=_int(data, "cached"),
        output_tokens=_int(data, "output"),
        calls=_int(data, "calls") or 1,
        cost=price if price is not None and math.isfinite(price) else math.nan,
    )


@dataclass(frozen=True)
class UsageSummary:
    month_input: int
    month_output: int
    month_cost: float  # the known costs only
    month_requests: int
    unknown_cost: int  # requests of the month without a price
    today_cost: float
    days: tuple[tuple[str, int], ...]  # the last 7 UTC days, oldest first: (weekday, requests)
    model: str  # the newest request's model

    @property
    def busiest(self) -> int:
        return max((count for _, count in self.days), default=0)


def _day(moment: float) -> datetime:
    found = datetime.fromtimestamp(moment, UTC)
    return found.replace(hour=0, minute=0, second=0, microsecond=0)


def summarize(records: Sequence[UsageRecord], now: float, fa: bool = False) -> UsageSummary:
    today = _day(now)
    month = today.replace(day=1)
    mine = [record for record in records if record.time <= now + 1]
    monthly = [record for record in mine if datetime.fromtimestamp(record.time, UTC) >= month]
    known = [record.cost for record in monthly if math.isfinite(record.cost)]
    daily = [record for record in mine if _day(record.time) == today]
    names = WEEKDAYS_FA if fa else WEEKDAYS
    days: list[tuple[str, int]] = []
    for back in range(6, -1, -1):
        day = today - timedelta(days=back)
        count = sum(1 for record in mine if _day(record.time) == day)
        days.append((names[day.weekday()], count))
    newest = max(mine, key=lambda record: record.time, default=None)
    return UsageSummary(
        month_input=sum(record.input_tokens for record in monthly),
        month_output=sum(record.output_tokens for record in monthly),
        month_cost=sum(known),
        month_requests=len(monthly),
        unknown_cost=len(monthly) - len(known),
        today_cost=sum(record.cost for record in daily if math.isfinite(record.cost)),
        days=tuple(days),
        model=newest.model if newest is not None else "",
    )


def short_tokens(count: int) -> str:
    """128400 -> 128.4k, 2100000 -> 2.1M, 950 -> 950."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


class UsageStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._added = 0

    @property
    def path(self) -> Path:
        return self.folder / USAGE_FILE

    def add(self, record: UsageRecord) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_json(), ensure_ascii=False) + "\n"
        with self.path.open("ab") as file:
            file.write(line.encode("utf-8"))
        self._added += 1
        if self._added >= COMPACT_EVERY:
            self.compact(record.time)

    def records(self) -> list[UsageRecord]:
        try:
            raw = self.path.read_bytes().decode("utf-8", "replace")
        except OSError:
            return []
        found: list[UsageRecord] = []
        for line in raw.splitlines():
            try:
                record = record_from(json.loads(line))
            except ValueError:
                continue
            if record is not None:
                found.append(record)
        return found

    def compact(self, now: float) -> int:
        """Drop the lines older than `KEEP_DAYS`; the number kept."""
        kept = [record for record in self.records() if record.time >= now - KEEP_DAYS * DAY]
        temporary = self.path.with_suffix(".tmp")
        text = "".join(json.dumps(item.to_json(), ensure_ascii=False) + "\n" for item in kept)
        temporary.write_bytes(text.encode("utf-8"))
        os.replace(temporary, self.path)
        self._added = 0
        return len(kept)

    def summary(self, now: float, fa: bool = False) -> UsageSummary:
        return summarize(self.records(), now, fa)
