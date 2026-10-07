"""PC log of 7 October 2026 (0.23.5): the daily report of 6 October counted the demo test's
26 trades (net -7.74 USD, win rate 0%, worst trade "unknown"), and the risk manager counted
their entries toward the trades-per-day limit, so a demo test could block the bot's signals
until the broker's midnight. The demo test's trades (TEST_MAGIC) now count nowhere.
"""

from __future__ import annotations

from typing import Any

from app.analytics.trades import load_trades
from app.brokers.demo_test import DEMO_MAGIC
from app.domain.history import DEAL_ENTRY_IN, DEAL_TYPE_BUY, TEST_MAGIC
from app.mt5.risk_reads import read_picture
from app.storage.signal_store import iso_time
from tests.fakes.fake_mt5 import FakeMT5, make_trade_deal

BOT_MAGIC = 26_000_101
CLOSED = 1_791_300_000.0


def test_the_demo_test_trades_under_the_magic_that_counts_nowhere() -> None:
    assert DEMO_MAGIC == TEST_MAGIC


def row(number: int, magic: int, net: float) -> dict[str, Any]:
    return {
        "id": f"t{number}",
        "account_id": "demo",
        "mode": "live",
        "source": "external" if magic else "manual",
        "symbol": "GBPUSD",
        "direction": "buy",
        "volume": 0.01,
        "open_time": iso_time(CLOSED + number * 60 - 30),
        "close_time": iso_time(CLOSED + number * 60),
        "profit": net,
        "net_profit": net,
        "magic": magic,
    }


class Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def query(self, sql: str, parameters: Any = ()) -> list[Any]:
        return list(self.rows)


def test_the_journal_reports_and_statistics_leave_the_demo_test_out() -> None:
    db = Rows([row(1, TEST_MAGIC, -0.30), row(2, 0, 1.50), row(3, TEST_MAGIC, -0.28)])
    assert [trade.id for trade in load_trades(db)] == ["t2"]
    every = load_trades(db, tests=True)
    assert [trade.id for trade in every] == ["t1", "t2", "t3"]
    assert [trade.demo_test for trade in every] == [True, False, True]


def test_the_demo_tests_entries_do_not_count_toward_the_trades_of_the_day() -> None:
    fake = FakeMT5(now=lambda: CLOSED)
    account = fake.accounts[0]
    assert fake.initialize()
    assert fake.login(account.login, account.password, account.server)
    server = int(CLOSED + fake.server_offset_hours * 3600)
    magics = [TEST_MAGIC] * 26 + [BOT_MAGIC, 0]
    fake.deals = [
        make_trade_deal(
            100 + number,
            900 + number,
            entry=DEAL_ENTRY_IN,
            deal_type=DEAL_TYPE_BUY,
            time_s=server - 600 + number,
            magic=magic,
        )
        for number, magic in enumerate(magics)
    ]
    picture = read_picture(
        fake,
        day="2026-10-06",
        day_start_server=server - 3600,
        read_at=CLOSED,
        strategy_of=lambda magic: "trend_pullback" if magic == BOT_MAGIC else "",
        cache={},
    )
    assert picture is not None
    assert picture.bot_entries_today == 1
    assert picture.manual_entries_today == 1
