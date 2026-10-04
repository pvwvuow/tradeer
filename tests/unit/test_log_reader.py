"""Saved log files read back for the Logs page (spec F3 page 12): time ranges, traces and
the export, plus the page's new filters."""

import csv
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.levels import LogLevel
from app.observability.log_reader import (
    entry_time,
    export_entries,
    log_files,
    merge_entries,
    read_entries,
    timeline_text,
)
from app.ui.log_filter import LogFilter, make_matcher

NOW = 1_790_000_000.0  # 2026-09-21 14:13:20 UTC


def at(seconds: float) -> str:
    moment = datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds")
    return moment.replace("+00:00", "Z")


def line(seconds: float, message: str, **fields: Any) -> dict[str, Any]:
    values = {"time": at(seconds), "level": "INFO", "category": "app", "message": message}
    values.update(fields)
    return values


def write_logs(root: Path) -> None:
    app = root / "app"
    app.mkdir(parents=True)
    today = [
        line(NOW - 60, "started"),
        line(NOW - 30, "signal found", category="strategy", trace_id="t1", symbol="EURUSD"),
        line(NOW - 28.5, "order sent", category="execution", trace_id="t1", level="WARNING"),
    ]
    text = "".join(json.dumps(item) + "\n" for item in today) + "not json\n\n[1, 2]\n"
    (app / "2026-09-21.jsonl").write_bytes(text.encode("utf-8"))
    os.utime(app / "2026-09-21.jsonl", (NOW, NOW))
    mt5 = root / "mt5"
    mt5.mkdir()
    old = json.dumps(line(NOW - 2 * 86_400, "connected", category="mt5")) + "\n"
    archive = mt5 / "2026-09-19.20260920-000000-000001.jsonl.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("2026-09-19.20260920-000000-000001.jsonl", old)
    os.utime(archive, (NOW - 86_400, NOW - 86_400))
    (root / "all.log").write_bytes(b"readable copy, never parsed\n")


def test_saved_files_plain_and_zipped_are_read_in_time_order(tmp_path: Path) -> None:
    write_logs(tmp_path)
    assert [path.name for path in log_files(tmp_path)] == [
        "2026-09-21.jsonl",
        "2026-09-19.20260920-000000-000001.jsonl.zip",
    ]
    found = read_entries(tmp_path)
    assert [entry["message"] for entry in found] == [
        "connected",
        "started",
        "signal found",
        "order sent",
    ]
    recent = read_entries(tmp_path, since=NOW - 3600)
    assert [entry["message"] for entry in recent] == ["started", "signal found", "order sent"]
    assert [entry["message"] for entry in read_entries(tmp_path, limit=2)] == [
        "signal found",
        "order sent",
    ]
    trace = read_entries(tmp_path, contains="t1")
    assert [entry["trace_id"] for entry in trace] == ["t1", "t1"]
    warnings = make_matcher(LogFilter(min_level=LogLevel.WARNING))[0]
    assert [entry["message"] for entry in read_entries(tmp_path, keep=warnings)] == [
        "order sent",
    ]
    assert read_entries(tmp_path / "missing") == []


def test_the_page_filters_symbol_strategy_and_time() -> None:
    entries = [
        line(NOW - 30, "a", symbol="EURUSD.m", strategy="trend_pullback"),
        line(NOW - 7200, "b", symbol="GBPUSD", strategy="london_breakout"),
        {"message": "no time"},
    ]

    def shown(log_filter: LogFilter) -> list[str]:
        matches = make_matcher(log_filter)[0]
        return [str(entry["message"]) for entry in entries if matches(entry)]

    assert shown(LogFilter(symbol="eur")) == ["a"]
    assert shown(LogFilter(strategy="London")) == ["b"]
    assert shown(LogFilter(since=NOW - 3600)) == ["a"]
    assert shown(LogFilter(until=NOW - 3600)) == ["b"]
    assert shown(LogFilter()) == ["a", "b", "no time"]
    assert entry_time(entries[0]) == NOW - 30
    assert entry_time({"time": "yesterday"}) is None and entry_time({}) is None


def test_merge_keeps_each_line_once_in_time_order() -> None:
    first = line(NOW - 10, "one")
    second = line(NOW - 5, "two")
    merged = merge_entries([second, first], [dict(first)], limit=10)
    assert [entry["message"] for entry in merged] == ["one", "two"]
    assert len(merge_entries([first, second], limit=1)) == 1


def test_the_trace_timeline() -> None:
    entries = [
        line(NOW - 30, "signal found", category="strategy", trace_id="t1"),
        line(NOW - 28.5, "order sent\nretcode 10009", category="execution", trace_id="t1"),
    ]
    text = timeline_text("t1", entries)
    assert text.startswith("Trace t1: 2 lines over 1.500 s")
    assert "+1.500 s" in text and "order sent" in text and "10009" not in text
    assert timeline_text("t9", []) == "Trace t9: no lines found."


def test_the_export_writes_json_lines_and_csv(tmp_path: Path) -> None:
    entries = [line(NOW - 30, "signal, found", symbol="EURUSD", trace_id="t1")]
    json_path, csv_path = export_entries(entries, tmp_path / "exports", NOW)
    assert json_path.name == "logs-20260921-141320.jsonl"
    assert json.loads(json_path.read_text(encoding="utf-8").strip())["trace_id"] == "t1"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][:3] == ["time", "level", "category"]
    assert rows[1][3] == "EURUSD" and rows[1][-1] == "signal, found"
