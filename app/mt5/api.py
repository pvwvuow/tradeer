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

# Order and position types (order_calc_profit, order_calc_margin, positions_get)
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

# Trade requests (order_send, order_check): action, filling, time, retcodes
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
ORDER_TIME_GTC = 0
ORDER_TIME_SPECIFIED = 2
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010

# Deals (history_deals_get): type, entry and reason
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_TYPE_BALANCE = 2
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3
DEAL_REASON_CLIENT = 0
DEAL_REASON_MOBILE = 1
DEAL_REASON_WEB = 2
DEAL_REASON_EXPERT = 3
DEAL_REASON_SL = 4
DEAL_REASON_TP = 5
DEAL_REASON_SO = 6

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
    """The package functions the app uses. Only `app/brokers/live_broker.py` and the trade test
    may call `order_send` and `order_check`; architecture tests keep them out of the rest."""

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

    def history_orders_get(self, *args: Any, **kwargs: Any) -> Any: ...

    def positions_total(self) -> int: ...

    def positions_get(self, *args: Any, **kwargs: Any) -> Any: ...

    def order_calc_profit(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None: ...

    def order_calc_margin(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
    ) -> float | None: ...

    def orders_get(self, *args: Any, **kwargs: Any) -> Any: ...

    def order_check(self, request: dict[str, Any]) -> Any: ...

    def order_send(self, request: dict[str, Any]) -> Any: ...
