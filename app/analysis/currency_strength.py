"""Currency strength meter (spec C3): how far each currency moved against the others.

Every pair's move over the last bars, in ATR, counts for its base currency and against its
quote currency. A currency's strength is the average of its moves.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.analysis import indicators
from app.analysis.bars import Bars

STRENGTH_PAIRS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
)
LOOKBACK_BARS = 24


@dataclass(frozen=True)
class CurrencyStrength:
    currency: str
    score: float
    pairs: int
    rank: int

    def text(self) -> str:
        return f"{self.currency} {self.score:+.2f}"


def pair_move(bars: Bars, lookback: int = LOOKBACK_BARS) -> float:
    """The close-to-close move over `lookback` bars in units of ATR(14)."""
    if len(bars) <= max(lookback, 15):
        return math.nan
    atr = indicators.last(indicators.atr(bars.high, bars.low, bars.close))
    if not atr > 0:
        return math.nan
    return float((bars.close[-1] - bars.close[-1 - lookback]) / atr)


def currency_strength(
    bars_by_pair: Mapping[str, Bars],
    lookback: int = LOOKBACK_BARS,
) -> tuple[CurrencyStrength, ...]:
    """`bars_by_pair` is keyed by the six-letter pair name, for example "EURUSD"."""
    moves: dict[str, list[float]] = {}
    for pair, bars in bars_by_pair.items():
        if len(pair) < 6:
            continue
        move = pair_move(bars, lookback)
        if not math.isfinite(move):
            continue
        moves.setdefault(pair[:3], []).append(move)
        moves.setdefault(pair[3:6], []).append(-move)
    scores = sorted(
        ((currency, sum(values) / len(values), len(values)) for currency, values in moves.items()),
        key=lambda item: -item[1],
    )
    return tuple(
        CurrencyStrength(currency, score, count, rank)
        for rank, (currency, score, count) in enumerate(scores, start=1)
    )
