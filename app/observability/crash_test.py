"""`--crash-test`: proof that a forced exception produces a masked crash report.

This is the Phase 2 acceptance test (spec G3) as a command the user can run on any build:
it logs two throwaway secrets, crashes a worker thread on purpose, and then checks that a
crash report was written and that neither secret appears in the report or the log files.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.observability.crash_handler import CrashReporter, CrashTestError
from app.observability.masking import MASK, SecretMasker

CRASH_THREAD_NAME = "crash-test"


@dataclass(frozen=True)
class CrashTestResult:
    ok: bool
    report_path: Path | None
    checks: tuple[tuple[str, bool], ...]

    def lines(self) -> list[str]:
        lines = [f"[{'OK' if passed else 'FAIL'}] {name}" for name, passed in self.checks]
        if self.report_path is not None:
            lines.append(f"Crash report: {self.report_path}")
        lines.append("Result: PASS" if self.ok else "Result: FAIL")
        return lines


def throwaway_secrets() -> tuple[str, str]:
    """A random key in a known token format and a random password. Neither is real."""
    return f"sk-crashtest{secrets.token_hex(12)}", f"pw-{secrets.token_urlsafe(12)}"


def _explode(api_key: str, password: str) -> None:
    raise CrashTestError(f"Deliberate crash test (token={api_key}, password {password})")


def _read_logs(log_dir: Path) -> str:
    if not log_dir.exists():
        return ""
    parts = [
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(log_dir.rglob("*"))
        if path.is_file() and path.suffix in (".jsonl", ".log")
    ]
    return "\n".join(parts)


def _load(path: Path | None) -> tuple[str, dict[str, Any]]:
    if path is None or not path.exists():
        return "", {}
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        return text, {}
    return text, data if isinstance(data, dict) else {}


def run_crash_test(
    reporter: CrashReporter,
    masker: SecretMasker,
    emit: Callable[[str], None],
    flush: Callable[[], None],
    log_dir: Path,
) -> CrashTestResult:
    """`reporter` must be installed; `emit` logs one line; `flush` waits for the log files."""
    api_key, password = throwaway_secrets()
    masker.register(password)
    try:
        emit(f"Crash test: logging api_key={api_key} and the password {password}")
        before = reporter.last_report
        worker = threading.Thread(
            target=_explode,
            args=(api_key, password),
            name=CRASH_THREAD_NAME,
        )
        worker.start()
        worker.join(timeout=10)
        flush()
        report = reporter.last_report if reporter.last_report != before else None
        text, data = _load(report)
        exception = data.get("exception") or {}
        logs = _read_logs(log_dir)
        checks = (
            ("the crash hook wrote a crash report", bool(text)),
            ("the report names the forced exception", "CrashTestError" in str(exception)),
            ("the report contains the stack trace", "_explode" in str(exception.get("stack"))),
            ("the report contains recent log lines", bool(data.get("recent_logs"))),
            ("the report contains versions and OS details", "versions" in data and "os" in data),
            ("no secret appears in the report", bool(text) and _clean(text, api_key, password)),
            ("no secret appears in the log files", bool(logs) and _clean(logs, api_key, password)),
        )
    finally:
        masker.forget(password)
    return CrashTestResult(all(passed for _, passed in checks), report, checks)


def _clean(text: str, *values: str) -> bool:
    return MASK in text and not any(value in text for value in values)
