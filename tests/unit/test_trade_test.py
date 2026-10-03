"""`--mt5-trade-test` (spec I4) against FakeMT5: the full order path on DEMO, refused on REAL."""

from app.brokers.trade_test import TEST_MAGIC, run_trade_test
from app.cli import parse_args
from app.mt5 import api
from app.mt5.checklist import ConnectRequest
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5


def run(fake: FakeMT5, symbol: str = "EURUSD") -> tuple[bool, str]:
    account = fake.accounts[0]
    request = ConnectRequest(DEFAULT_PATH, account.login, account.password, account.server)
    lines: list[str] = []
    ok = run_trade_test(
        fake,
        request,
        symbol,
        lines.append,
        pause=lambda seconds: None,
        path_exists=lambda path: True,
    )
    return ok, "\n".join(lines)


def test_the_trade_test_opens_modifies_closes_and_reads_the_deal_on_demo() -> None:
    fake = FakeMT5(commission_per_lot=3.5)
    ok, text = run(fake, "XAUUSD")
    assert ok, text
    for step in ("1. Open buy 0.01 lot", "2. The position is in MT5", "3. Move the SL", "4. Close"):
        assert f"[\u2713] {step}" in text, step
    assert f"magic {TEST_MAGIC}" in text and "order_check #1: 0 OK" in text
    assert "10009 DONE" in text and "5. Closed deal read from the history" in text
    assert "commission -0.08" in text and text.endswith("Result: PASS")
    assert fake.positions == []
    assert fake.trading_calls == ["order_check", "order_send", "order_send", "order_send"]


def test_ioc_only_symbols_are_sent_with_ioc() -> None:
    fake = FakeMT5()
    gold = next(symbol for symbol in fake.symbols if symbol.name == "XAUUSD.m")
    gold.filling_flags = 2
    ok, text = run(fake, "XAUUSD")
    assert ok and "filling IOC" in text


def test_a_real_account_is_refused_before_any_order() -> None:
    fake = FakeMT5(accounts=[FakeAccount(trade_mode=api.ACCOUNT_TRADE_MODE_REAL)])
    ok, text = run(fake)
    assert not ok and "Refused: this is a REAL account" in text
    assert text.endswith("Result: FAIL (nothing was sent)") and fake.trading_calls == []


def test_a_rejected_order_fails_with_its_retcode() -> None:
    fake = FakeMT5()
    fake.send_script = [10019]
    ok, text = run(fake)
    assert not ok and "10019 NO_MONEY" in text and fake.positions == []


def test_the_command_line_flag() -> None:
    options = parse_args(["--mt5-trade-test", "--symbol", "XAUUSD"])
    assert options.mt5_trade_test and options.symbol == "XAUUSD"
    assert parse_args([]).symbol == "EURUSD" and not parse_args([]).mt5_trade_test
