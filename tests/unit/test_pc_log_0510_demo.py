"""PC demo test of 5 October 2026 evening (0.23.0, MetaTrader5 5.0.6090): every
`order_check` and `order_send` answered None with the last error "-2 Unnamed arguments not
allowed", so no order could open, on the demo test and at the London open alike. The helper
now asks MT5 again with the request's fields named; MT5 refuses such a call before anything
reaches the trade server, so the second ask can never open a trade twice. Fixed in 0.23.1.
"""

from __future__ import annotations

from collections import namedtuple
from enum import IntEnum
from typing import Any

import numpy as np

from app.mt5.terminal_process import _reply, call_package, decode

SendResult = namedtuple("SendResult", ["retcode", "symbol", "volume"])
REFUSED = (-2, "Unnamed arguments not allowed")
SUCCESS = (1, "Success")


def request() -> dict[str, Any]:
    return {
        "action": 1,
        "symbol": "EURUSD",
        "volume": 0.01,
        "type": 0,
        "price": 1.12186,
        "sl": 1.12086,
        "tp": 1.12386,
        "deviation": 10,
        "magic": 26_070_098,
        "comment": "tw-demo-test",
        "type_filling": 0,
        "type_time": 0,
    }


class NamedOnly:
    """Takes the request only as named fields, like the package on the PC."""

    def __init__(self) -> None:
        self.error: tuple[int, str] = SUCCESS
        self.forms: list[str] = []

    def last_error(self) -> tuple[int, str]:
        return self.error

    def _trade(self, *args: Any, **kwargs: Any) -> Any:
        if args:
            self.forms.append("positional")
            self.error = REFUSED
            return None
        self.forms.append("named")
        self.error = SUCCESS
        return SendResult(10009, kwargs["symbol"], kwargs["volume"])

    def order_send(self, *args: Any, **kwargs: Any) -> Any:
        return self._trade(*args, **kwargs)

    def order_check(self, *args: Any, **kwargs: Any) -> Any:
        return self._trade(*args, **kwargs)

    def positions_get(self, *args: Any, **kwargs: Any) -> Any:
        self.forms.append("positions_get")
        self.error = REFUSED
        return None


class Refusing(NamedOnly):
    """Refuses every form, as for a field MT5 cannot read: each form is asked once."""

    def _trade(self, *args: Any, **kwargs: Any) -> Any:
        self.forms.append("positional" if args else "named")
        self.error = REFUSED
        return None


class Blocked(NamedOnly):
    """Takes the request but blocks trading: that answer is final, nothing is asked again."""

    def _trade(self, *args: Any, **kwargs: Any) -> Any:
        self.forms.append("positional" if args else "named")
        self.error = (-8, "Terminal: Autotrading disabled")
        return None


class PlainOnly(NamedOnly):
    """Refuses numpy and enum values but takes the request positionally with plain values."""

    def _trade(self, *args: Any, **kwargs: Any) -> Any:
        values = args[0] if args else kwargs
        plain = all(type(value) in (int, float, str) for value in values.values())
        self.forms.append(("positional" if args else "named") + (" plain" if plain else ""))
        if not args or not plain:
            self.error = REFUSED
            return None
        self.error = SUCCESS
        return SendResult(10009, values["symbol"], values["volume"])


def test_a_positional_request_refused_for_its_form_is_sent_with_named_fields() -> None:
    package = NamedOnly()
    message = ("call", "order_send", (request(),), {})
    status, payload = _reply(package, message)
    assert status == "ok"
    result = decode(payload)
    assert result.retcode == 10009 and result.symbol == "EURUSD" and result.volume == 0.01
    assert package.forms == ["positional", "named"]
    assert package.last_error() == SUCCESS
    status, payload = _reply(package, ("call", "order_check", (request(),), {}))
    assert status == "ok" and decode(payload).retcode == 10009


def test_every_form_is_asked_once_and_none_comes_back_when_all_are_refused() -> None:
    package = Refusing()
    function = package.order_send
    assert call_package(package, function, (request(),), {}) is None
    assert package.forms == ["positional", "named", "positional", "named"]
    assert package.last_error() == REFUSED


def test_another_error_is_final_and_other_calls_are_never_repeated() -> None:
    package = Blocked()
    assert call_package(package, package.order_send, (request(),), {}) is None
    assert package.forms == ["positional"]
    reads = NamedOnly()
    assert call_package(reads, reads.positions_get, ("EURUSD",), {}) is None
    assert reads.forms == ["positions_get"]
    named = NamedOnly()
    assert call_package(named, named.order_send, (), request()) is not None
    assert named.forms == ["named"]


class Side(IntEnum):
    BUY = 0


def test_numpy_and_enum_values_are_sent_as_plain_numbers() -> None:
    package = PlainOnly()
    body = {**request(), "type": Side.BUY, "volume": np.float64(0.01), "magic": np.int64(7)}
    result = call_package(package, package.order_check, (body,), {})
    assert result is not None and result.retcode == 10009
    assert package.forms == ["positional", "named", "positional plain"]
