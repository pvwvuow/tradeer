"""Currency exposure (spec C6). Pure.

Every position is long its base currency and short its quote currency, weighted by the money
it risks: long EURUSD + long GBPUSD risking 0.5% each is +0.5% EUR, +0.5% GBP and -1.0% USD,
so a third USD-short trade can be blocked although each symbol alone looks fine. Gold is
XAU against USD the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.signals import Direction


@dataclass(frozen=True)
class ExposureItem:
    base: str
    quote: str
    direction: Direction
    risk_money: float


def currency_exposure(items: Iterable[ExposureItem]) -> dict[str, float]:
    """Net risk money per currency: positive = long the currency, negative = short."""
    totals: dict[str, float] = {}
    for item in items:
        signed = item.risk_money * item.direction.sign
        if item.base:
            totals[item.base] = totals.get(item.base, 0.0) + signed
        if item.quote:
            totals[item.quote] = totals.get(item.quote, 0.0) - signed
    return totals


def as_percent(totals: Mapping[str, float], capital: float) -> dict[str, float]:
    if capital <= 0:
        return {name: 0.0 for name in totals}
    return {name: value / capital * 100.0 for name, value in totals.items()}


def exposure_text(percents: Mapping[str, float]) -> str:
    shown = [
        f"{'long' if value > 0 else 'short'} {name} {abs(value):.2f}%"
        for name, value in sorted(percents.items(), key=lambda item: -abs(item[1]))
        if abs(value) >= 0.005
    ]
    return ", ".join(shown) or "none"
