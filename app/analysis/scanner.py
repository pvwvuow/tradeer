"""The opportunity scanner (spec C3): on every closed bar, rank symbols by setup state
("ready" before "forming") and then by probability x EV.

While no probability is known yet (no baseline, no model) the order inside a state is the
share of the strategy's rules that already pass.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

STATE_RANK: dict[str, int] = {"ready": 2, "forming": 1, "none": 0}


@dataclass(frozen=True)
class ScanEntry:
    symbol: str
    strategy: str
    state: str  # none, forming, ready
    passed: int
    total: int
    bar_time: int
    note: str = ""
    direction: str = ""
    probability: float | None = None
    expected_value: float | None = None

    @property
    def score(self) -> float | None:
        """Probability x EV (in R) when both are known."""
        if self.probability is None or self.expected_value is None:
            return None
        value = self.probability * self.expected_value
        return value if math.isfinite(value) else None

    @property
    def progress(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def key(self) -> tuple[int, float, float, str, str]:
        score = self.score
        return (
            -STATE_RANK.get(self.state, 0),
            -(score if score is not None else -math.inf),
            -self.progress,
            self.symbol,
            self.strategy,
        )


def rank(entries: Iterable[ScanEntry]) -> list[ScanEntry]:
    return sorted(entries, key=lambda entry: entry.key())
