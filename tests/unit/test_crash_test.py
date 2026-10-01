import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any

from app.observability.buffer import RecentLogBuffer
from app.observability.crash_handler import CrashInfo, CrashReporter
from app.observability.crash_test import run_crash_test
from app.observability.files import LogFileWriter
from app.observability.levels import LevelRegistry, LogLevel
from app.observability.masking import SecretMasker
from app.observability.records import (
    CATEGORY_KEY,
    DROP_KEY,
    ENTRY_KEY,
    JSON_KEY,
    LINE_KEY,
    RecordPatcher,
)

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


class Pipeline:
    """The loguru pipeline without loguru: patcher, in-memory tail and log files."""

    def __init__(self, root: Path, masker: SecretMasker) -> None:
        self.buffer = RecentLogBuffer()
        self.writer = LogFileWriter(root / "logs")
        self.patcher = RecordPatcher(LevelRegistry(), masker)

    def emit(self, message: str, level: str = "INFO", exception: ExcInfo | None = None) -> None:
        record: dict[str, Any] = {
            "time": datetime.now(UTC),
            "level": SimpleNamespace(name=level, no=int(LogLevel[level])),
            "message": message,
            "name": "tests",
            "module": "tests",
            "function": "emit",
            "line": 1,
            "thread": SimpleNamespace(name="MainThread", id=1),
            "extra": {CATEGORY_KEY: "app"},
            "exception": exception,
        }
        self.patcher(record)
        extra = record["extra"]
        if DROP_KEY not in extra:
            self.buffer.append(extra[ENTRY_KEY])
            self.writer.write(extra[CATEGORY_KEY], extra[JSON_KEY], extra[LINE_KEY])

    def log_crash(self, info: CrashInfo) -> None:
        self.emit(f"Uncaught error: {info.summary}", "CRITICAL", info.exc_info)


def run_with(masker: SecretMasker, root: Path) -> tuple[bool, list[str]]:
    pipeline = Pipeline(root, masker)
    reporter = CrashReporter(
        root / "crash_reports",
        recent_logs=pipeline.buffer.last,
        masker=masker,
        native_crashes=False,
    )
    reporter.add_listener(pipeline.log_crash)
    reporter.install()
    try:
        result = run_crash_test(reporter, masker, pipeline.emit, lambda: None, root / "logs")
    finally:
        reporter.uninstall()
        pipeline.writer.close()
    return result.ok, result.lines()


def test_the_crash_test_passes_when_masking_works() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, lines = run_with(SecretMasker(), Path(tmp))
    assert ok, lines
    assert lines[-1] == "Result: PASS"
    assert any(line.startswith("Crash report: ") for line in lines)


class LeakyMasker(SecretMasker):
    def mask(self, text: str) -> str:
        return text


def test_the_crash_test_fails_when_secrets_leak() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok, lines = run_with(LeakyMasker(), Path(tmp))
    assert not ok
    assert "[FAIL] no secret appears in the report" in lines
    assert "[FAIL] no secret appears in the log files" in lines
    assert lines[-1] == "Result: FAIL"
