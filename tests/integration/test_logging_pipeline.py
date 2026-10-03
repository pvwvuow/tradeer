"""The real loguru pipeline end to end. Runs in CI (needs loguru installed)."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

from app.main import main
from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.context import trace
from app.observability.files import ALL_LOG_NAME, LogFilePolicy, LogFileWriter
from app.observability.levels import LevelRegistry, LogLevel
from app.observability.logger import LogPipeline, audit, get_logger
from app.observability.masking import MASK, SecretMasker

TOKEN = "ghp" + "_" + "M1n2B3v4C5x6Z7l8K9j0H1g2F3d4S5a6P7o8"
PASSWORD = "correct-horse-battery"


def start_pipeline(root: Path, registry: LevelRegistry | None = None) -> LogPipeline:
    masker = SecretMasker()
    masker.register(PASSWORD)
    writer = LogFileWriter(root / "logs", LogFilePolicy(retention_days=100_000))
    pipeline = LogPipeline(writer, RecentLogBuffer(), registry or LevelRegistry(), masker)
    pipeline.start()
    return pipeline


def entries(root: Path, category: str) -> list[dict[str, Any]]:
    files = sorted((root / "logs" / category).glob("*.jsonl"))
    lines = [line for path in files for line in path.read_text(encoding="utf-8").splitlines()]
    return [json.loads(line) for line in lines if line]


def all_log_files_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "logs").rglob("*")
        if path.is_file() and path.suffix in (".jsonl", ".log")
    )


def test_records_reach_category_files_all_log_and_the_tail_masked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pipeline = start_pipeline(root)
        try:
            with trace(symbol="EURUSD") as trace_id:
                get_logger(LogCategory.MT5).info("Login with {} and token={}", PASSWORD, TOKEN)
            get_logger(LogCategory.RISK).bind(api_key="k" * 12, attempt=2).warning("Risk check")
            pipeline.flush()
        finally:
            pipeline.stop()
        mt5 = entries(root, "mt5")
        risk = entries(root, "risk")
        everything = all_log_files_text(root)
        readable = (root / "logs" / ALL_LOG_NAME).read_text(encoding="utf-8")
        tail = pipeline.buffer.last(10)
    assert [entry["message"] for entry in mt5] == [f"Login with {MASK} and token={MASK}"]
    assert mt5[0]["trace_id"] == trace_id
    assert mt5[0]["symbol"] == "EURUSD"
    assert mt5[0]["category"] == "mt5"
    assert mt5[0]["time"].endswith("Z")
    assert risk[0]["level"] == "WARNING"
    assert risk[0]["context"] == {"api_key": MASK, "attempt": 2}
    assert PASSWORD not in everything
    assert TOKEN not in everything
    assert "k" * 12 not in everything
    assert [entry["category"] for entry in tail] == ["mt5", "risk"]
    assert "| mt5         | Login with ***" in readable
    assert "Risk check" in readable


def test_category_levels_and_debug_mode_apply_at_runtime() -> None:
    registry = LevelRegistry(overrides={LogCategory.MT5: LogLevel.WARNING})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pipeline = start_pipeline(root, registry)
        try:
            log = get_logger(LogCategory.MT5)
            log.info("dropped: below the mt5 level")
            log.warning("kept")
            get_logger(LogCategory.APP).debug("dropped: debug mode is off")
            registry.enable_debug(5)
            get_logger(LogCategory.APP).debug("kept: debug mode is on")
            pipeline.flush()
        finally:
            pipeline.stop()
        messages = [entry["message"] for entry in pipeline.buffer.last(10)]
        app_entries = entries(root, "app")
    assert messages == ["kept", "kept: debug mode is on"]
    assert [entry["level"] for entry in app_entries] == ["DEBUG"]


def test_exceptions_are_logged_with_a_masked_stack() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pipeline = start_pipeline(root)
        try:
            try:
                raise ValueError(f"bad token={TOKEN}")
            except ValueError:
                get_logger(LogCategory.EXECUTION).exception("Order failed")
            pipeline.flush()
        finally:
            pipeline.stop()
        execution = entries(root, "execution")
        everything = all_log_files_text(root)
    assert execution[0]["level"] == "ERROR"
    assert execution[0]["exception_type"] == "ValueError"
    assert "Traceback" in execution[0]["stack_trace"]
    assert TOKEN not in everything
    assert "ValueError" in everything


def test_audit_entries_record_before_and_after() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pipeline = start_pipeline(root)
        try:
            audit("log_level_changed", before={"level": "INFO"}, after={"level": "DEBUG"})
            pipeline.flush()
        finally:
            pipeline.stop()
        records = entries(root, "audit")
    assert records[0]["message"] == "Audit: log_level_changed (level INFO \u2192 DEBUG)"
    assert records[0]["context"]["before"] == {"level": "INFO"}
    assert records[0]["context"]["after"] == {"level": "DEBUG"}
    assert records[0]["context"]["source"] == "user"


def test_the_crash_test_command_passes_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "crash-test.txt"
        with mock.patch.dict(os.environ, {"APPDATA": tmp, "XDG_CONFIG_HOME": tmp}):
            code = main(["--crash-test", "--profile", "pytest", "--report-file", str(report)])
        text = report.read_text(encoding="utf-8")
        crash_reports = list(Path(tmp).rglob("crash_*.json"))
    assert code == 0, text
    assert "Result: PASS" in text
    assert len(crash_reports) == 1
