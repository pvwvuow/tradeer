"""`--backtest` on a FakeMT5 (spec C8): connect, read the history, replay, print the report."""

import tempfile
from pathlib import Path

from app.backtest.command import measured_clock, run_backtest_command
from app.cli import parse_args
from app.mt5.checklist import ConnectRequest
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeMT5
from tests.unit.signal_helpers import MORNING


def test_the_command_reports_a_replay() -> None:
    fake = FakeMT5(now=lambda: float(MORNING))
    account = fake.accounts[0]
    request = ConnectRequest(DEFAULT_PATH, account.login, account.password, account.server)
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        ok = run_backtest_command(
            fake,
            request,
            symbol="EURUSD",
            start="2026-09-28",
            end="2026-09-29",
            strategies=["london_breakout"],
            directory=Path(folder),
            emit=lines.append,
            now=lambda: float(MORNING),
            path_exists=lambda path: True,
        )
        assert (Path(folder) / "backtest_cache").is_dir()
    assert ok, lines
    assert lines[0] == "Backtest (read-only: no orders are sent)"
    assert "EURUSD (EURUSD.m) 2026-09-28..2026-09-29, london_breakout" in lines
    assert any(line.startswith("Trades ") for line in lines)
    assert "Backtest: 100%" in lines and lines[-1] == "Result: DONE"
    assert "order_send" not in fake.calls


def test_a_bad_request_fails_cleanly() -> None:
    fake = FakeMT5(now=lambda: float(MORNING))
    account = fake.accounts[0]
    request = ConnectRequest(DEFAULT_PATH, account.login, account.password, account.server)
    lines: list[str] = []
    ok = run_backtest_command(
        fake,
        request,
        symbol="EURUSD",
        start="2026-09-29",
        end="2026-09-01",
        strategies=[],
        directory=Path(tempfile.gettempdir()),
        emit=lines.append,
        path_exists=lambda path: True,
    )
    assert not ok and lines[-1] == "Result: FAIL"


def test_the_clock_comes_from_a_fresh_tick() -> None:
    fake = FakeMT5(now=lambda: float(MORNING))
    fake.initialize()
    clock, note = measured_clock(fake, "EURUSD.m", float(MORNING))
    assert clock.measured and clock.offset_at(float(MORNING)) == 3.0
    assert note.startswith("broker time UTC+2/+3")
    missing, why = measured_clock(fake, "NOPE", float(MORNING))
    assert not missing.measured and "assumed" in why


def test_the_options_parse() -> None:
    arguments = ["--backtest", "--symbol", "XAUUSD", "--from", "2026-01-01"]
    options = parse_args([*arguments, "--strategies", "a, b"])
    assert options.backtest and options.symbol == "XAUUSD" and options.start == "2026-01-01"
    assert options.end == "" and options.strategies == ("a", "b")
