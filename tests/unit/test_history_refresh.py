"""The trade list stays complete (PC log of 12 October 2026, 0.50.2): the history is read
again after a reconnect, every 10 minutes and when a position closed; a closed trade's
notice gives its real net result."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.engine.execution import ExecutionSnapshot, PositionView
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.mt5.history_sync import HistoryResult
from app.mt5.models import AccountSnapshot
from app.notify.events import closed_result, execution_notices
from app.storage.tracker import HISTORY_SECONDS, AccountTracker
from tests.fakes.fake_mt5 import FakeMT5
from tests.unit.storage_helpers import temporary_store

RESULT = HistoryResult(4, 1, 7, 3, 1, 3.0, "UTC+3")
QUIET = HistoryResult(4, 0, 7, 3, 0, 3.0, "UTC+3")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Importer:
    """Counts the imports (the real one is tested in test_history_sync)."""

    def __init__(self) -> None:
        self.runs = 0
        self.result = RESULT

    def run(self, account: AccountSnapshot, offset: float | None, known: Any) -> HistoryResult:
        self.runs += 1
        return self.result


def run_now(work: Callable[[], None]) -> None:
    work()


def account() -> AccountSnapshot:
    fake = FakeMT5()
    fake.initialize()
    fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
    return AccountSnapshot.from_mt5(fake.account_info())


def test_a_reconnect_a_timer_and_a_closed_position_read_the_history_again() -> None:
    connected = ConnectionStatus(ConnectionState.CONNECTED, "Connected", account())
    lost = replace(connected, state=ConnectionState.RECONNECTING)
    current = [connected]
    importer = Importer()
    events: list[str] = []
    clock = Clock()
    with temporary_store() as store:
        store.start_session("s-1", app_version="0", profile="p", mode="paper", settings={})
        tracker = AccountTracker(
            store,
            "s-1",
            status=lambda: current[0],
            history=importer,  # type: ignore[arg-type]
            clock=clock,
            wall_clock=lambda: 1_727_100_000.0,
            run_in_background=run_now,
            log=lambda level, message: events.append(message),
        )
        tracker.on_status(connected)
        assert importer.runs == 1  # the first connection
        tracker.on_status(connected)
        assert importer.runs == 1  # still the same connection
        current[0] = lost
        tracker.on_status(lost)
        current[0] = connected
        tracker.on_status(connected)
        assert importer.runs == 2  # back after a reconnect
        assert any("Reconnected to MT5" in line for line in events)
        importer.result = QUIET
        clock.now += HISTORY_SECONDS + 1
        tracker.tick()
        assert importer.runs == 3  # the timer
        current[0] = replace(connected, open_positions=2)
        tracker.tick()
        current[0] = replace(connected, open_positions=1)
        tracker.tick()
        assert importer.runs == 4  # a position closed
        tracker.tick()
        assert importer.runs == 4
    imported = [line for line in events if line.startswith("Trade history imported")]
    assert len(imported) == 2  # the quiet ones found nothing new and said nothing


def view(ticket: int, profit: float) -> PositionView:
    return PositionView(
        "live",
        ticket,
        "EURUSD",
        "sell",
        0.09,
        1.1192,
        1.12171,
        1.11553,
        profit,
        "london_breakout",
        False,
    )


def test_a_closed_trade_notice_gives_the_real_net_result() -> None:
    line = (
        "19:00:07 UTC  EURUSD position 34969404 closed by take profit: net +32.25 "
        "(profit +32.85, commission -0.60, swap +0.00), +1.41 R"
    )
    assert closed_result([line], 34969404) == ("take profit", "+32.25")
    assert closed_result([line], 1) is None
    old = ExecutionSnapshot(positions=(view(34969404, 6.48), view(7, -3.0)))
    new = ExecutionSnapshot(messages=(line,))
    texts = [notice.text for notice in execution_notices(old, new)]
    assert "EURUSD position 34969404 (live) closed by take profit, net +32.25" in texts
    assert "EURUSD position 7 (live) closed, last result -3.00" in texts
