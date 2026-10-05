"""PC log of 5 October 2026, London open (0.22.3): Auto sent the London breakout's buy stop
and sell stop on XAUUSD, and both stopped at an `order_check` that returned nothing. Such a
check no longer stops an order (the trade server checks it), MT5's last error and switches
are explained, every failed order is an urgent notice, and Auto mode asks for no approvals.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from app.brokers.live_broker import send_open
from app.brokers.requests import OrderPlan
from app.domain.modes import OperatingMode
from app.domain.probability import ProbabilityEstimate
from app.domain.signals import (
    Direction,
    OrderType,
    Signal,
    SignalRecord,
    SignalState,
    StateChange,
)
from app.engine.execution import ExecutionSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5 import api
from app.mt5.retcodes import explain_last_error, no_result_text
from app.notify.events import EventKind, Notice, signal_notices
from app.notify.watcher import NoticeWatcher
from app.observability.decision_trace import DecisionTrace
from tests.fakes.fake_mt5 import FakeMT5


class SilentCheckMT5(FakeMT5):
    """FakeMT5 whose `order_check` answers nothing, as the terminal did at the London open."""

    def order_check(self, request: dict[str, Any]) -> Any:
        self.calls.append("order_check")
        self._error = (api.RES_E_FAIL, "Terminal: Call failed")
        return None


class SilentMT5(SilentCheckMT5):
    """Nothing comes back from `order_send` either: trading from Python is blocked."""

    def order_send(self, request: dict[str, Any]) -> Any:
        self.calls.append("order_send")
        self._error = (api.RES_E_AUTO_TRADING_DISABLED, "Terminal: Autotrading disabled")
        return None


def connected(fake: FakeMT5) -> FakeMT5:
    account = fake.accounts[0]
    assert fake.initialize()
    assert fake.login(account.login, account.password, account.server)
    return fake


def buy_stop() -> OrderPlan:
    return OrderPlan(
        symbol="XAUUSD.m",
        direction=Direction.LONG,
        order_type=OrderType.STOP,
        volume=0.01,
        price=2400.0,
        sl=2380.0,
        tp=2430.0,
        magic=26_070_002,
        comment="tw-0510london",
        deviation=10,
        filling=api.ORDER_FILLING_IOC,
        digits=2,
        expiration=int(time.time()) + 6 * 3600,
    )


def no_pause(seconds: float) -> None:
    return None


def test_a_check_without_an_answer_no_longer_stops_the_order() -> None:
    fake = connected(SilentCheckMT5())
    result = send_open(fake, buy_stop(), retries=1, pause=no_pause)
    assert result.ok and result.placed, result.text
    assert fake.trading_calls == ["order_check", "order_send"]
    assert len(fake.pending_orders) == 1
    check = result.attempts[0]
    assert check.action == "order_check" and check.retcode is None
    assert check.last_error == "-1 Terminal: Call failed"
    assert "last error -1 Terminal: Call failed: the terminal could not run" in check.retcode_text


def test_no_answer_at_all_says_what_to_change_in_mt5() -> None:
    fake = connected(SilentMT5())
    fake.tradeapi_disabled = True
    result = send_open(fake, buy_stop(), retries=2, pause=no_pause)
    assert not result.ok and fake.trading_calls == ["order_check", "order_send"]
    assert "last error -8 Terminal: Autotrading disabled" in result.text
    assert "MT5: Algo Trading on, trading from Python blocked, broker connected" in result.text
    assert 'untick "Disable automatic trading through the external Python API"' in result.text
    assert "XAUUSD.m: trade mode 4, filling FOK+IOC" in result.text


def test_a_refused_check_still_sends_nothing() -> None:
    fake = connected(FakeMT5())
    fake.check_retcode = 10019
    result = send_open(fake, buy_stop(), retries=1, pause=no_pause)
    assert not result.ok and fake.trading_calls == ["order_check"]
    assert result.text.startswith("order_check refused the order: 10019 NO_MONEY")
    assert "MT5:" not in result.text


def test_last_errors_in_plain_words() -> None:
    assert "external Python API" in explain_last_error("-8 Terminal: Autotrading disabled")
    assert "send the log" in explain_last_error('-2 Invalid "expiration" argument')
    assert "reconnects" in explain_last_error("-10004 No IPC connection")
    assert explain_last_error("") == "" and explain_last_error("1 Success") == ""
    assert no_result_text("") == "0 NO_RESULT: MT5 returned no result"


def pending(signal_id: str = "sig-london-buy") -> SignalRecord:
    signal = Signal(
        id=signal_id,
        symbol="XAUUSD",
        timeframe="M15",
        direction=Direction.LONG,
        order_type=OrderType.STOP,
        entry=4168.11,
        sl=4124.41,
        tp=4233.66,
        reason="Asia range 4124.41 to 4163.03",
        strategy="london_breakout",
        strategy_version="1.0.0",
        params_hash="a685671d3cdad730",
        bar_time=0,
        created_at=0.0,
        expires_at=time.time() + 3600,
        digits=2,
        state=SignalState.PENDING_APPROVAL,
    )
    estimate = ProbabilityEstimate(None, None, None, 0, "none", 30)
    return SignalRecord(signal, DecisionTrace(signal.id, "trace-london"), estimate)


def failed(reason: str) -> SignalRecord:
    record = pending()
    change = StateChange(SignalState.SENT, SignalState.FAILED, 0.0, reason)
    signal = replace(record.signal, state=SignalState.FAILED, history=(change,))
    return replace(record, signal=signal)


def test_every_failed_order_is_told_once() -> None:
    why = "order_check refused the order: 0 NO_RESULT: MT5 returned no result"
    old, new = SignalsSnapshot(signals=(pending(),)), SignalsSnapshot(signals=(failed(why),))
    notices = signal_notices(old, new, auto=True)
    assert [notice.kind for notice in notices] == [EventKind.ERROR]
    assert notices[0].text == f"Order failed: {new.signals[0].signal.summary()}: {why}"
    assert notices[0].key == "failed:sig-london-buy"
    assert signal_notices(new, new) == []
    oco = failed("cancelled: the other side of the breakout filled (OCO)")
    assert signal_notices(old, SignalsSnapshot(signals=(oco,))) == []


def test_auto_mode_asks_for_no_approval() -> None:
    old, new = SignalsSnapshot(), SignalsSnapshot(signals=(pending(),))
    assert signal_notices(old, new, auto=True) == []
    assert [notice.kind for notice in signal_notices(old, new)] == [EventKind.APPROVAL_NEEDED]


class Center:
    def __init__(self) -> None:
        self.notices: list[Notice] = []

    def publish_all(self, notices: list[Notice]) -> list[Notice]:
        self.notices.extend(notices)
        return notices

    def publish(self, notice: Notice) -> Notice:
        self.notices.append(notice)
        return notice


def test_the_watcher_follows_the_trading_mode() -> None:
    expected = {OperatingMode.AUTO: [], OperatingMode.SEMI_AUTO: [EventKind.APPROVAL_NEEDED]}
    for mode, kinds in expected.items():
        center = Center()
        watcher = NoticeWatcher(center)
        watcher.on_execution(ExecutionSnapshot(mode=mode))
        watcher.on_signals(SignalsSnapshot())
        watcher.on_signals(SignalsSnapshot(signals=(pending(),)))
        assert [notice.kind for notice in center.notices] == kinds, mode
