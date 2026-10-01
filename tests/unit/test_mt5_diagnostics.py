from app.mt5.checklist import ConnectRequest
from app.mt5.diagnostics import (
    DIAGNOSTIC_TIMEFRAMES,
    estimate_broker_offset,
    offset_text,
    run_diagnostics,
)
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5

NOW = 1_790_000_000.0


def request(password: str = FakeAccount().password) -> ConnectRequest:
    account = FakeAccount()
    return ConnectRequest(DEFAULT_PATH, account.login, password, account.server)


def test_broker_offset_from_fresh_ticks_in_half_hour_steps() -> None:
    assert estimate_broker_offset(int(NOW + 3 * 3600 + 2), NOW) == 3.0
    assert estimate_broker_offset(int(NOW - 5.5 * 3600), NOW) == -5.5
    assert estimate_broker_offset(int(NOW), NOW) == 0.0
    assert estimate_broker_offset(int(NOW + 3 * 3600 - 1200), NOW) is None
    assert estimate_broker_offset(int(NOW + 20 * 3600), NOW) is None
    assert offset_text(3.0) == "server time is UTC+3"
    assert offset_text(-5.5) == "server time is UTC-5:30"
    assert "unknown" in offset_text(None)


def test_diagnostics_cover_history_specs_offset_and_permissions() -> None:
    fake = FakeMT5(now=lambda: NOW, server_offset_hours=2.0)
    report = run_diagnostics(fake, request(), utc_now=lambda: NOW, path_exists=lambda path: True)
    assert report.broker_offset_hours == 2.0
    assert len(report.history) == 3 * len(DIAGNOSTIC_TIMEFRAMES)
    assert {spec.name for spec in report.specs} == {"EURUSD.m", "GBPUSD.m", "XAUUSD.m"}
    text = report.text()
    for expected in (
        "Checklist",
        "server time is UTC+2",
        "EURUSD.m M15: 5,000 bars",
        "XAUUSD.m: digits 2",
        "Ping: 35 ms",
        "Expert Advisors allowed: yes",
        "App running as administrator: unknown",
    ):
        assert expected in text, expected
    assert fake.trading_calls == []


def test_the_report_has_no_password_even_if_it_appears_somewhere() -> None:
    fake = FakeMT5(now=lambda: NOW)
    fake.accounts[0].name = f"Trader password={FakeAccount().password}"
    report = run_diagnostics(fake, request(), utc_now=lambda: NOW, path_exists=lambda path: True)
    assert FakeAccount().password not in report.text()


def test_a_failed_connection_still_gives_a_useful_report() -> None:
    fake = FakeMT5(now=lambda: NOW)
    report = run_diagnostics(
        fake,
        request("wrong-password"),
        utc_now=lambda: NOW,
        path_exists=lambda path: True,
    )
    assert report.history == ()
    assert report.broker_offset_hours is None
    assert "wrong login number, password or server" in report.text()
