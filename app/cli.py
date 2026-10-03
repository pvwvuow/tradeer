"""Command-line options and the build self-check (spec I2 `--self-check`)."""

from __future__ import annotations

import argparse
import importlib
import platform
import struct
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from app.__version__ import __version__

Importer = Callable[[str], ModuleType]

REQUIRED_PYTHON = (3, 11)
HELPER_CHECK_SECONDS = 60.0

# (module to import, attribute that holds its version or None)
REQUIRED_MODULES: tuple[tuple[str, str | None], ...] = (
    ("MetaTrader5", "__version__"),
    ("PySide6", "__version__"),
    ("PySide6.QtWidgets", None),
    ("sqlite3", "sqlite_version"),
    ("httpx", "__version__"),
    ("pyqtgraph", "__version__"),
)


@dataclass(frozen=True)
class CliOptions:
    self_check: bool
    report_file: Path | None
    profile: str
    crash_test: bool = False
    mt5_smoke_test: bool = False
    mt5_trade_test: bool = False
    symbol: str = "EURUSD"
    backtest: bool = False
    start: str = ""
    end: str = ""
    strategies: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        status = "OK" if self.ok else "FAIL"
        return f"[{status}] {self.name}: {self.detail}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="MT5TradingWorkstation",
        description="MT5 Trading Workstation",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="verify that MetaTrader5 and Qt load, then exit with code 0 (pass) or 1 (fail)",
    )
    parser.add_argument(
        "--crash-test",
        action="store_true",
        help="force a crash in a worker thread, verify the masked crash report, exit 0 or 1",
    )
    parser.add_argument(
        "--mt5-smoke-test",
        action="store_true",
        help="connect with the saved profile, print account, prices, bars and deals (read-only)",
    )
    parser.add_argument(
        "--mt5-trade-test",
        action="store_true",
        help="DEMO only: open, modify and close one minimum-lot trade, read the deal back",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="read MT5 history and replay the strategies on it, print the report (no orders)",
    )
    parser.add_argument(
        "--symbol",
        default="EURUSD",
        help="symbol for --mt5-trade-test and --backtest (default EURUSD; suffixes are found)",
    )
    parser.add_argument("--from", dest="start", default="", help="--backtest start, YYYY-MM-DD")
    parser.add_argument("--to", dest="end", default="", help="--backtest last day, YYYY-MM-DD")
    parser.add_argument(
        "--strategies",
        default="",
        help="--backtest strategies, comma separated (default: every built-in strategy)",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="also write the report of a test command to this file",
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="account profile name; run one app instance per profile",
    )
    return parser


def parse_args(argv: Sequence[str]) -> CliOptions:
    # Unknown arguments are left for Qt (for example `-platform offscreen`).
    namespace, _unknown = build_parser().parse_known_args(list(argv))
    return CliOptions(
        self_check=bool(namespace.self_check),
        report_file=namespace.report_file,
        profile=str(namespace.profile),
        crash_test=bool(namespace.crash_test),
        mt5_smoke_test=bool(namespace.mt5_smoke_test),
        mt5_trade_test=bool(namespace.mt5_trade_test),
        symbol=str(namespace.symbol),
        backtest=bool(namespace.backtest),
        start=str(namespace.start),
        end=str(namespace.end),
        strategies=tuple(
            name.strip() for name in str(namespace.strategies).split(",") if name.strip()
        ),
    )


def _check_python(pointer_bits: int, version: tuple[int, int]) -> CheckResult:
    ok = pointer_bits == 64 and version == REQUIRED_PYTHON
    detail = f"Python {version[0]}.{version[1]}, {pointer_bits}-bit (needs 3.11, 64-bit)"
    return CheckResult("python", ok, detail)


def _check_module(importer: Importer, name: str, version_attr: str | None) -> CheckResult:
    try:
        module = importer(name)
    except Exception as error:
        return CheckResult(name, False, f"cannot be loaded: {error}")
    if version_attr is None:
        return CheckResult(name, True, "loaded")
    return CheckResult(name, True, f"version {getattr(module, version_attr, 'unknown')}")


def bundled_files() -> tuple[tuple[str, Path], ...]:
    """Data files the build must contain (PyInstaller `--add-data`)."""
    calendar = Path(__file__).resolve().parent / "calendar" / "mql5" / "CalendarExporter.mq5"
    return (("calendar exporter", calendar),)


def _check_file(name: str, path: Path) -> CheckResult:
    if path.is_file():
        return CheckResult(name, True, f"{path.name} included")
    return CheckResult(name, False, f"{path} is missing from the build")


def check_mt5_helper() -> CheckResult:
    """Start the MT5 helper process once, exactly as the app does (ADR 46)."""
    from app.mt5.errors import MT5Error
    from app.mt5.terminal_process import MT5Process

    name = "MT5 helper process"
    process = MT5Process(start_timeout=HELPER_CHECK_SECONDS)
    try:
        pid, version = process.ping()
    except MT5Error as error:
        detail = f"{error.title}: {error.detail}" if error.detail else error.title
        return CheckResult(name, False, detail)
    finally:
        process.close()
    return CheckResult(name, True, f"started as process {pid}, MetaTrader5 {version} loaded")


def check_ml_library() -> CheckResult:
    """Train and reload a tiny LightGBM model, exactly as the Model page does (Phase 11)."""
    import numpy as np

    from app.ml.model import LightGBMFactory, LightGBMParams, lightgbm_version

    name = "ML library"
    try:
        rng = np.random.default_rng(1)
        x = rng.normal(0.0, 1.0, (200, 3))
        y = (x[:, 0] > 0).astype(np.float64)
        factory = LightGBMFactory(LightGBMParams(num_boost_round=5, min_data_in_leaf=5))
        model = factory.load(factory.fit(x, y).dump())
        contributions = model.contributions(x[:2])
    except Exception as error:
        return CheckResult(name, False, f"{type(error).__name__}: {error}")
    shape = "x".join(str(size) for size in contributions.shape)
    return CheckResult(name, True, f"LightGBM {lightgbm_version()} trains and explains ({shape})")


def run_self_check(
    importer: Importer = importlib.import_module,
    pointer_bits: int | None = None,
    python_version: tuple[int, int] | None = None,
    extra_checks: Sequence[Callable[[], CheckResult]] = (),
) -> tuple[bool, str]:
    bits = struct.calcsize("P") * 8 if pointer_bits is None else pointer_bits
    version = python_version or (sys.version_info[0], sys.version_info[1])
    results = [_check_python(bits, version)]
    results.extend(_check_module(importer, name, attr) for name, attr in REQUIRED_MODULES)
    results.extend(_check_file(name, path) for name, path in bundled_files())
    results.extend(check() for check in extra_checks)
    ok = all(result.ok for result in results)
    lines = [f"MT5 Trading Workstation {__version__} self-check on {platform.platform()}"]
    lines.extend(result.line() for result in results)
    lines.append("Result: PASS" if ok else "Result: FAIL")
    return ok, "\n".join(lines)


def emit_report(report: str, report_file: Path | None) -> None:
    """Print a report and optionally save it (windowed builds have no console)."""
    print(report)
    if report_file is not None:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(report + "\n", encoding="utf-8")


def self_check_main(options: CliOptions) -> int:
    ok, report = run_self_check(extra_checks=(check_mt5_helper, check_ml_library))
    emit_report(report, options.report_file)
    return 0 if ok else 1
