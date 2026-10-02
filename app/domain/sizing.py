"""Position sizing (spec C6). Pure: the caller asks MT5 for the loss of one lot at the stop.

lot = capital x risk% / (loss per lot at the SL + the expected commission per lot), rounded
**down** to the volume step and clamped to the symbol's maximum. When even the smallest lot
would risk more than allowed, the trade is rejected instead of rounded up. The loss per lot
comes from `order_calc_profit`, which converts JPY, gold and cross-currency profits to the
account currency correctly; the symbol's tick value is never used (ADR 60).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

VOLUME_EPSILON = 1e-9


@dataclass(frozen=True)
class VolumeRules:
    minimum: float
    maximum: float
    step: float

    @property
    def decimals(self) -> int:
        """Digits of the volume step: 0.01 -> 2, 0.1 -> 1, 1 -> 0."""
        if self.step <= 0 or not math.isfinite(self.step):
            return 2
        text = f"{self.step:.8f}".rstrip("0")
        return len(text.split(".")[1]) if "." in text else 0

    @property
    def valid(self) -> bool:
        values = (self.minimum, self.maximum, self.step)
        return all(math.isfinite(v) and v > 0 for v in values) and self.minimum <= self.maximum


def round_down(volume: float, step: float, decimals: int) -> float:
    """The largest multiple of `step` that is not above `volume`."""
    if not math.isfinite(volume) or volume <= 0:
        return 0.0
    steps = math.floor(volume / step + VOLUME_EPSILON)
    return round(steps * step, decimals)


@dataclass(frozen=True)
class SizingInput:
    capital: float  # equity or balance, in the account currency
    capital_basis: str  # "equity" or "balance"
    risk_percent: float
    loss_per_lot: float  # positive, account currency, from order_calc_profit
    commission_per_lot: float  # expected round-turn commission, account currency
    volume: VolumeRules
    currency: str


@dataclass(frozen=True)
class SizingResult:
    ok: bool
    volume: float
    allowed_risk: float  # capital x risk%
    risk_money: float  # volume x (loss per lot + commission): what the trade really risks
    reason: str
    lines: tuple[str, ...]  # the full calculation, for the decision trace and the log

    def text(self) -> str:
        return " | ".join(self.lines)


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def size_position(data: SizingInput) -> SizingResult:
    cur = data.currency
    allowed = data.capital * data.risk_percent / 100.0
    lines = [
        f"{data.capital_basis} {_money(data.capital, cur)} x {data.risk_percent:g}% = "
        f"{_money(allowed, cur)} allowed risk",
    ]

    def reject(reason: str) -> SizingResult:
        return SizingResult(False, 0.0, allowed, 0.0, reason, (*lines, f"rejected: {reason}"))

    if not math.isfinite(data.capital) or data.capital <= 0:
        return reject(f"no {data.capital_basis} to size from")
    if not math.isfinite(data.loss_per_lot) or data.loss_per_lot <= 0:
        return reject("MT5 could not calculate the loss per lot at the stop loss")
    if not data.volume.valid:
        return reject("the symbol's volume limits are unknown")
    commission = max(0.0, data.commission_per_lot if math.isfinite(data.commission_per_lot) else 0)
    per_lot = data.loss_per_lot + commission
    lines.append(
        f"loss per lot at SL {_money(data.loss_per_lot, cur)} (order_calc_profit) + commission "
        f"{_money(commission, cur)} = {_money(per_lot, cur)}",
    )
    raw = allowed / per_lot
    rules = data.volume
    volume = round_down(min(raw, rules.maximum), rules.step, rules.decimals)
    lines.append(
        f"{_money(allowed, cur)} / {_money(per_lot, cur)} = {raw:.4f} lots -> {volume:g} "
        f"(step {rules.step:g}, min {rules.minimum:g}, max {rules.maximum:g}, rounded down)",
    )
    if volume + VOLUME_EPSILON < rules.minimum:
        smallest = rules.minimum * per_lot
        return reject(
            f"the smallest lot {rules.minimum:g} would risk {_money(smallest, cur)}, more than "
            f"the allowed {_money(allowed, cur)}",
        )
    risk = volume * per_lot
    lines.append(f"risk {volume:g} x {_money(per_lot, cur)} = {_money(risk, cur)}")
    return SizingResult(True, volume, allowed, risk, "", tuple(lines))


def commission_per_lot(commissions: float, entry_volume: float) -> float | None:
    """The round-turn commission per lot from history: all commissions of a symbol's deals
    divided by the lots that were opened. None when there is no history."""
    if entry_volume <= VOLUME_EPSILON:
        return None
    return abs(commissions) / entry_volume
