"""What an AI call costs and the daily caps (AI Desk spec 8.2).

The cost comes from the `usage` the service returns; reasoning tokens are paid as output.
Before a call the caps are checked with its worst case (the input estimate plus the largest
answer), so a single call can never jump over the daily cost cap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

MILLION = 1_000_000.0
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Prices:
    input_price: float = 0.0  # USD per 1M tokens
    cached_input_price: float = 0.0  # 0 = the input price
    output_price: float = 0.0

    @property
    def known(self) -> bool:
        return self.input_price > 0.0 or self.output_price > 0.0


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0  # part of the input tokens
    output_tokens: int = 0  # paid output, reasoning included
    reasoning_tokens: int = 0  # part of the output tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cached_tokens + other.cached_tokens,
            self.output_tokens + other.output_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
        )


def call_cost(prices: Prices, usage: Usage) -> float:
    """USD; 0.0 when no price is set (the page then says "cost unknown")."""
    if not prices.known:
        return 0.0
    cached_price = prices.cached_input_price or prices.input_price
    cached = min(max(usage.cached_tokens, 0), max(usage.input_tokens, 0))
    fresh = max(usage.input_tokens, 0) - cached
    total = (
        fresh * prices.input_price
        + cached * cached_price
        + max(usage.output_tokens, 0) * prices.output_price
    )
    return total / MILLION


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def worst_case(prices: Prices, prompt_chars: int, max_output_tokens: int) -> float:
    tokens = math.ceil(max(prompt_chars, 0) / CHARS_PER_TOKEN)
    return call_cost(prices, Usage(input_tokens=tokens, output_tokens=max_output_tokens))


def utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")


@dataclass
class Budget:
    """Today's spending (UTC day) against the daily cost cap and call cap."""

    cost_cap: float = 1.0
    call_cap: int = 60
    day: str = ""
    spent: float = 0.0
    calls: int = 0

    def _roll(self, now: float) -> None:
        today = utc_day(now)
        if today != self.day:
            self.day = today
            self.spent = 0.0
            self.calls = 0

    def check(self, worst: float, now: float) -> str:
        """Why the next call must wait, or "" when it fits under both caps."""
        self._roll(now)
        if self.calls >= self.call_cap:
            return f"Daily call cap reached ({self.calls} of {self.call_cap} calls)."
        if self.spent + max(worst, 0.0) > self.cost_cap + 1e-12:
            return f"Daily cost cap reached (${self.spent:.2f} of ${self.cost_cap:.2f})."
        return ""

    def record(self, cost: float, now: float) -> None:
        self._roll(now)
        self.calls += 1
        if math.isfinite(cost) and cost > 0.0:
            self.spent += cost

    def left(self, now: float) -> float:
        self._roll(now)
        return max(self.cost_cap - self.spent, 0.0)
