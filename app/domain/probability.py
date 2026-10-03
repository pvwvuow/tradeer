"""Win probability and expected value at entry (spec C5, C10), before the ML model exists.

Until a model is trained (Phase 11) the estimate is the **baseline**: the strategy's
historical win rate with a Wilson confidence interval. With fewer than `min_samples`
resolved signals there is no honest number, so the estimate says "unknown" and why.
Probabilities are always estimates; the text shows the interval and the sample size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Z_95 = 1.959963984540054
BASELINE_MIN_SAMPLES = 30


def wilson_interval(wins: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for a win rate (95% by default)."""
    if total <= 0:
        return 0.0, 1.0
    rate = wins / total
    z2 = z * z
    centre = (rate + z2 / (2 * total)) / (1 + z2 / total)
    half = z * math.sqrt(rate * (1 - rate) / total + z2 / (4 * total * total)) / (1 + z2 / total)
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class ProbabilityEstimate:
    value: float | None
    low: float | None
    high: float | None
    samples: int
    source: str  # "baseline", or "none" while there is too little history
    min_samples: int = BASELINE_MIN_SAMPLES

    @property
    def known(self) -> bool:
        return self.value is not None

    def text(self) -> str:
        if self.value is None or self.low is None or self.high is None:
            return (
                f"unknown: {self.samples} of {self.min_samples} resolved signals needed for a "
                "baseline"
            )
        spread = (self.high - self.low) / 2 * 100
        return f"{self.value * 100:.0f}% \u00b1 {spread:.0f} (n = {self.samples}, {self.source})"


def baseline(wins: int, total: int, min_samples: int = BASELINE_MIN_SAMPLES) -> ProbabilityEstimate:
    """The strategy's historical win rate, or "unknown" below `min_samples`."""
    if total < min_samples or total <= 0:
        return ProbabilityEstimate(None, None, None, max(total, 0), "none", min_samples)
    low, high = wilson_interval(wins, total)
    return ProbabilityEstimate(wins / total, low, high, total, "baseline", min_samples)


def expected_value_r(probability: float, reward_r: float, cost_r: float = 0.0) -> float:
    """Expected result per trade in R: win `reward_r`, lose 1R, minus the costs in R."""
    return probability * reward_r - (1.0 - probability) - cost_r


def cost_in_r(spread_price: float, risk_price: float) -> float:
    """The spread paid on entry as a share of the stop distance (1R)."""
    if risk_price <= 0 or not math.isfinite(spread_price):
        return math.nan
    return max(spread_price, 0.0) / risk_price
