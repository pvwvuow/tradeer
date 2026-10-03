"""A tiny stand-in for the `MetaTrader5` package, loaded inside the MT5 helper process.

Module-level functions like the real package, with named-tuple results, a numpy rate array
and calls that hang, fail or end the process, for the helper-process tests.
"""

from __future__ import annotations

import os
import time
from collections import namedtuple
from typing import Any

import numpy as np

__version__ = "5.0.test"

AccountInfo = namedtuple("AccountInfo", ["login", "balance", "currency", "server"])
SymbolInfo = namedtuple("SymbolInfo", ["name", "digits", "point", "visible"])
TradeRequest = namedtuple("TradeRequest", ["symbol", "volume"])
CheckResult = namedtuple("CheckResult", ["retcode", "comment", "request"])

RATES = np.array(
    [(1_700_000_000, 1.1, 1.2, 1.0, 1.15, 10, 2, 0)],
    dtype=[
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ],
)

_state = {"initialized": False}


def initialize(*args: Any, **kwargs: Any) -> bool:
    _state["initialized"] = True
    return True


def shutdown() -> None:
    _state["initialized"] = False


def last_error() -> tuple[int, str]:
    return (1, "Success")


def version() -> tuple[int, int, str] | None:
    return (500, 5120, "15 Jun 2025") if _state["initialized"] else None


def account_info() -> AccountInfo:
    return AccountInfo(51234567, 10_000.0, "USD", "DemoBroker-Server")


def symbols_get(group: str = "*") -> tuple[SymbolInfo, ...]:
    return tuple(SymbolInfo(f"SYM{index}", 5, 0.00001, index % 2 == 0) for index in range(500))


def copy_rates_from_pos(symbol: str, timeframe: int, start_pos: int, count: int) -> np.ndarray:
    return RATES.copy()


def nested_result() -> CheckResult:
    return CheckResult(0, "Done", TradeRequest("EURUSD", 0.1))


def process_id() -> int:
    return os.getpid()


def sleep(seconds: float) -> bool:
    time.sleep(seconds)
    return True


def fail() -> None:
    raise ValueError("broken on purpose")


def unpicklable() -> Any:
    return lambda: None


def crash() -> None:
    os._exit(3)
