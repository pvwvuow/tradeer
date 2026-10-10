"""One position per target (docs/SIGNAL_DESK.md 2.4).

The lots from the risk manager are split evenly over the targets and rounded down to the
volume step; the first legs get the step left over. When a leg would be under the minimum
lot, the last leg is merged away (its target is dropped) until every leg fits, so a small
account still copies the signal with fewer targets instead of breaking a limit.
"""

from __future__ import annotations

import math

EPSILON = 1e-9


def split(total: float, count: int, min_lot: float, step: float) -> tuple[float, ...]:
    """The lots of each leg, at most `count` legs; empty when even one leg is too small."""
    if count <= 0 or step <= 0 or total <= 0:
        return ()
    units = math.floor(total / step + EPSILON)
    least = max(1, math.ceil(min_lot / step - EPSILON))
    if units < least:
        return ()
    legs = count
    while legs > 1 and units // legs < least:
        legs -= 1
    base, extra = divmod(units, legs)
    decimals = max(0, -math.floor(math.log10(step) + EPSILON))
    return tuple(round((base + (1 if at < extra else 0)) * step, decimals) for at in range(legs))
