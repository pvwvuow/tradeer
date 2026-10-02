"""Correlation matrix (spec C3) of hourly log returns over the bars all symbols share."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import reduce

import numpy as np

from app.analysis import indicators
from app.analysis.bars import Bars

CORRELATION_BARS = 120
MIN_RETURNS = 30


@dataclass(frozen=True)
class CorrelationMatrix:
    symbols: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    returns: int

    def value(self, first: str, second: str) -> float:
        try:
            return self.values[self.symbols.index(first)][self.symbols.index(second)]
        except ValueError:
            return math.nan

    def strong_pairs(self, threshold: float = 0.7) -> list[tuple[str, str, float]]:
        found: list[tuple[str, str, float]] = []
        for row, first in enumerate(self.symbols):
            for column in range(row + 1, len(self.symbols)):
                value = self.values[row][column]
                if math.isfinite(value) and abs(value) >= threshold:
                    found.append((first, self.symbols[column], value))
        return sorted(found, key=lambda item: -abs(item[2]))


def correlation_matrix(
    bars_by_symbol: Mapping[str, Bars],
    count: int = CORRELATION_BARS,
) -> CorrelationMatrix:
    symbols = tuple(bars_by_symbol)
    size = len(symbols)
    empty = tuple(tuple(math.nan for _ in symbols) for _ in symbols)
    if size < 2:
        return CorrelationMatrix(symbols, empty, 0)
    common = reduce(np.intersect1d, [bars.time for bars in bars_by_symbol.values()])
    common = common[-(count + 1) :]
    if len(common) <= MIN_RETURNS:
        return CorrelationMatrix(symbols, empty, max(len(common) - 1, 0))
    columns = []
    for bars in bars_by_symbol.values():
        positions = np.searchsorted(bars.time, common)
        columns.append(indicators.log_returns(bars.close[positions]))
    with np.errstate(divide="ignore", invalid="ignore"):
        matrix = np.corrcoef(np.vstack(columns))
    values = tuple(
        tuple(float(matrix[row, column]) for column in range(size)) for row in range(size)
    )
    return CorrelationMatrix(symbols, values, len(common) - 1)
