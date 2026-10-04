"""What a soak report reads (spec G3 phase 14): the saved metrics, health checks, WARNING+
log lines and crash reports of the latest continuous run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.__version__ import __version__
from app.observability.soak import (
    DAY,
    LOOKBACK_DAYS,
    SAMPLE_NAMES,
    Sample,
    SoakInputs,
    SoakResult,
    evaluate_soak,
    find_run,
    report_name,
    soak_markdown,
)
from app.storage.repositories import Store
from app.storage.signal_store import epoch, iso_time

LOST_PATTERN = "MT5 connection lost%"


def _status(details: Any) -> str:
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
        except ValueError:
            return ""
        if isinstance(parsed, Mapping):
            return str(parsed.get("status", ""))
    return ""


def load_samples(store: Store, since: float, until: float) -> list[Sample]:
    marks = ", ".join("?" for _ in SAMPLE_NAMES)
    rows = store.db.query(
        "SELECT time, name, value, details_json FROM performance_metrics "
        f"WHERE time >= ? AND time <= ? AND name IN ({marks}) ORDER BY time",
        (iso_time(since), iso_time(until), *SAMPLE_NAMES),
    )
    return [
        Sample(
            epoch(row.get("time")),
            str(row.get("name")),
            float(row["value"]),
            _status(row.get("details_json")),
        )
        for row in rows
        if row.get("value") is not None
    ]


def run_times(store: Store, since: float, until: float) -> list[float]:
    rows = store.db.query(
        "SELECT DISTINCT time FROM performance_metrics WHERE time >= ? AND time <= ?",
        (iso_time(since), iso_time(until)),
    )
    return [epoch(row.get("time")) for row in rows]


def _count(store: Store, sql: str, parameters: tuple[Any, ...]) -> int:
    return int(store.db.scalar(sql, parameters) or 0)


def crash_count(crash_dir: Path, start: float, end: float) -> int:
    try:
        reports = [path for path in crash_dir.glob("crash_*.json") if path.is_file()]
    except OSError:
        return 0
    found = 0
    for path in reports:
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if start <= modified <= end:
            found += 1
    return found


def soak_inputs(store: Store, crash_dir: Path, now: float) -> SoakInputs:
    """The latest continuous run of the last `LOOKBACK_DAYS` days, with what happened in it."""
    since = now - LOOKBACK_DAYS * DAY
    start, end, gap = find_run(run_times(store, since, now))
    if not end:
        return SoakInputs(start=0.0, end=0.0)
    window = (iso_time(start), iso_time(end))
    logs = "SELECT COUNT(*) FROM app_logs WHERE time >= ? AND time <= ? AND level = ?"
    lost = "SELECT COUNT(*) FROM app_logs WHERE time >= ? AND time <= ? AND message LIKE ?"
    frozen = (
        "SELECT COUNT(*) FROM health_checks WHERE time >= ? AND time <= ? "
        "AND name = 'workers' AND LOWER(status) = 'critical'"
    )
    return SoakInputs(
        start=start,
        end=end,
        samples=load_samples(store, start, end),
        critical_logs=_count(store, logs, (*window, "CRITICAL")),
        error_logs=_count(store, logs, (*window, "ERROR")),
        disconnects=_count(store, lost, (*window, LOST_PATTERN)),
        frozen_workers=_count(store, frozen, window),
        crash_reports=crash_count(crash_dir, start, end),
        longest_gap=gap,
    )


def create_soak_report(store: Store, crash_dir: Path, folder: Path, now: float) -> SoakResult:
    """Evaluate the latest run and write `soak-<UTC time>.md` into `folder`."""
    report = evaluate_soak(soak_inputs(store, crash_dir, now))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / report_name(now)
    path.write_text(soak_markdown(report, now, __version__), encoding="utf-8")
    return SoakResult(path, report)
