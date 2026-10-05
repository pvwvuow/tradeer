"""Health > Demo test (asked for on 5 October 2026): every order action of the bot, for real
on a DEMO account, and the refusals and the clean-up around it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.brokers.demo_test import (
    BREAKOUT_COMMENT,
    DEMO_MAGIC,
    DemoTest,
    Outcome,
    Step,
    save_report,
)
from app.brokers.trade_test import TEST_MAGIC
from app.mt5 import api
from app.strategies.registry import MAGIC_NUMBERS
from tests.fakes.fake_mt5 import FakeMT5

SYMBOL = "XAUUSD.m"
APP = Path(__file__).resolve().parents[2] / "app"
BREAKOUT = "Breakout pair: one fills, the other is cancelled"
EXPIRY = "Pending order expires by itself"


class Gateway:
    """The MT5 gateway thread, inline: every request runs at once on the fake."""

    def __init__(self, mt5: FakeMT5) -> None:
        self.mt5 = mt5
        self.requests: list[str] = []

    def run(self, name: str, work: Any, *, timeout: Any = None, arguments: Any = None) -> Any:
        self.requests.append(name)
        return work(self.mt5)


class Market:
    """One clock for the fake and the test; while the test waits, the price can rise into the
    breakout pair's buy stop, and the fake's server removes expired orders."""

    def __init__(self, mt5: FakeMT5, *, moves: bool = True) -> None:
        self.mt5 = mt5
        self.now = 1_791_200_000.0
        self.moves = moves
        mt5.now = self.clock

    def clock(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.now += seconds
        if not self.moves:
            return
        bid = next(symbol.bid for symbol in self.mt5.symbols if symbol.name == SYMBOL)
        for order in self.mt5.pending_orders:
            if order.comment == BREAKOUT_COMMENT and order.type == api.ORDER_TYPE_BUY_STOP:
                bid = order.price_open
        self.mt5.set_bid(SYMBOL, bid)


def connected(fake: FakeMT5) -> FakeMT5:
    account = fake.accounts[0]
    assert fake.initialize()
    assert fake.login(account.login, account.password, account.server)
    return fake


def demo(fake: FakeMT5, market: Market, **options: Any) -> DemoTest:
    return DemoTest(
        Gateway(fake),  # type: ignore[arg-type]
        pause=market.pause,
        clock=market.clock,
        wall=market.clock,
        **options,
    )


def test_every_step_passes_on_a_demo_account_and_nothing_is_left() -> None:
    fake = connected(FakeMT5())
    seen: list[Step] = []
    lines: list[str] = []
    test = demo(fake, Market(fake), log=lambda level, text: lines.append(f"{level} {text}"))
    report = test.run(["XAUUSD"], seen.append)
    assert [step.name for step in report.steps if step.outcome is not Outcome.PASS] == []
    assert len(report.steps) == 16 and seen == list(report.steps)
    assert report.ok and report.leftovers == () and not report.stopped
    assert report.summary().startswith("Demo test PASS: 16 passed, 0 failed, 0 skipped")
    assert fake.positions == [] and fake.pending_orders == []
    assert {deal.magic for deal in fake.deals} == {DEMO_MAGIC}
    details = {step.name: step.detail for step in report.steps}
    assert details["MT5 and the demo account"].startswith("DEMO account at Demo Broker Ltd")
    assert "0.02 lot at" in details["Market buy with SL and TP"]
    assert "0.01 lot left" in details["Close part of the position"]
    assert details["Closed trade in the history"].endswith("closed by expert")
    assert details[BREAKOUT].startswith("the buy stop filled after 1 s")
    assert "without a cancel from the app" in details[EXPIRY]
    assert "10016 INVALID_STOPS" in details["Refused order is not sent again"]
    assert "closed 1 position(s) and cancelled 1 order(s)" in details[report.steps[-1].name]
    assert lines[0] == f"INFO started on XAUUSD (magic {DEMO_MAGIC})"
    assert lines[-1].startswith("INFO Demo test PASS")


def test_a_real_account_is_refused_before_any_order() -> None:
    fake = FakeMT5()
    fake.accounts[0].trade_mode = api.ACCOUNT_TRADE_MODE_REAL
    report = demo(connected(fake), Market(fake)).run(["XAUUSD", "EURUSD"])
    assert [step.outcome for step in report.steps] == [Outcome.FAIL]
    assert report.steps[0].detail.startswith("refused: this is a REAL account")
    assert fake.trading_calls == [] and report.verdict == "FAIL"


def test_mt5_that_blocks_trading_stops_the_test_with_the_fix() -> None:
    fake = connected(FakeMT5())
    fake.algo_trading = False
    fake.tradeapi_disabled = True
    report = demo(fake, Market(fake)).run(["XAUUSD"])
    assert len(report.steps) == 1 and report.steps[0].outcome is Outcome.FAIL
    assert "Press Algo Trading" in report.steps[0].detail
    assert "external Python API" in report.steps[0].detail
    assert fake.trading_calls == []


def test_without_mt5_it_says_to_connect_first() -> None:
    fake = FakeMT5()
    report = demo(fake, Market(fake)).run(["XAUUSD"])
    assert report.steps[0].detail.startswith("MT5 is not connected")
    assert report.verdict == "FAIL" and fake.trading_calls == []


def test_a_closed_market_skips_the_order_steps() -> None:
    fake = connected(FakeMT5())
    fake.check_retcode = 10018
    report = demo(fake, Market(fake)).run(["XAUUSD"])
    names = [(step.name, step.outcome) for step in report.steps]
    assert names[-2:] == [
        ("Market buy with SL and TP", Outcome.SKIP),
        ("Other order steps", Outcome.SKIP),
    ]
    assert "10018 MARKET_CLOSED" in report.steps[-2].detail
    assert fake.trading_calls == ["order_check"] and fake.positions == []
    assert report.verdict == "INCOMPLETE"


def test_stop_ends_the_test_and_closes_what_it_opened() -> None:
    fake = connected(FakeMT5())
    test = demo(fake, Market(fake))

    def listen(step: Step) -> None:
        if step.name == "Market buy with SL and TP":
            test.stop()

    report = test.run(["XAUUSD", "EURUSD"], listen)
    assert [step.name for step in report.steps] == [
        "MT5 and the demo account",
        "Symbol and price",
        "Lot size and margin",
        "Market buy with SL and TP",
        "Stopped",
    ]
    assert report.stopped and report.leftovers == () and fake.positions == []
    assert report.verdict == "INCOMPLETE" and report.summary().endswith("(stopped)")


def test_a_quiet_market_skips_the_breakout_and_a_stuck_expiry_fails() -> None:
    fake = connected(FakeMT5())
    market = Market(fake, moves=False)
    report = demo(fake, market, fill_wait=5.0, expiry_grace=10.0).run(["XAUUSD"])
    steps = {step.name: step for step in report.steps}
    assert steps[BREAKOUT].outcome is Outcome.SKIP
    assert "did not reach either side" in steps[BREAKOUT].detail
    assert steps[EXPIRY].outcome is Outcome.FAIL
    assert "so it was cancelled" in steps[EXPIRY].detail
    assert fake.pending_orders == [] and fake.positions == [] and report.leftovers == ()
    assert report.verdict == "FAIL"


def test_the_report_is_saved_without_the_login_or_the_name(tmp_path: Path) -> None:
    fake = connected(FakeMT5())
    report = demo(fake, Market(fake)).run(["XAUUSD"])
    path = save_report(report, tmp_path / "reports")
    text = path.read_text(encoding="utf-8")
    assert path.name == "demo-test-20261005-113320.md"
    assert text.startswith("# Demo test\n") and report.summary() in text
    assert f"Magic number of the test trades: {DEMO_MAGIC}" in text
    assert "[\u2713] XAUUSD: Market buy with SL and TP: position" in text
    account = fake.accounts[0]
    assert str(account.login) not in text and account.name not in text


def test_the_demo_magic_number_is_nobody_elses() -> None:
    assert DEMO_MAGIC not in MAGIC_NUMBERS.values() and DEMO_MAGIC != TEST_MAGIC


def test_the_demo_test_refuses_real_accounts_before_any_order() -> None:
    text = (APP / "brokers" / "demo_test.py").read_text(encoding="utf-8")
    assert text.index("AccountKind.DEMO") < text.index(".open(")
