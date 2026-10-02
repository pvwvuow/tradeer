import os
import threading
import time
from collections import namedtuple
from multiprocessing import Pipe
from typing import cast

import numpy as np
import pytest

from app.mt5.api import MT5Api
from app.mt5.errors import MT5Error, MT5Timeout, MT5Unavailable
from app.mt5.gateway import MT5Gateway
from app.mt5.terminal_process import MT5Process, decode, encode, helper_main

PACKAGE = "tests.fakes.fake_mt5_package"
MISSING = "tests.fakes.no_such_package"


def test_records_come_back_as_named_tuples_with_the_same_fields() -> None:
    Tick = namedtuple("Tick", ["time", "bid", "ask"])
    Request = namedtuple("Request", ["symbol"])
    Result = namedtuple("Result", ["retcode", "request"])
    rates = np.zeros(3, dtype=[("time", "<i8"), ("close", "<f8")])
    value = {
        "ticks": (Tick(1, 1.1, 1.2), Tick(2, 1.3, 1.4)),
        "result": Result(0, Request("EURUSD")),
        "rates": rates,
        "error": (1, "Success"),
        "none": None,
    }
    encoded = encode(value)
    assert encoded["ticks"][0].fields is encoded["ticks"][1].fields  # names stored once
    back = decode(encoded)
    assert back["ticks"][1].bid == 1.3
    assert back["ticks"][1]._asdict() == {"time": 2, "bid": 1.3, "ask": 1.4}
    assert type(back["ticks"][0]).__name__ == "Tick"
    assert back["result"].request.symbol == "EURUSD"
    assert back["error"] == (1, "Success")
    assert back["rates"] is rates
    assert back["none"] is None


def test_the_helper_loop_answers_reports_errors_and_stops() -> None:
    parent, child = Pipe()
    thread = threading.Thread(target=helper_main, args=(child, PACKAGE), daemon=True)
    thread.start()
    try:
        assert parent.recv() == ("ready", "5.0.test")
        parent.send(("call", "account_info", (), {}))
        status, payload = parent.recv()
        assert status == "ok" and decode(payload).login == 51234567
        parent.send(("call", "no_such_function", (), {}))
        assert parent.recv()[0] == "missing"
        parent.send(("call", "fail", (), {}))
        assert parent.recv() == ("error", "ValueError: broken on purpose")
        parent.send(("call", "unpicklable", (), {}))
        status, payload = parent.recv()
        assert status == "error" and "could not be sent" in payload
        parent.send(("ping", "ping", (), {}))
        assert parent.recv() == ("ok", (os.getpid(), "5.0.test"))
        parent.send(None)
        thread.join(5)
    finally:
        parent.close()
    assert not thread.is_alive()


def test_a_package_that_cannot_be_imported_is_reported_by_the_helper() -> None:
    parent, child = Pipe()
    helper_main(child, MISSING)
    status, detail = parent.recv()
    assert status == "failed" and "ModuleNotFoundError" in detail


def test_calls_run_in_another_process_and_keep_the_package_shapes() -> None:
    process = MT5Process(PACKAGE)
    try:
        assert process.start() == "5.0.test"
        assert process.initialize()
        assert process.version() == (500, 5120, "15 Jun 2025")
        account = process.account_info()
        assert account.login == 51234567 and account.currency == "USD"
        symbols = process.symbols_get()
        assert len(symbols) == 500 and symbols[3].name == "SYM3"
        rates = process.copy_rates_from_pos("EURUSD", 5, 1, 10)
        assert rates["close"][0] == 1.15 and rates.dtype.names[0] == "time"
        assert process.nested_result().request.symbol == "EURUSD"
        pid, version = process.ping()
        assert pid == process.process_id() == process.pid
        assert pid != os.getpid() and version == "5.0.test"
        with pytest.raises(AttributeError):
            process.no_such_function()
        with pytest.raises(MT5Error) as caught:
            process.fail()
        assert "broken on purpose" in str(caught.value)
    finally:
        process.close()
    assert not process.running


def test_a_hanging_call_restarts_the_helper_and_the_next_call_works() -> None:
    beats: list[float] = []
    process = MT5Process(
        PACKAGE,
        call_timeout=0.6,
        wait_slice=0.05,
        on_wait=lambda: beats.append(1.0),
    )
    try:
        first = process.process_id()
        started = time.monotonic()
        with pytest.raises(MT5Timeout):
            process.sleep(30)
        assert time.monotonic() - started < 10
        assert len(beats) >= 3  # the waiting thread kept beating
        assert process.process_id() != first
        assert process.starts == 2
    finally:
        process.close()


def test_a_crashed_helper_is_reported_and_replaced() -> None:
    process = MT5Process(PACKAGE, wait_slice=0.05)
    try:
        first = process.process_id()
        with pytest.raises(MT5Error) as caught:
            process.crash()
        assert "stopped" in str(caught.value)
        assert process.process_id() != first
    finally:
        process.close()


def test_a_missing_package_raises_unavailable() -> None:
    process = MT5Process(MISSING)
    with pytest.raises(MT5Unavailable) as caught:
        process.start()
    assert "ModuleNotFoundError" in caught.value.detail
    assert not process.running


def test_the_gateway_beats_and_reports_its_busy_request_while_the_helper_works() -> None:
    helpers: list[MT5Process] = []

    def factory() -> MT5Api:
        process = MT5Process(PACKAGE, wait_slice=0.05)
        process.start()
        helpers.append(process)
        return cast(MT5Api, process)

    beats: list[float] = []
    gateway = MT5Gateway(factory, heartbeat=lambda: beats.append(time.monotonic()), idle_seconds=5)
    gateway.start()
    try:
        assert gateway.call("account_info").balance == 10_000.0
        submitted = time.monotonic()
        slow = gateway.submit("slow", lambda mt5: mt5.sleep(0.8))
        time.sleep(0.4)
        busy = gateway.busy()
        assert busy is not None and busy.name == "slow" and busy.seconds > 0.1
        assert slow.result(timeout=10) is True
        finished = time.monotonic()
        during = [beat for beat in beats if submitted + 0.15 < beat < finished - 0.15]
        assert len(during) >= 3  # idle beats are 5 s apart: these came from the waiting call
    finally:
        gateway.stop()
    assert gateway.busy() is None
    assert helpers and not helpers[0].running
