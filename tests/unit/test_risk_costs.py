"""A stop too close for the commission is refused (0.51.1)."""

import math

from app.risk.costs import commission_check


def test_the_losing_range_reversion_stops_are_refused() -> None:
    check = commission_check(36.0, 6.0)  # a 3.6 pip EURUSD stop, 6 USD a lot
    assert check is not None and not check.passed
    assert check.value == round(6 / 42, 3) and check.event


def test_a_normal_stop_passes() -> None:
    check = commission_check(150.0, 6.0)  # 15 pips
    assert check is not None and check.passed


def test_unknown_numbers_never_block() -> None:
    assert commission_check(None, 6.0) is None
    assert commission_check(math.nan, 6.0) is None
    assert commission_check(40.0, 0.0) is None
    assert commission_check(40.0, math.nan) is None
