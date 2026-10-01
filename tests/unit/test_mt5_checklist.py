from app.mt5 import api
from app.mt5.checklist import (
    STEPS,
    ChecklistReport,
    CheckStatus,
    ConnectRequest,
    check_terminal_path,
    read_quotes,
    run_checklist,
)
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5

PASSWORD = FakeAccount().password


def request(**changes: object) -> ConnectRequest:
    values: dict[str, object] = {
        "terminal_path": DEFAULT_PATH,
        "login": FakeAccount().login,
        "password": PASSWORD,
        "server": FakeAccount().server,
    }
    values.update(changes)
    return ConnectRequest(**values)  # type: ignore[arg-type]


def check(fake: FakeMT5, req: ConnectRequest | None = None) -> ChecklistReport:
    return run_checklist(fake, req or request(), path_exists=lambda path: True)


def statuses(report: ChecklistReport) -> dict[str, CheckStatus]:
    return {item.key: item.status for item in report.items}


def test_a_healthy_demo_account_passes_every_step_with_real_values() -> None:
    report = check(FakeMT5())
    assert [item.key for item in report.items] == [key for key, _ in STEPS]
    assert set(statuses(report).values()) == {CheckStatus.OK}
    assert report.connected
    assert report.ready_to_trade
    assert not report.analysis_only
    assert report.symbols == ("EURUSD.m", "GBPUSD.m", "XAUUSD.m")
    values = {item.key: item.value for item in report.items}
    assert "DEMO" in values["account"] and "10,000.00 USD" in values["account"]
    assert "EURUSD = EURUSD.m" in values["symbols"]
    assert "EURUSD.m 1.08345 / 1.08352" in values["quotes"]
    assert "build 4755" in values["terminal_running"]
    assert report.account is not None and report.account.login == FakeAccount().login


def test_the_report_never_contains_the_password() -> None:
    report = check(FakeMT5())
    text = "\n".join(report.lines())
    assert PASSWORD not in text
    assert PASSWORD not in repr(request())


def test_a_wrong_password_stops_at_login_with_a_fix() -> None:
    report = check(FakeMT5(), request(password="wrong-password"))
    found = statuses(report)
    assert found["terminal_running"] is CheckStatus.OK
    assert found["login"] is CheckStatus.FAIL
    assert found["account"] is CheckStatus.SKIPPED
    problem = report.first_problem()
    assert problem is not None and "wrong login number, password or server" in problem.value
    assert not report.connected


def test_the_investor_password_connects_read_only() -> None:
    report = check(FakeMT5(), request(password=FakeAccount().investor_password))
    assert report.connected
    assert report.analysis_only
    assert not report.ready_to_trade
    assert statuses(report)["account_trading"] is CheckStatus.WARN


def test_algo_trading_off_and_python_api_disabled_explain_the_button() -> None:
    off = check(FakeMT5(algo_trading=False))
    item = next(item for item in off.items if item.key == "algo_trading")
    assert item.status is CheckStatus.FAIL
    assert "Algo Trading" in item.fix
    assert off.connected and not off.ready_to_trade
    disabled = check(FakeMT5(tradeapi_disabled=True))
    item = next(item for item in disabled.items if item.key == "algo_trading")
    assert "external Python API" in item.fix


def test_ipc_failures_stop_at_the_terminal_step() -> None:
    fake = FakeMT5(initialize_error=(api.RES_E_INTERNAL_FAIL_TIMEOUT, "IPC timeout"))
    report = run_checklist(fake, request(), path_exists=lambda path: True, elevated=True)
    item = next(item for item in report.items if item.key == "terminal_running")
    assert item.status is CheckStatus.FAIL
    assert "as administrator right now" in item.fix
    assert statuses(report)["login"] is CheckStatus.SKIPPED
    assert fake.calls == ["initialize"]


def test_terminal_path_problems_are_caught_before_mt5_is_called() -> None:
    def missing(path: str) -> bool:
        return False

    assert check_terminal_path(DEFAULT_PATH, missing).status is CheckStatus.FAIL
    mt4 = check_terminal_path(r"C:\Program Files\MT4\terminal.exe", lambda path: True)
    assert mt4.status is CheckStatus.FAIL and "MetaTrader 4" in mt4.value
    other = check_terminal_path(r"C:\tools\app.exe", lambda path: True)
    assert other.status is CheckStatus.FAIL
    assert check_terminal_path("", lambda path: True).status is CheckStatus.WARN
    fake = FakeMT5()
    report = run_checklist(fake, request(), path_exists=missing)
    assert fake.calls == []
    assert statuses(report)["terminal_running"] is CheckStatus.SKIPPED


def test_no_login_uses_the_account_already_logged_in() -> None:
    fake = FakeMT5()
    report = check(fake, request(login=None, password="", server=""))
    assert statuses(report)["login"] is CheckStatus.OK
    assert statuses(report)["account"] is CheckStatus.FAIL
    fake.login(FakeAccount().login, PASSWORD)
    again = check(fake, request(login=None, password="", server=""))
    assert again.connected


def test_missing_symbols_closed_market_short_history_and_no_broker() -> None:
    fake = FakeMT5()
    fake.symbols = [symbol for symbol in fake.symbols if not symbol.name.startswith("GBP")]
    report = check(fake)
    assert statuses(report)["symbols"] is CheckStatus.WARN
    assert "not found: GBPUSD" in next(i.value for i in report.items if i.key == "symbols")
    short = check(FakeMT5(maxbars=5000))
    assert statuses(short)["history"] is CheckStatus.WARN
    empty = check(FakeMT5(rates_count=0))
    assert statuses(empty)["history"] is CheckStatus.FAIL
    offline = check(FakeMT5(broker_connected=False))
    assert statuses(offline)["broker_connection"] is CheckStatus.FAIL
    assert statuses(offline)["quotes"] is CheckStatus.WARN
    assert not offline.connected


def test_the_checklist_never_trades() -> None:
    fake = FakeMT5()
    check(fake)
    read_quotes(fake, ("EURUSD.m",))
    assert fake.trading_calls == []
