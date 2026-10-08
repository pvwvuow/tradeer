"""What the AI Desk sends (AI Desk spec 3.1): one compact JSON text, pure and tested.

Stable parts first (playbook, track record) so a service's prompt cache can reuse them,
the market last. Times are ISO text, prices are rounded to each symbol's digits, the
account is in percent only, and free text (news titles, lessons) sits in `text` fields that
the prompt marks as data. When the text is too long the oldest bars go first, then the
daily bars, then the lessons with the least evidence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.analytics.llm_client import redact

CONTEXT_LIMIT = 12_000
BAR_COUNTS: dict[str, int] = {"M15": 60, "H1": 48, "H4": 30, "D1": 20}
SHRINK_STEPS: tuple[float, ...] = (1.0, 0.75, 0.5, 0.35, 0.25)


@dataclass(frozen=True)
class Bar:
    time: float  # open time, UTC seconds
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class NewsItem:
    time: float
    currency: str
    title: str


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    digits: int
    bid: float
    ask: float
    point: float
    stops_level: int = 0
    market_open: bool = True
    bars: Mapping[str, Sequence[Bar]] = field(default_factory=dict)  # closed bars only
    indicators: Mapping[str, float] = field(default_factory=dict)  # ATR_M15, EMA20_H1, ...
    card: Mapping[str, str] = field(default_factory=dict)  # trend, bias, structure, ...
    news: Sequence[NewsItem] = ()


@dataclass(frozen=True)
class Lesson:
    id: int
    text: str
    uses: int = 0
    average_r: float = 0.0


@dataclass(frozen=True)
class DeskInputs:
    now: float
    session: str
    minutes_to_session_change: int
    broker_offset_hours: float = 0.0
    account: Mapping[str, float] = field(default_factory=dict)  # percent values only
    symbols: Sequence[SymbolSnapshot] = ()
    positions: Sequence[Mapping[str, object]] = ()
    lessons: Sequence[Lesson] = ()
    track_record: Mapping[str, object] = field(default_factory=dict)


def iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%d %H:%M")


def _bars(rows: Sequence[Bar], count: int, digits: int) -> list[list[object]]:
    picked = list(rows)[-count:] if count > 0 else []
    return [
        [
            iso(bar.time),
            round(bar.open, digits),
            round(bar.high, digits),
            round(bar.low, digits),
            round(bar.close, digits),
        ]
        for bar in picked
    ]


def _symbol(snapshot: SymbolSnapshot, scale: float, daily: bool) -> dict[str, object]:
    digits = snapshot.digits
    spread = (snapshot.ask - snapshot.bid) / snapshot.point if snapshot.point > 0 else 0.0
    bars: dict[str, object] = {}
    for timeframe, wanted in BAR_COUNTS.items():
        if timeframe == "D1" and not daily:
            continue
        count = wanted if timeframe == "D1" else max(int(wanted * scale), 8)
        rows = snapshot.bars.get(timeframe, ())
        if rows:
            bars[timeframe] = _bars(rows, count, digits)
    indicators = {name: round(value, digits + 1) for name, value in snapshot.indicators.items()}
    return {
        "symbol": snapshot.symbol,
        "digits": digits,
        "bid": round(snapshot.bid, digits),
        "ask": round(snapshot.ask, digits),
        "spread_points": round(spread, 1),
        "stops_level_points": snapshot.stops_level,
        "market_open": snapshot.market_open,
        "indicators": indicators,
        "analysis": dict(snapshot.card),
        "news": [
            {"time": iso(item.time), "currency": item.currency, "text": item.title}
            for item in snapshot.news
        ],
        "bars_ohlc": bars,
    }


def _lessons(lessons: Sequence[Lesson], keep: int) -> list[dict[str, object]]:
    ranked = sorted(lessons, key=lambda item: (-item.uses, item.id))[:keep]
    return [
        {"id": item.id, "text": item.text, "uses": item.uses, "average_r": round(item.average_r, 2)}
        for item in sorted(ranked, key=lambda item: item.id)
    ]


def _document(inputs: DeskInputs, scale: float, daily: bool, keep: int) -> dict[str, object]:
    return {
        "playbook": _lessons(inputs.lessons, keep),
        "track_record": dict(inputs.track_record),
        "now": {
            "utc": iso(inputs.now),
            "broker_offset_hours": inputs.broker_offset_hours,
            "session": inputs.session,
            "minutes_to_session_change": inputs.minutes_to_session_change,
        },
        "account_percent": {name: round(value, 2) for name, value in inputs.account.items()},
        "symbols": [_symbol(item, scale, daily) for item in inputs.symbols],
        "positions": [dict(item) for item in inputs.positions],
    }


def _text(document: Mapping[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), default=str)


def build_context(inputs: DeskInputs, limit: int = CONTEXT_LIMIT) -> str:
    """The context JSON, masked, at most `limit` characters when it can be cut that far."""
    attempts: list[tuple[float, bool, int]] = [(scale, True, 12) for scale in SHRINK_STEPS]
    attempts += [(SHRINK_STEPS[-1], False, keep) for keep in (12, 8, 4, 0)]
    text = ""
    for scale, daily, keep in attempts:
        text = redact(_text(_document(inputs, scale, daily, keep)))
        if len(text) <= limit:
            return text
    return text
