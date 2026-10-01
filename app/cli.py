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

# (module to import, attribute that holds its version or None)
REQUIRED_MODULES: tuple[tuple[str, str | None], ...] = (
    ("MetaTrader5", "__version__"),
    ("PySide6", "__version__"),
    ("PySide6.QtWidgets", None),
)


@dataclass(frozen=True)
class CliOptions:
    self_check: bool
    report_file: Path | None
    profile: str
    crash_test: bool = False


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
        "--report-file",
        type=Path,
        default=None,
        help="also write the self-check or crash-test report to this file",
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


def run_self_check(
    importer: Importer = importlib.import_module,
    pointer_bits: int | None = None,
    python_version: tuple[int, int] | None = None,
) -> tuple[bool, str]:
    bits = struct.calcsize("P") * 8 if pointer_bits is None else pointer_bits
    version = python_version or (sys.version_info[0], sys.version_info[1])
    results = [_check_python(bits, version)]
    results.extend(_check_module(importer, name, attr) for name, attr in REQUIRED_MODULES)
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
    ok, report = run_self_check()
    emit_report(report, options.report_file)
    return 0 if ok else 1
