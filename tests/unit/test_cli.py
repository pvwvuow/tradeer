import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from app.__version__ import __version__
from app.cli import parse_args, run_self_check, self_check_main
from app.main import main


def _fake_importer(missing: set[str]) -> Callable[[str], ModuleType]:
    def importer(name: str) -> ModuleType:
        if name in missing:
            raise ImportError(f"No module named {name!r}")
        module = ModuleType(name)
        module.__dict__["__version__"] = "9.9.9"
        return module

    return importer


def test_parse_args_defaults() -> None:
    options = parse_args([])
    assert not options.self_check
    assert options.report_file is None
    assert options.profile == "default"


def test_parse_args_leaves_qt_arguments_alone() -> None:
    options = parse_args(["--self-check", "-platform", "offscreen", "--profile", "demo"])
    assert options.self_check
    assert options.profile == "demo"


def test_self_check_passes_when_everything_loads() -> None:
    ok, report = run_self_check(_fake_importer(set()), pointer_bits=64, python_version=(3, 11))
    assert ok
    assert "Result: PASS" in report
    assert __version__ in report


def test_self_check_fails_without_metatrader5() -> None:
    importer = _fake_importer({"MetaTrader5"})
    ok, report = run_self_check(importer, pointer_bits=64, python_version=(3, 11))
    assert not ok
    assert "[FAIL] MetaTrader5" in report
    assert "Result: FAIL" in report


def test_self_check_fails_on_32_bit_or_wrong_python() -> None:
    assert not run_self_check(_fake_importer(set()), pointer_bits=32, python_version=(3, 11))[0]
    assert not run_self_check(_fake_importer(set()), pointer_bits=64, python_version=(3, 12))[0]


def test_self_check_main_writes_the_report_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report_file = Path(tmp) / "nested" / "self-check.txt"
        code = main(["--self-check", "--report-file", str(report_file)])
        text = report_file.read_text(encoding="utf-8")
    assert code in (0, 1)
    assert "self-check" in text


@pytest.mark.skipif(sys.platform != "win32", reason="MetaTrader5 ships Windows wheels only")
def test_real_self_check_passes_on_windows() -> None:
    options = parse_args(["--self-check"])
    assert self_check_main(options) == 0
