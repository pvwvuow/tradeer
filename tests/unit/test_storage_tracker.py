from collections.abc import Callable

import pytest

from app.mt5.checklist import ChecklistReport
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.mt5.errors import MT5Error
from app.mt5.gateway import MT5Gateway
from app.mt5.history_sync import HistoryImporter
from app.mt5.models import AccountSnapshot, Quote
from app.storage.ids import account_id
from app.storage.tracker import AccountTracker, broker_offset
from tests.fakes.fake_mt5 import FakeMT5, make_closed_trade
from tests.unit.storage_helpers import temporary_store

NOW = 1_727_100_000.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def status_for(fake: FakeMT5, connected: bool = True) -> ConnectionStatus:
    account = AccountSnapshot.from_mt5(fake.account_info())
    quote = Quote("EURUSD.m", 1.1, 1.1001, int(NOW + 3 * 3600))
    report = ChecklistReport((), None, account, ("EURUSD.m",), (quote,))
    state = ConnectionState.CONNECTED if connected else ConnectionState.RECONNECTING
    return ConnectionStatus(state, "Connected", account, None, report)


def run_now(work: Callable[[], None]) -> None:
    work()


def test_the_broker_offset_comes_from_the_freshest_quote() -> None:
    fake = FakeMT5(now=lambda: NOW)
    fake.initialize()
    fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
    assert broker_offset(status_for(fake), NOW) == 3.0
    assert broker_offset(ConnectionStatus(), NOW) is None


def test_a_new_connection_saves_the_account_imports_history_and_snapshots() -> None:
    fake = FakeMT5(now=lambda: NOW)
    fake.deals = make_closed_trade(5, opened=1_726_000_000, closed=1_726_000_600, profit=4.0)
    fake.initialize()
    fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    results: list[object] = []
    with temporary_store() as store:
        current = status_for(fake)
        store.start_session("s-1", app_version="0", profile="p", mode="paper", settings={})
        clock = Clock()
        tracker = AccountTracker(
            store,
            "s-1",
            status=lambda: current,
            history=HistoryImporter(gateway, store, clock=lambda: NOW),
            clock=clock,
            wall_clock=lambda: NOW,
            run_in_background=run_now,
        )
        tracker.add_listener(results.append)
        try:
            tracker.on_status(current)
            tracker.on_status(current)
        finally:
            gateway.stop()
        key = account_id(fake.accounts[0].server, fake.accounts[0].login)
        assert tracker.account_id == key
        assert store.get("sessions", "s-1")["account_id"] == key  # type: ignore[index]
        assert len(results) == 1 and tracker.last_history is not None
        assert tracker.last_history.offset_hours == 3.0
        assert store.count("trades", key) == 1
        assert store.count("account_snapshots", key) == 1
        clock.now += 301
        tracker.tick()
        assert store.count("account_snapshots", key) == 2


def test_nothing_is_recorded_while_disconnected() -> None:
    fake = FakeMT5(now=lambda: NOW)
    fake.initialize()
    fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
    errors: list[object] = []
    with temporary_store() as store:
        lost = status_for(fake, connected=False)
        tracker = AccountTracker(store, "s-1", status=lambda: lost, run_in_background=run_now)
        tracker.add_listener(errors.append)
        tracker.on_status(lost)
        tracker.tick()
        assert store.count("accounts") == 0
        with pytest.raises(MT5Error):
            tracker.import_history()
        assert len(errors) == 1 and isinstance(errors[0], MT5Error)
