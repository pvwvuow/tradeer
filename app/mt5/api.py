"""The part of the `MetaTrader5` package this app uses, plus the constants it needs.

The constant values are copied from the MetaTrader5 documentation so that pure code never has
to import the package. `tests/unit/test_mt5_api.py` compares them with the real package on
Windows.
"""

from __future__ import annotations

from typing import Any, Protocol

# Timeframes
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408

TIMEFRAMES: dict[str, int] = {
    "M1": TIMEFRAME_M1,
    "M5": TIMEFRAME_M5,
    "M15": TIMEFRAME_M15,
    "M30": TIMEFRAME_M30,
    "H1": TIMEFRAME_H1,
    "H4": TIMEFRAME_H4,
    "D1": TIMEFRAME_D1,
}

# account_info().trade_mode
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

# account_info().margin_mode
ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 0
ACCOUNT_MARGIN_MODE_EXCHANGE = 1
ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2

# account_info().margin_so_mode
ACCOUNT_STOPOUT_MODE_PERCENT = 0
ACCOUNT_STOPOUT_MODE_MONEY = 1

# symbol_info().trade_mode
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_LONGONLY = 1
SYMBOL_TRADE_MODE_SHORTONLY = 2
SYMBOL_TRADE_MODE_CLOSEONLY = 3
SYMBOL_TRADE_MODE_FULL = 4

# last_error() codes
RES_S_OK = 1
RES_E_FAIL = -1
RES_E_INVALID_PARAMS = -2
RES_E_NO_MEMORY = -3
RES_E_NOT_FOUND = -4
RES_E_INVALID_VERSION = -5
RES_E_AUTH_FAILED = -6
RES_E_UNSUPPORTED = -7
RES_E_AUTO_TRADING_DISABLED = -8
RES_E_INTERNAL_FAIL = -10000
RES_E_INTERNAL_FAIL_SEND = -10001
RES_E_INTERNAL_FAIL_RECEIVE = -10002
RES_E_INTERNAL_FAIL_INIT = -10003
RES_E_INTERNAL_FAIL_CONNECT = -10004
RES_E_INTERNAL_FAIL_TIMEOUT = -10005


class MT5Api(Protocol):
    """Read-only functions used in Phase 3. Order functions arrive with execution (Phase 8)."""

    def initialize(self, *args: Any, **kwargs: Any) -> bool: ...

    def login(self, *args: Any, **kwargs: Any) -> bool: ...

    def shutdown(self) -> Any: ...

    def last_error(self) -> tuple[int, str]: ...

    def version(self) -> Any: ...

    def terminal_info(self) -> Any: ...

    def account_info(self) -> Any: ...

    def symbols_total(self) -> int: ...

    def symbols_get(self, *args: Any, **kwargs: Any) -> Any: ...

    def symbol_info(self, symbol: str) -> Any: ...

    def symbol_info_tick(self, symbol: str) -> Any: ...

    def symbol_select(self, symbol: str, enable: bool = True) -> bool: ...

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> Any: ...

    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any: ...

    def positions_total(self) -> int: ...
