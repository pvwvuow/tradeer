"""Trade server return codes (the `retcode` of a sent order) and what the app does with each
(spec C7).

Retry only where a new attempt with fresh prices is safe: requote, price changed, off quotes,
timeout, too many requests, connection. Never retry invalid stops, no money, trading disabled
or a closed market: those need a person, and retrying them only spams the trade server.

A call that returns nothing has no return code, only MT5's `last_error()`: `no_result_text`
says what that error means and what to change (PC log of 5 October 2026).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Policy(StrEnum):
    DONE = "done"  # the deal was made
    PLACED = "placed"  # the pending order was placed
    PARTIAL = "partial"  # only part of the volume was filled
    RETRY = "retry"  # safe to send again with fresh prices
    FAIL = "fail"  # do not send again


@dataclass(frozen=True)
class Retcode:
    code: int
    name: str
    text: str
    policy: Policy

    @property
    def ok(self) -> bool:
        return self.policy in (Policy.DONE, Policy.PLACED, Policy.PARTIAL)


_TABLE: tuple[tuple[int, str, str, Policy], ...] = (
    (10004, "REQUOTE", "the broker offered a new price (requote)", Policy.RETRY),
    (10006, "REJECT", "the request was rejected", Policy.FAIL),
    (10007, "CANCEL", "the request was cancelled by the trader", Policy.FAIL),
    (10008, "PLACED", "the order was placed", Policy.PLACED),
    (10009, "DONE", "the request was completed", Policy.DONE),
    (10010, "DONE_PARTIAL", "only part of the volume was filled", Policy.PARTIAL),
    (10011, "ERROR", "the trade server reported an error", Policy.FAIL),
    (10012, "TIMEOUT", "the request timed out", Policy.RETRY),
    (10013, "INVALID", "the request is invalid", Policy.FAIL),
    (10014, "INVALID_VOLUME", "the volume is invalid", Policy.FAIL),
    (10015, "INVALID_PRICE", "the price is invalid", Policy.FAIL),
    (10016, "INVALID_STOPS", "the stop loss or take profit is invalid", Policy.FAIL),
    (10017, "TRADE_DISABLED", "trading is disabled", Policy.FAIL),
    (10018, "MARKET_CLOSED", "the market is closed", Policy.FAIL),
    (10019, "NO_MONEY", "there is not enough money", Policy.FAIL),
    (10020, "PRICE_CHANGED", "the price changed", Policy.RETRY),
    (10021, "PRICE_OFF", "there are no quotes (off quotes)", Policy.RETRY),
    (10022, "INVALID_EXPIRATION", "the order expiration is invalid", Policy.FAIL),
    (10023, "ORDER_CHANGED", "the order state changed", Policy.FAIL),
    (10024, "TOO_MANY_REQUESTS", "too many requests", Policy.RETRY),
    (10025, "NO_CHANGES", "the request changes nothing", Policy.FAIL),
    (10026, "SERVER_DISABLES_AT", "the server disabled algo trading", Policy.FAIL),
    (10027, "CLIENT_DISABLES_AT", "Algo Trading is off in the terminal", Policy.FAIL),
    (10028, "LOCKED", "the request is locked for processing", Policy.FAIL),
    (10029, "FROZEN", "the order or position is frozen (freeze level)", Policy.FAIL),
    (10030, "INVALID_FILL", "the filling mode is not supported", Policy.FAIL),
    (10031, "CONNECTION", "no connection to the trade server", Policy.RETRY),
    (10032, "ONLY_REAL", "allowed for real accounts only", Policy.FAIL),
    (10033, "LIMIT_ORDERS", "the limit of pending orders is reached", Policy.FAIL),
    (10034, "LIMIT_VOLUME", "the volume limit for the symbol is reached", Policy.FAIL),
    (10035, "INVALID_ORDER", "the order type is invalid or not allowed", Policy.FAIL),
    (10036, "POSITION_CLOSED", "the position is already closed", Policy.FAIL),
    (10038, "INVALID_CLOSE_VOLUME", "the close volume is more than the position", Policy.FAIL),
    (10039, "CLOSE_ORDER_EXIST", "a close order for the position already exists", Policy.FAIL),
    (10040, "LIMIT_POSITIONS", "the limit of open positions is reached", Policy.FAIL),
    (10041, "REJECT_CANCEL", "the pending order activation was rejected", Policy.FAIL),
    (10042, "LONG_ONLY", "only long positions are allowed", Policy.FAIL),
    (10043, "SHORT_ONLY", "only short positions are allowed", Policy.FAIL),
    (10044, "CLOSE_ONLY", "only closing is allowed", Policy.FAIL),
    (10045, "FIFO_CLOSE", "positions must be closed in FIFO order", Policy.FAIL),
)

RETCODES: dict[int, Retcode] = {row[0]: Retcode(*row) for row in _TABLE}
RETRY_CODES = frozenset(code for code, item in RETCODES.items() if item.policy is Policy.RETRY)

ALGO_TRADING_FIX = "Press Algo Trading in the MT5 toolbar so it turns green."
PYTHON_API_FIX = (
    'In MT5 open Tools > Options > Expert Advisors and untick "Disable automatic trading '
    'through the external Python API".'
)
LINK_FAILED = "the link to the MT5 terminal failed (the app reconnects by itself)"

# `last_error()` codes (RES_E_*): what they mean when a trade call returned nothing.
_LAST_ERRORS: dict[int, str] = {
    -1: "the terminal could not run the request",
    -2: "MT5 refused a field of the request (an app bug: please send the log)",
    -3: "the terminal ran out of memory",
    -4: "MT5 did not find it",
    -5: "the MetaTrader5 package does not fit this terminal: update MT5",
    -6: "the login was refused",
    -7: "this terminal does not support the call",
    -8: f"MT5 does not allow trading from the app. {ALGO_TRADING_FIX} {PYTHON_API_FIX}",
}


def describe(code: int | None) -> Retcode:
    """The table entry, or an unknown code that must not be retried."""
    if code is None:
        return Retcode(0, "NO_RESULT", "MT5 returned no result", Policy.FAIL)
    found = RETCODES.get(int(code))
    if found is not None:
        return found
    return Retcode(int(code), f"CODE_{int(code)}", f"unknown return code {code}", Policy.FAIL)


def retcode_text(code: int | None) -> str:
    item = describe(code)
    return f"{item.code} {item.name}: {item.text}"


def explain_last_error(last_error: str) -> str:
    """`last_error()` as text ("-8 Terminal: Autotrading disabled") in plain words, or ""."""
    head = last_error.strip().split(" ", 1)[0]
    try:
        code = int(head)
    except ValueError:
        return ""
    if code <= -10000:
        return LINK_FAILED
    return _LAST_ERRORS.get(code, "")


def no_result_text(last_error: str) -> str:
    """The text of a call that returned nothing, with MT5's last error and what it means."""
    base = retcode_text(None)
    if not last_error:
        return base
    hint = explain_last_error(last_error)
    extra = f": {hint}" if hint else ""
    return f"{base} (last error {last_error}{extra})"
