"""A signal source's own budget (docs/SIGNAL_DESK.md 3.4, phase 21d). Pure.

A Telegram channel may risk only its share of the account: its risk per trade (2% by
default) of its own equity, the budget the user gave it plus what its trades made or lost.
The Signal desk puts that money on every leg (`budget_features`); the risk manager sizes
the trade with the smaller of the account's risk per trade and this money, so the account
limits always apply on top of the channel's. When even the smallest lot would risk more than
the channel may, the card says so with both numbers and the trade is refused.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

BUDGET_RISK = "budget_risk"  # money this source may risk on one trade
BUDGET_EQUITY = "budget_equity"  # the source's equity: its budget plus its closed result
BUDGET_PERCENT = "budget_percent"  # the source's risk per trade, % of its equity


@dataclass(frozen=True)
class Budget:
    money: float
    equity: float
    percent: float


def budget_features(budget: Budget | None) -> dict[str, float | str]:
    if budget is None:
        return {}
    return {
        BUDGET_RISK: round(budget.money, 2),
        BUDGET_EQUITY: round(budget.equity, 2),
        BUDGET_PERCENT: round(budget.percent, 4),
    }


def _number(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return math.nan
    try:
        return float(raw)
    except ValueError:
        return math.nan


def budget_of(features: Mapping[str, float | str]) -> Budget | None:
    """The budget a signal carries, None for a signal without one (or a broken one)."""
    if BUDGET_RISK not in features:
        return None
    money = _number(features.get(BUDGET_RISK))
    equity = _number(features.get(BUDGET_EQUITY))
    percent = _number(features.get(BUDGET_PERCENT))
    if not math.isfinite(money):
        return Budget(0.0, 0.0, 0.0)  # a budget that cannot be read allows nothing
    return Budget(
        max(0.0, money),
        equity if math.isfinite(equity) else 0.0,
        percent if math.isfinite(percent) else 0.0,
    )


def capped_percent(account_percent: float, budget: Budget | None, capital: float) -> float:
    """The risk per trade in % of the account: the account's, or less when the source's
    money is smaller."""
    if budget is None or not (math.isfinite(capital) and capital > 0):
        return account_percent
    return min(account_percent, budget.money / capital * 100.0)


def small_budget_reason(
    budget: Budget,
    smallest_money: float,
    min_lot: float,
    currency: str,
) -> str:
    """Why the smallest lot is too big for this source, "" when it is not."""
    if not math.isfinite(smallest_money) or smallest_money <= budget.money:
        return ""
    share = smallest_money / budget.equity * 100.0 if budget.equity > 0 else math.inf
    part = f"{share:.1f}% of this channel" if math.isfinite(share) else "more than this channel has"
    return (
        f"budget too small: the smallest lot {min_lot:g} risks {smallest_money:,.2f} {currency}"
        f" = {part} (its limit is {budget.percent:g}% = {budget.money:,.2f} {currency}); "
        "raise the channel's budget or its risk"
    )
