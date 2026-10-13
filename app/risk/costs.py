"""Costs against the stop (0.51.1, PC log of 12 October 2026).

Three EURUSD trades with 3.6 to 5 pip stops lost about 25 USD each: the commission (about
6 USD a lot) was 11 to 14% of the money at risk per lot before the spread, so even a fair
strategy loses there. A trade whose commission is more than `MAX_COMMISSION_SHARE` of the
money at risk per lot (the loss at the SL plus the commission, as the sizing counts it) is
refused, with the numbers in the decision trace. Unknown numbers (no loss from MT5, no
commission in the history or the risk settings) never block. Pure.
"""

from __future__ import annotations

import math

from app.risk.limits import LIMIT_BLOCK, LimitCheck

MAX_COMMISSION_SHARE = 0.10


def commission_check(
    loss_per_lot: float | None,
    commission_per_lot: float,
    limit: float = MAX_COMMISSION_SHARE,
) -> LimitCheck | None:
    """The commission's share of the money at risk per lot, or None when it is unknown."""
    if loss_per_lot is None or not math.isfinite(loss_per_lot) or loss_per_lot <= 0:
        return None
    if not math.isfinite(commission_per_lot) or commission_per_lot <= 0 or limit <= 0:
        return None
    at_risk = loss_per_lot + commission_per_lot
    share = commission_per_lot / at_risk
    return LimitCheck(
        "commission vs stop",
        share <= limit + 1e-9,
        round(share, 3),
        limit,
        f"commission {commission_per_lot:,.2f} of {at_risk:,.2f} at risk per lot: the stop is "
        "too close for the costs",
        LIMIT_BLOCK,
    )
