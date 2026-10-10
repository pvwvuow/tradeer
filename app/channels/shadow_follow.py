"""Follow every channel signal's shadow on the price history (docs/SIGNAL_DESK.md 3.5).

Runs in a worker thread every few minutes: the signals whose shadow is not resolved yet are
re-read from their first text (with the channel's aliases), the history of each symbol is
loaded once (the finest bars there are, M1 first) and every leg is followed from the message
time plus the latency (`app.signals.shadow`). Reads and the local store only: nothing here
can trade.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from app.analysis.bars import Bars
from app.signals.parse import parse
from app.signals.shadow import HORIZON_SECONDS, ShadowResult, ShadowState, follow, legs_of
from app.storage.channel_store import ChannelRepository, ChannelSignal

KEEP_DAYS = 30  # signals older than this are not followed any more
BarSource = Callable[[str, float, float], tuple[Bars, float] | None]  # symbol, start, end
Log = Callable[[str, str], None]
UNREADABLE = ShadowResult(ShadowState.EXPIRED)


def _quiet(level: str, message: str) -> None:
    return None


def follow_signals(
    repository: ChannelRepository,
    bars: BarSource,
    now: float | None = None,
    log: Log = _quiet,
) -> int:
    """Update the shadow of every open signal; the number of legs saved."""
    moment = time.time() if now is None else now
    pending = repository.to_follow(moment - KEEP_DAYS * 86_400)
    by_symbol: dict[str, list[ChannelSignal]] = defaultdict(list)
    for signal in pending:
        if signal.symbol:
            by_symbol[signal.symbol].append(signal)
    saved = 0
    for symbol, signals in by_symbol.items():
        start = min(signal.date for signal in signals) - 3600
        end = min(moment, max(signal.date for signal in signals) + HORIZON_SECONDS)
        try:
            loaded = bars(symbol, start, end)
        except Exception as error:
            log("WARNING", f"Shadow results of {symbol}: no history ({type(error).__name__})")
            continue
        if loaded is None:
            continue
        history, point = loaded
        for signal in signals:
            saved += _follow_one(repository, signal, history, point, moment)
    return saved


def _follow_one(
    repository: ChannelRepository,
    signal: ChannelSignal,
    history: Bars,
    point: float,
    now: float,
) -> int:
    message = repository.message(signal.channel_id, signal.message_id)
    if message is None:
        return 0
    policy = repository.policy(signal.channel_id)
    parsed = parse(message.text, (), policy.aliases)
    start = signal.date + max(0.0, signal.latency)
    after = np.nonzero(history.time >= start)[0]
    if not len(after):
        return 0  # no bar after the message yet
    reference = float(history.open[int(after[0])])
    legs = legs_of(parsed, start, reference, policy.pending_minutes)
    if not legs:  # not readable as a whole signal: never followed again
        repository.save_shadow(signal.channel_id, signal.message_id, 0, UNREADABLE, now)
        return 0
    for number, leg in enumerate(legs, start=1):
        moved = replace(leg, close_at=signal.close_at, break_even_at=signal.break_even_at)
        repository.save_shadow(
            signal.channel_id,
            signal.message_id,
            number,
            follow(moved, history, point, now),
            now,
        )
    return len(legs)
