"""The backtest's cost and account model (spec C8): spread, slippage, commission, swap, and
the money one lot makes, in the account currency.

MT5's `order_calc_profit` cannot be called for a past price, so the replay converts profit
the way MT5 does for the common cases: a quote currency equal to the account currency
(EURUSD, XAUUSD on a USD account) needs no conversion, a base currency equal to it (USDJPY on
a USD account) divides by the closing price, and any other pair uses a fixed rate the user
gives (account currency per 1 unit of the quote currency).
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from app.domain.signals import Direction
from app.mt5.models import SymbolSpec


class BacktestCosts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_balance: float = Field(default=10_000.0, gt=0, le=1e9, description="Start balance")
    account_currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="Account currency (3 letters)",
    )
    leverage: float = Field(default=100.0, gt=0, le=5000, description="Account leverage")
    min_spread_points: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description="Lowest spread used (points); MT5 bars store each bar's minimum spread",
    )
    extra_spread_points: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description="Added to every bar's spread (points)",
    )
    slippage_points: int = Field(
        default=1,
        ge=0,
        le=1000,
        description="Slippage on market, stop and stop-loss fills (points)",
    )
    commission_per_lot: float = Field(
        default=0.0,
        ge=0,
        le=1000,
        description="Commission per lot, round turn (account currency)",
    )
    swap_long_per_lot: float = Field(
        default=0.0,
        ge=-1000,
        le=1000,
        description="Swap per lot and night for buys (account currency, + or -)",
    )
    swap_short_per_lot: float = Field(
        default=0.0,
        ge=-1000,
        le=1000,
        description="Swap per lot and night for sells (account currency, + or -)",
    )
    triple_swap_weekday: int = Field(
        default=2,
        ge=0,
        le=6,
        description="Weekday of the triple swap (0 Monday .. 6 Sunday; 2 = Wednesday)",
    )
    quote_rate: float = Field(
        default=0.0,
        ge=0,
        le=1e6,
        description="Account currency per 1 quote currency for cross pairs (0 = not needed)",
    )

    def spread_points(self, bar_spread: float) -> float:
        base = bar_spread if math.isfinite(bar_spread) and bar_spread > 0 else 0.0
        return max(base, float(self.min_spread_points)) + self.extra_spread_points

    def swap_per_lot(self, direction: Direction) -> float:
        return self.swap_long_per_lot if direction is Direction.LONG else self.swap_short_per_lot


def currencies(spec: SymbolSpec) -> tuple[str, str]:
    base = (spec.currency_margin or spec.name[:3]).upper()
    quote = (spec.currency_profit or spec.name[3:6]).upper()
    return base, quote


def quote_to_account(spec: SymbolSpec, costs: BacktestCosts, price: float) -> float | None:
    """Account currency per 1 unit of the symbol's quote currency, or None if unknown."""
    account = costs.account_currency.upper()
    base, quote = currencies(spec)
    if quote == account:
        return 1.0
    if base == account:
        return 1.0 / price if price > 0 else None
    return costs.quote_rate if costs.quote_rate > 0 else None


def profit(
    spec: SymbolSpec,
    costs: BacktestCosts,
    direction: Direction,
    volume: float,
    price_open: float,
    price_close: float,
) -> float | None:
    """What MT5's `order_calc_profit` returns for these prices (account currency)."""
    contract = spec.contract_size if spec.contract_size > 0 else 100_000.0
    rate = quote_to_account(spec, costs, price_close)
    if rate is None or not math.isfinite(price_open) or not math.isfinite(price_close):
        return None
    return (price_close - price_open) * direction.sign * contract * volume * rate


def margin(
    spec: SymbolSpec,
    costs: BacktestCosts,
    volume: float,
    price: float,
) -> float | None:
    """The margin for `volume` lots with the account leverage (account currency)."""
    contract = spec.contract_size if spec.contract_size > 0 else 100_000.0
    account = costs.account_currency.upper()
    base, _ = currencies(spec)
    if base == account:
        return contract * volume / costs.leverage
    rate = quote_to_account(spec, costs, price)
    if rate is None:
        return None
    return contract * volume * price * rate / costs.leverage
