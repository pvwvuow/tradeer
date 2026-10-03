"""Trade requests, the retcode policy and the checks at approval (spec C5, C7)."""

from app.brokers.requests import OrderPlan, choose_filling, close_request, open_request
from app.domain.orders import (
    failed,
    frozen,
    recheck_at_approval,
    round_price,
    short_id,
    stop_distance_check,
)
from app.domain.signals import Direction, OrderType, Signal
from app.mt5 import api
from app.mt5.retcodes import RETRY_CODES, Policy, describe, retcode_text
from tests.unit.signal_helpers import MORNING, make_signal

NOW = float(MORNING)


def plan(**changes: object) -> OrderPlan:
    values: dict[str, object] = {
        "symbol": "XAUUSD",
        "direction": Direction.LONG,
        "order_type": OrderType.MARKET,
        "volume": 0.05,
        "price": 2385.123,
        "sl": 2375.0,
        "tp": 2405.0,
        "magic": 26_070_001,
        "comment": "tw-0123456789",
        "deviation": 10,
        "filling": api.ORDER_FILLING_IOC,
        "digits": 2,
    }
    values.update(changes)
    return OrderPlan(**values)  # type: ignore[arg-type]


def test_retry_only_on_safe_codes() -> None:
    assert RETRY_CODES == {10004, 10012, 10020, 10021, 10024, 10031}
    for code in (10016, 10019, 10017, 10018, 10014, 10030):
        assert describe(code).policy is Policy.FAIL, code
    assert describe(10009).ok and describe(10008).policy is Policy.PLACED
    assert describe(None).name == "NO_RESULT" and not describe(None).ok
    assert describe(99999).policy is Policy.FAIL
    assert retcode_text(10016) == "10016 INVALID_STOPS: the stop loss or take profit is invalid"


def test_the_filling_mode_follows_the_symbol_flags() -> None:
    assert choose_filling(3) == api.ORDER_FILLING_FOK
    assert choose_filling(2) == api.ORDER_FILLING_IOC  # XAUUSD at FIBO: IOC only
    assert choose_filling(0) == api.ORDER_FILLING_RETURN


def test_every_order_request_carries_sl_tp_magic_and_the_signal_comment() -> None:
    request = open_request(plan())
    assert request["action"] == api.TRADE_ACTION_DEAL and request["type"] == api.ORDER_TYPE_BUY
    assert request["sl"] == 2375.0 and request["tp"] == 2405.0 and request["price"] == 2385.12
    assert request["magic"] == 26_070_001 and request["comment"] == "tw-0123456789"
    assert request["type_filling"] == api.ORDER_FILLING_IOC and request["deviation"] == 10
    pending = open_request(
        plan(order_type=OrderType.STOP, direction=Direction.SHORT, expiration=1_790_000_000),
    )
    assert pending["action"] == api.TRADE_ACTION_PENDING
    assert pending["type"] == api.ORDER_TYPE_SELL_STOP
    assert pending["type_time"] == api.ORDER_TIME_SPECIFIED
    assert pending["expiration"] == 1_790_000_000
    closing = close_request(
        7,
        "XAUUSD",
        Direction.LONG,
        0.05,
        2390.0,
        deviation=10,
        magic=1,
        comment="tw-x",
        filling=1,
    )
    assert closing["type"] == api.ORDER_TYPE_SELL and closing["position"] == 7


def test_short_ids_fit_the_mt5_comment() -> None:
    comment = short_id("1d9bc982-b4aa-5f3e-9c8b-0a1b2c3d4e5f")
    assert comment == "tw-1d9bc982b4" and len(comment) <= 31


def test_prices_round_to_the_tick_grid() -> None:
    assert round_price(1.083527, 5) == 1.08353
    assert round_price(2385.13, 2, 0.05) == 2385.15


def test_stops_and_freeze_levels() -> None:
    check = stop_distance_check(Direction.LONG, 1.1, 1.0995, 1.102, stops_level=10, point=1e-5)
    assert check.passed and check.value == 50.0
    near = stop_distance_check(Direction.LONG, 1.1, 1.09995, 1.102, stops_level=10, point=1e-5)
    assert not near.passed
    wrong = stop_distance_check(Direction.SHORT, 1.1, 1.0995, 1.098, stops_level=0, point=1e-5)
    assert not wrong.passed
    assert frozen(1.1, 1.09998, 5, 1e-5) and not frozen(1.1, 1.0999, 5, 1e-5)


def failing(signal: Signal, bid: float, ask: float, now: float = NOW) -> list[str]:
    checks = recheck_at_approval(
        signal,
        bid,
        ask,
        max_entry_move_r=0.25,
        max_spread_sl_fraction=0.2,
        stops_level=10,
        point=1e-5,
        now=now,
    )
    return [check.name for check in failed(checks)]


def test_the_approval_re_check() -> None:
    signal = make_signal(entry=1.10000, sl=1.09900, tp=1.10200)
    assert failing(signal, 1.10003, 1.10010) == []
    assert failing(signal, 1.10040, 1.10047) == ["entry tolerance"]
    assert failing(signal, 1.09975, 1.10010) == ["spread"]
    assert "not expired" in failing(signal, 1.1, 1.1, now=signal.expires_at)
    stop = make_signal(order_type=OrderType.STOP, entry=1.10100, sl=1.10000, tp=1.10300)
    assert failing(stop, 1.10050, 1.10057) == []
    assert failing(stop, 1.10150, 1.10157) == ["pending entry"]
