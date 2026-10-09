"""One bet per currency and direction (8 October 2026).

The London breakout sold EURUSD and GBPUSD a minute apart: two trades, one idea (a stronger
dollar), and both stopped out when the dollar turned. A forex symbol is two legs: buying
EURUSD is long EUR and short USD. Two trades on different symbols are the same bet when they
share a leg with the same sign (EURUSD sell and GBPUSD sell are both long USD).
"""

from __future__ import annotations

from app.domain.signals import Direction

CURRENCIES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "AUD",
        "NZD",
        "CAD",
        "SEK",
        "NOK",
        "DKK",
        "SGD",
        "HKD",
        "PLN",
        "MXN",
        "ZAR",
        "TRY",
        "CNH",
        "XAU",
        "XAG",
    },
)


def legs(symbol: str) -> tuple[str, str] | None:
    """Base and quote currency of a broker symbol ("EURUSD.a" -> EUR, USD), None if unknown."""
    letters = "".join(char for char in symbol.upper() if char.isalpha())
    base, quote = letters[:3], letters[3:6]
    if base in CURRENCIES and quote in CURRENCIES and base != quote:
        return base, quote
    return None


def exposure(symbol: str, direction: Direction) -> dict[str, int]:
    """Currency -> +1 (long) or -1 (short) for a trade, empty for a non-currency symbol."""
    found = legs(symbol)
    if found is None:
        return {}
    sign = 1 if direction is Direction.LONG else -1
    return {found[0]: sign, found[1]: -sign}


def shared_bet(
    symbol: str,
    direction: Direction,
    other_symbol: str,
    other_direction: Direction,
) -> str:
    """'long USD' when the two trades bet the same way on a currency, '' otherwise."""
    if symbol == other_symbol:
        return ""
    mine = exposure(symbol, direction)
    theirs = exposure(other_symbol, other_direction)
    for currency, sign in mine.items():
        if theirs.get(currency) == sign:
            return f"{'long' if sign > 0 else 'short'} {currency}"
    return ""
