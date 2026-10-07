"""What a soak report reads (spec G3 phase 14): the saved metrics, health checks, WARNING+
log lines and crash reports of the newest runs that add up to 24 hours."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.__version__ import __version__
from app.observability.soak import (
    DAY,
    LOOKBACK_DAYS,
    SAMPLE_NAMES,
    Run,
    Sample,
    SoakInputs,
    SoakResult,
    evaluate_soak,
    find_runs,
    pick_runs,
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


def _total(store: Store, runs: Sequence[Run], sql: str, *extra: Any) -> int:
    return sum(
        _count(store, sql, (iso_time(start), iso_time(end), *extra)) for start, end, _ in runs
    )


def soak_inputs(store: Store, crash_dir: Path, now: float) -> SoakInputs:
    """The newest runs of the last `LOOKBACK_DAYS` days that add up to 24 hours (each of
    4 hours or more), with what happened in them."""
    since = now - LOOKBACK_DAYS * DAY
    runs = pick_runs(find_runs(run_times(store, since, now)))
    if not runs:
        return SoakInputs(start=0.0, end=0.0)
    logs = "SELECT COUNT(*) FROM app_logs WHERE time >= ? AND time <= ? AND level = ?"
    lost = "SELECT COUNT(*) FROM app_logs WHERE time >= ? AND time <= ? AND message LIKE ?"
    frozen = (
        "SELECT COUNT(*) FROM health_checks WHERE time >= ? AND time <= ? "
        "AND name = 'workers' AND LOWER(status) = 'critical'"
    )
    samples = [sample for start, end, _ in runs for sample in load_samples(store, start, end)]
    return SoakInputs(
        start=runs[0][0],
        end=runs[-1][1],
        samples=samples,
        critical_logs=_total(store, runs, logs, "CRITICAL"),
        error_logs=_total(store, runs, logs, "ERROR"),
        disconnects=_total(store, runs, lost, LOST_PATTERN),
        frozen_workers=_total(store, runs, frozen),
        crash_reports=sum(crash_count(crash_dir, start, end) for start, end, _ in runs),
        longest_gap=max(gap for _, _, gap in runs),
        runs=tuple((start, end) for start, end, _ in runs),
    )


def create_soak_report(store: Store, crash_dir: Path, folder: Path, now: float) -> SoakResult:
    """Evaluate the newest runs and write `soak-<UTC time>.md` into `folder`."""
    report = evaluate_soak(soak_inputs(store, crash_dir, now))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / report_name(now)
    path.write_text(soak_markdown(report, now, __version__), encoding="utf-8")
    return SoakResult(path, report)
