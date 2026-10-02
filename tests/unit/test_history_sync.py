import pytest

from app.mt5 import api
from app.mt5.errors import MT5Error
from app.mt5.gateway import MT5Gateway
from app.mt5.history_sync import HISTORY_START, HistoryImporter, read_history
from app.mt5.models import AccountSnapshot
from app.storage import outbox
from app.storage.ids import account_id, trade_id
from app.storage.repositories import Store
from tests.fakes.fake_mt5 import FakeMT5, make_closed_trade, make_order, make_trade_deal
from tests.unit.storage_helpers import temporary_store

NOW = 1_727_100_000.0


def connected_fake() -> FakeMT5:
    fake = FakeMT5(now=lambda: NOW)
    fake.deals = [
        *make_closed_trade(101, opened=1_726_000_000, closed=1_726_003_600, profit=20.0),
        *make_closed_trade(102, opened=1_726_100_000, closed=1_726_100_900, profit=-8.0),
        make_trade_deal(
            9001,
            0,
            entry=api.DEAL_ENTRY_IN,
            deal_type=api.DEAL_TYPE_BALANCE,
            time_s=1_725_000_000,
            profit=10_000.0,
        ),
    ]
    fake.orders = [make_order(1, 101, 1_726_000_000), make_order(2, 102, 1_726_100_000)]
    fake.initialize()
    fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
    return fake


def importer_for(fake: FakeMT5, store: Store) -> tuple[HistoryImporter, MT5Gateway]:
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    return HistoryImporter(gateway, store, clock=lambda: NOW), gateway


def test_history_is_imported_and_importing_again_changes_nothing() -> None:
    fake = connected_fake()
    account = AccountSnapshot.from_mt5(fake.account_info())
    with temporary_store() as store:
        importer, gateway = importer_for(fake, store)
        try:
            first = importer.run(account, 3.0)
            pending = store.outbox_counts().pending
            second = importer.run(account, 3.0)
        finally:
            gateway.stop()
        key = account_id(account.server, account.login)
        assert (first.deals, first.new_deals, first.orders, first.trades) == (5, 5, 2, 2)
        assert first.changed_trades == 2
        assert (second.new_deals, second.changed_trades) == (0, 0)
        assert store.outbox_counts().pending == pending
        trade = store.get("trades", trade_id(key, 101))
        assert trade is not None
        assert (trade["source"], trade["outcome"], trade["direction"]) == ("manual", "win", "buy")
        assert trade["net_profit"] == round(20.0 - 0.7 - 0.12, 8)
        assert trade["open_time"] == "2024-09-10T17:26:40.000Z"
        assert store.count("trades", key) == 2
        assert fake.trading_calls == []


def test_a_trade_closed_later_is_updated_by_the_next_import() -> None:
    fake = connected_fake()
    entry = make_trade_deal(
        2000,
        200,
        entry=api.DEAL_ENTRY_IN,
        deal_type=api.DEAL_TYPE_SELL,
        time_s=1_727_000_000,
    )
    fake.deals.append(entry)
    account = AccountSnapshot.from_mt5(fake.account_info())
    with temporary_store() as store:
        importer, gateway = importer_for(fake, store)
        try:
            importer.run(account, None)
            key = account_id(account.server, account.login)
            opened = store.get("trades", trade_id(key, 200))
            assert opened is not None and opened["outcome"] == "open"
            exit_deal = make_trade_deal(
                2001,
                200,
                entry=api.DEAL_ENTRY_OUT,
                deal_type=api.DEAL_TYPE_BUY,
                time_s=1_727_050_000,
                profit=6.0,
            )
            fake.deals.append(exit_deal)
            result = importer.run(account, None)
        finally:
            gateway.stop()
        assert result.changed_trades == 1
        trade = store.get("trades", trade_id(key, 200))
        assert trade is not None and trade["outcome"] == "win"
        rows = [item.row_id for item in outbox.pending(store.db.connection(), 1000)]
        assert rows.count(trade_id(key, 200)) == 1


def test_the_next_import_starts_three_days_before_the_newest_deal() -> None:
    fake = connected_fake()
    account = AccountSnapshot.from_mt5(fake.account_info())
    with temporary_store() as store:
        importer, gateway = importer_for(fake, store)
        try:
            importer.run(account, 0.0)
            fake.deals = [deal for deal in fake.deals if deal.time < 1_726_000_000]
            result = importer.run(account, 0.0)
        finally:
            gateway.stop()
        key = account_id(account.server, account.login)
        assert store.get_state(f"history_until:{key}") == str(1_726_100_900)
        assert result.deals == 0
        assert store.count("trades", key) == 2


def test_read_history_raises_a_clear_error_when_mt5_fails() -> None:
    fake = FakeMT5()
    with pytest.raises(MT5Error):
        fake.initialize()
        fake.terminal_running = False
        fake._error = (api.RES_E_INTERNAL_FAIL_TIMEOUT, "IPC timeout")
        read_history(fake, HISTORY_START, int(NOW))


def test_winter_deals_use_the_winter_offset_of_a_new_york_close_broker() -> None:
    fake = connected_fake()
    # 2024-01-10 12:00 server time = 10:00 UTC at UTC+2 (US winter).
    fake.deals = make_closed_trade(300, opened=1_704_888_000, closed=1_704_891_600, profit=5.0)
    account = AccountSnapshot.from_mt5(fake.account_info())
    with temporary_store() as store:
        importer, gateway = importer_for(fake, store)
        try:
            result = importer.run(account, 3.0)  # measured in September: US summer time
        finally:
            gateway.stop()
        key = account_id(account.server, account.login)
        trade = store.get("trades", trade_id(key, 300))
        assert trade is not None
        assert trade["open_time"] == "2024-01-10T10:00:00.000Z"
        assert "US summer time" in result.text()


def test_new_time_rules_import_everything_again_once() -> None:
    fake = connected_fake()
    account = AccountSnapshot.from_mt5(fake.account_info())
    with temporary_store() as store:
        importer, gateway = importer_for(fake, store)
        try:
            importer.run(account, 0.0)
            same_rules = importer.run(account, 0.0)
            new_rules = importer.run(account, 3.0)
            again = importer.run(account, 3.0)
        finally:
            gateway.stop()
        # Normal imports re-read the last 3 days only; new rules re-read everything since 2000.
        assert same_rules.deals == again.deals == 4
        assert new_rules.deals == 5
        assert new_rules.changed_trades == 2
