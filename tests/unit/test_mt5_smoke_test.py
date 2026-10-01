from app.mt5.checklist import ConnectRequest
from app.mt5.smoke_test import run_smoke_test
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5, make_deal


def run(fake: FakeMT5, password: str = FakeAccount().password) -> tuple[bool, list[str]]:
    account = FakeAccount()
    request = ConnectRequest(DEFAULT_PATH, account.login, password, account.server)
    lines: list[str] = []
    ok = run_smoke_test(fake, request, lines.append, path_exists=lambda path: True)
    return ok, lines


def test_the_smoke_test_prints_account_prices_bars_and_deals() -> None:
    fake = FakeMT5(deals=[make_deal(ticket) for ticket in range(1, 16)])
    ok, lines = run(fake)
    text = "\n".join(lines)
    assert ok, text
    assert lines[0].startswith("MT5 smoke test (read-only")
    assert "Account: 51234567" in text
    assert "EURUSD.m 1.08345 / 1.08352" in text
    assert "EURUSD.m M15, last 10 closed bars:" in text
    assert "Last 10 deals (of 15 in the history):" in text
    assert "#15 " in text and "#5 " not in text
    assert lines[-1] == "Result: PASS"
    assert fake.trading_calls == []


def test_the_smoke_test_fails_clearly_without_a_connection() -> None:
    ok, lines = run(FakeMT5(), password="wrong-password")
    assert not ok
    assert lines[-1] == "Result: FAIL"
    assert any("wrong login number" in line for line in lines)
    ok, lines = run(FakeMT5(rates_count=0))
    assert not ok
    assert lines[-1] == "Result: FAIL"


def test_a_new_account_without_deals_still_passes() -> None:
    ok, lines = run(FakeMT5())
    assert ok
    assert "Last 0 deals (of 0 in the history):" in lines
