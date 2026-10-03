"""Closed bars of one symbol and timeframe as numpy columns, with times in UTC seconds."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

TF_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}
ANALYSIS_TIMEFRAMES: tuple[str, ...] = ("M5", "M15", "H1", "H4", "D1")


def _floats(values: object) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _ints(values: object) -> IntArray:
    return np.asarray(values, dtype=np.int64)


@dataclass(frozen=True, eq=False)
class Bars:
    """Bar open times are UTC (`time`) and broker server time (`server_time`)."""

    symbol: str
    timeframe: str
    time: IntArray
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    volume: IntArray = field(default_factory=lambda: _ints([]))
    spread: IntArray = field(default_factory=lambda: _ints([]))
    server_time: IntArray = field(default_factory=lambda: _ints([]))

    @classmethod
    def build(
        cls,
        symbol: str,
        timeframe: str,
        *,
        time: object,
        open: object,
        high: object,
        low: object,
        close: object,
        volume: object | None = None,
        spread: object | None = None,
        server_time: object | None = None,
    ) -> Bars:
        times = _ints(time)
        size = len(times)
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            time=times,
            open=_floats(open),
            high=_floats(high),
            low=_floats(low),
            close=_floats(close),
            volume=_ints(volume) if volume is not None else np.ones(size, dtype=np.int64),
            spread=_ints(spread) if spread is not None else np.zeros(size, dtype=np.int64),
            server_time=_ints(server_time) if server_time is not None else times.copy(),
        )

    @classmethod
    def empty(cls, symbol: str, timeframe: str) -> Bars:
        return cls.build(symbol, timeframe, time=[], open=[], high=[], low=[], close=[])

    def __len__(self) -> int:
        return len(self.time)

    @property
    def seconds(self) -> int:
        return TF_SECONDS[self.timeframe]

    @property
    def last_close(self) -> float:
        return float(self.close[-1])

    def tail(self, count: int) -> Bars:
        return self.slice(max(len(self) - count, 0), len(self))

    def slice(self, start: int, stop: int) -> Bars:
        return Bars(
            self.symbol,
            self.timeframe,
            self.time[start:stop],
            self.open[start:stop],
            self.high[start:stop],
            self.low[start:stop],
            self.close[start:stop],
            self.volume[start:stop],
            self.spread[start:stop],
            self.server_time[start:stop],
        )

    def since(self, utc_time: int) -> Bars:
        start = int(np.searchsorted(self.time, utc_time, side="left"))
        return self.slice(start, len(self))
