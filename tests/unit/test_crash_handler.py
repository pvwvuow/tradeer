import faulthandler
import json
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

from app.__version__ import __version__
from app.observability.crash_handler import (
    FATAL_LOG_NAME,
    CrashInfo,
    CrashReporter,
    CrashTestError,
)
from app.observability.masking import MASK, SecretMasker

TOKEN = "ghp" + "_" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8"
ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


class Ticker:
    """A clock that moves one second forward on every call."""

    def __init__(self) -> None:
        self.moment = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def raised(error: BaseException) -> ExcInfo:
    try:
        raise error
    except BaseException as caught:
        return type(caught), caught, caught.__traceback__


def make_reporter(
    directory: Path,
    masker: SecretMasker | None = None,
    **options: Any,
) -> CrashReporter:
    reporter = CrashReporter(
        directory,
        recent_logs=lambda count: [{"message": f"line before the crash, token={TOKEN}"}],
        masker=masker or SecretMasker(),
        native_crashes=False,
        **options,
    )
    reporter.set_state_provider(lambda: {"page": "logs", "api_key": "k" * 12})
    return reporter


def test_a_report_has_stack_logs_state_versions_and_os() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reporter = make_reporter(Path(tmp))
        path = reporter.report_exception(*raised(ValueError("boom")), source="test")
        assert path is not None
        assert path.name.startswith("crash_")
        assert path.suffix == ".json"
        data = json.loads(path.read_text(encoding="utf-8"))
    assert data["source"] == "test"
    assert data["summary"] == "ValueError: boom"
    assert data["exception"]["type"] == "ValueError"
    assert "raise error" in data["exception"]["stack"]
    assert data["recent_logs"][0]["message"].startswith("line before the crash")
    assert data["state"] == {"page": "logs", "api_key": MASK}
    assert data["versions"]["app"] == __version__
    assert data["os"]["system"]
    assert data["context"]["session_id"]


def test_reports_never_contain_secrets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        masker = SecretMasker()
        masker.register("correct-horse-battery")
        reporter = make_reporter(Path(tmp), masker)
        error = RuntimeError(f"login failed, password correct-horse-battery, token={TOKEN}")
        path = reporter.report_exception(*raised(error), source="test")
        assert path is not None
        text = path.read_text(encoding="utf-8")
    assert TOKEN not in text
    assert "correct-horse-battery" not in text
    assert "k" * 12 not in text
    assert MASK in text


def test_the_thread_hook_writes_a_report_for_a_crashing_thread() -> None:
    received: list[CrashInfo] = []
    with tempfile.TemporaryDirectory() as tmp:
        reporter = make_reporter(Path(tmp))
        reporter.add_listener(received.append)
        reporter.install()
        try:
            worker = threading.Thread(target=_crash, name="worker-1")
            worker.start()
            worker.join()
        finally:
            reporter.uninstall()
        assert reporter.last_report is not None
        data = json.loads(reporter.last_report.read_text(encoding="utf-8"))
    assert data["source"] == "threading.excepthook"
    assert data["thread"] == "worker-1"
    assert data["exception"]["type"].endswith("CrashTestError")
    assert len(received) == 1
    assert received[0].notify_user
    assert received[0].exc_info is not None


def _crash() -> None:
    raise CrashTestError("forced")


def test_the_sys_hook_reports_and_leaves_ctrl_c_to_the_previous_hook() -> None:
    calls: list[type[BaseException]] = []

    def previous(kind: type[BaseException], value: BaseException, tb: object) -> None:
        calls.append(kind)

    original = sys.excepthook
    sys.excepthook = previous
    try:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = make_reporter(Path(tmp))
            reporter.install()
            assert reporter.installed
            sys.excepthook(*raised(KeyboardInterrupt()))
            assert reporter.last_report is None
            sys.excepthook(*raised(OSError("disk")))
            assert reporter.last_report is not None
            reporter.uninstall()
            assert sys.excepthook is previous
    finally:
        sys.excepthook = original
    assert calls == [KeyboardInterrupt]


def test_listener_errors_never_escape_the_hook() -> None:
    def broken(info: CrashInfo) -> None:
        raise RuntimeError("listener bug")

    with tempfile.TemporaryDirectory() as tmp:
        reporter = make_reporter(Path(tmp))
        reporter.add_listener(broken)
        assert reporter.report_exception(*raised(ValueError("x")), source="test") is not None
        reporter.remove_listener(broken)
        reporter.remove_listener(broken)


def test_report_floods_are_throttled() -> None:
    now = [100.0]
    with tempfile.TemporaryDirectory() as tmp:
        reporter = make_reporter(Path(tmp), max_per_minute=2, monotonic=lambda: now[0])
        results = [reporter.report_message("again", source="test") for _ in range(3)]
        assert [result is not None for result in results] == [True, True, False]
        assert reporter.suppressed == 1
        now[0] += 61
        assert reporter.report_message("later", source="test") is not None


def test_only_the_newest_reports_are_kept() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reporter = make_reporter(Path(tmp), keep=3, clock=Ticker())
        paths = [reporter.report_message(f"report {index}", source="test") for index in range(5)]
        remaining = reporter.reports()
    assert remaining == list(reversed(paths[2:]))


def test_message_reports_have_details_but_no_exception() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reporter = make_reporter(Path(tmp))
        path = reporter.report_message("Qt fatal error", source="qt", details={"worker": "ui"})
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
    assert data["exception"] is None
    assert data["details"] == {"worker": "ui"}
    assert data["summary"] == "Qt fatal error"


def test_native_crashes_are_recorded_in_a_file() -> None:
    was_enabled = faulthandler.is_enabled()
    with tempfile.TemporaryDirectory() as tmp:
        reporter = CrashReporter(Path(tmp), masker=SecretMasker())
        reporter.install()
        try:
            assert faulthandler.is_enabled()
            assert (Path(tmp) / FATAL_LOG_NAME).exists()
        finally:
            reporter.uninstall()
    if was_enabled and sys.__stderr__ is not None:
        faulthandler.enable(sys.__stderr__)
