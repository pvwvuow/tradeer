"""The soak test report (spec G3 phase 14): did a long run on demo stay within the budgets?

Run the app on a demo account for 24 hours or more, then press Health > Create soak report.
The report finds the latest continuous run in what the app saved (performance metrics every
15 minutes and on changes, health checks, WARNING+ log lines, crash reports) and checks it
against the budgets of spec D4:

1. Duration: the run lasted at least 24 hours (no gap over 35 minutes between samples; a
   restart for an update is no gap).
2. Memory: the working set stayed under 500 MB.
3. No leak: after the first hour, memory grew less than 50 MB per day (least squares).
4. CPU: the average stayed under 3 % of the PC.
5. MT5 calls: the p95 stayed under 1 s.
6. Bar processing: no sample over its budget.
7. Stability: no CRITICAL log line, no crash report and no frozen worker.

Errors and lost MT5 connections are listed for information. Pure: plain values in, a
report and its Markdown out.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HOUR = 3600.0
DAY = 86_400.0
MIN_HOURS = 24.0
MAX_GAP_SECONDS = 35 * 60.0
WARMUP_SECONDS = HOUR
MEMORY_BUDGET_MB = 500.0
LEAK_MB_PER_DAY = 50.0
CPU_BUDGET_PERCENT = 3.0
MT5_BUDGET_MS = 1000.0
LOOKBACK_DAYS = 7
REPORT_FOLDER = "reports"
SAMPLE_NAMES = ("memory_mb", "cpu_percent", "mt5_ms_p95", "bar_ms_p95")
TABLE_NAMES = {
    "memory_mb": ("Memory", "MB"),
    "cpu_percent": ("CPU", "%"),
    "mt5_ms_p95": ("MT5 call p95", "ms"),
    "bar_ms_p95": ("Closed-bar processing p95", "ms"),
}


@dataclass(frozen=True)
class Sample:
    time: float  # UTC seconds
    name: str
    value: float
    status: str = ""


@dataclass(frozen=True)
class SoakInputs:
    start: float
    end: float
    samples: Sequence[Sample] = ()
    critical_logs: int = 0
    error_logs: int = 0
    disconnects: int = 0
    frozen_workers: int = 0
    crash_reports: int = 0
    longest_gap: float = 0.0


@dataclass(frozen=True)
class SoakCheck:
    name: str
    passed: bool
    value: str
    needed: str


@dataclass(frozen=True)
class SoakReport:
    start: float
    end: float
    longest_gap: float
    checks: tuple[SoakCheck, ...]
    notes: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    @property
    def hours(self) -> float:
        return max(0.0, self.end - self.start) / HOUR

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def summary(self) -> str:
        done = sum(1 for check in self.checks if check.passed)
        result = "PASS" if self.passed else "FAIL"
        return (
            f"Soak test {result}: {done} of {len(self.checks)} checks passed over "
            f"{self.hours:,.1f} hours"
        )


@dataclass(frozen=True)
class SoakResult:
    path: Path
    report: SoakReport

    @property
    def text(self) -> str:
        return f"{self.report.summary}. Report saved: {self.path}"


def utc_text(seconds: float) -> str:
    if not seconds:
        return "n/a"
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")


def find_run(
    times: Sequence[float],
    max_gap: float = MAX_GAP_SECONDS,
) -> tuple[float, float, float]:
    """The latest continuous run: (start, end, longest gap inside it); zeros without samples."""
    ordered = sorted(set(times))
    if not ordered:
        return 0.0, 0.0, 0.0
    start = ordered[-1]
    longest = 0.0
    for earlier, later in zip(reversed(ordered[:-1]), reversed(ordered[1:]), strict=True):
        gap = later - earlier
        if gap > max_gap:
            break
        start = earlier
        longest = max(longest, gap)
    return start, ordered[-1], longest


def slope_per_day(points: Sequence[tuple[float, float]]) -> float | None:
    """Least-squares slope of (time, value) in value per day; None for fewer than 3 points."""
    if len(points) < 3:
        return None
    count = float(len(points))
    mean_t = sum(t for t, _ in points) / count
    mean_v = sum(v for _, v in points) / count
    spread = sum((t - mean_t) ** 2 for t, _ in points)
    if spread <= 0:
        return None
    covariance = sum((t - mean_t) * (v - mean_v) for t, v in points)
    return covariance / spread * DAY


def _series(inputs: SoakInputs, name: str) -> list[Sample]:
    found = [
        sample
        for sample in inputs.samples
        if sample.name == name
        and inputs.start <= sample.time <= inputs.end
        and math.isfinite(sample.value)
    ]
    return sorted(found, key=lambda sample: sample.time)


def _number(value: float | None, unit: str, digits: int = 0) -> str:
    if value is None:
        return "not measured"
    return f"{value:,.{digits}f} {unit}".strip()


def evaluate_soak(inputs: SoakInputs) -> SoakReport:
    hours = max(0.0, inputs.end - inputs.start) / HOUR
    gap_minutes = inputs.longest_gap / 60.0
    memory = _series(inputs, "memory_mb")
    cpu = _series(inputs, "cpu_percent")
    mt5 = _series(inputs, "mt5_ms_p95")
    bars = _series(inputs, "bar_ms_p95")
    memory_max = max((s.value for s in memory), default=None)
    settled = [(s.time, s.value) for s in memory if s.time >= inputs.start + WARMUP_SECONDS]
    growth = slope_per_day(settled)
    cpu_mean = sum(s.value for s in cpu) / len(cpu) if cpu else None
    mt5_max = max((s.value for s in mt5), default=None)
    bars_max = max((s.value for s in bars), default=None)
    bars_over = sum(1 for s in bars if s.status == "warning")
    trouble = inputs.critical_logs + inputs.crash_reports + inputs.frozen_workers
    checks = (
        SoakCheck(
            "Duration",
            hours >= MIN_HOURS,
            f"{hours:,.1f} hours, longest gap {gap_minutes:,.0f} min",
            f">= {MIN_HOURS:g} hours without a gap over {MAX_GAP_SECONDS / 60:.0f} min",
        ),
        SoakCheck(
            "Memory",
            memory_max is not None and memory_max < MEMORY_BUDGET_MB,
            _number(memory_max, "MB at most"),
            f"< {MEMORY_BUDGET_MB:,.0f} MB",
        ),
        SoakCheck(
            "No memory leak",
            growth is not None and growth < LEAK_MB_PER_DAY,
            _number(growth, "MB per day", 1),
            f"< {LEAK_MB_PER_DAY:g} MB per day after the first hour",
        ),
        SoakCheck(
            "CPU",
            cpu_mean is not None and cpu_mean < CPU_BUDGET_PERCENT,
            _number(cpu_mean, "% on average", 2),
            f"< {CPU_BUDGET_PERCENT:g} % on average",
        ),
        SoakCheck(
            "MT5 calls",
            mt5_max is not None and mt5_max < MT5_BUDGET_MS,
            _number(mt5_max, "ms highest p95"),
            f"p95 < {MT5_BUDGET_MS:,.0f} ms",
        ),
        SoakCheck(
            "Bar processing",
            bars_max is not None and bars_over == 0,
            f"{_number(bars_max, 'ms highest p95')}, {bars_over} over budget",
            "never over its budget (1 s per 10 symbols)",
        ),
        SoakCheck(
            "Stability",
            trouble == 0,
            (
                f"{inputs.critical_logs} CRITICAL lines, {inputs.crash_reports} crash reports, "
                f"{inputs.frozen_workers} frozen workers"
            ),
            "none",
        ),
    )
    notes = (
        f"{inputs.error_logs} ERROR log lines during the run (see the Logs page).",
        f"MT5 connection lost {inputs.disconnects} time(s) (the app reconnects on its own).",
    )
    rows = tuple(_row(name, _series(inputs, name)) for name in SAMPLE_NAMES)
    return SoakReport(inputs.start, inputs.end, inputs.longest_gap, checks, notes, rows)


def _row(name: str, series: Sequence[Sample]) -> tuple[str, ...]:
    title, unit = TABLE_NAMES[name]
    if not series:
        return title, "0", "n/a", "n/a", "n/a"
    values = [sample.value for sample in series]
    digits = 2 if unit == "%" else 0
    return (
        title,
        str(len(values)),
        _number(min(values), unit, digits),
        _number(sum(values) / len(values), unit, digits),
        _number(max(values), unit, digits),
    )


def soak_markdown(report: SoakReport, now: float, version: str = "") -> str:
    by = f" by MT5 Trading Workstation {version}" if version else ""
    lines = [
        "# Soak test report",
        "",
        f"Created {utc_text(now)}{by}.",
        "",
        f"Run: {utc_text(report.start)} to {utc_text(report.end)} ({report.hours:,.1f} hours).",
        "",
        f"**{report.summary}.**",
        "",
        "| Check | Result | Found | Needed |",
        "|---|---|---|---|",
    ]
    for check in report.checks:
        result = "PASS" if check.passed else "FAIL"
        lines.append(f"| {check.name} | {result} | {check.value} | {check.needed} |")
    lines += ["", "## Notes", "", *[f"- {note}" for note in report.notes], ""]
    lines += ["## Samples", "", "| Metric | Samples | Lowest | Average | Highest |"]
    lines.append("|---|---|---|---|---|")
    lines += ["| " + " | ".join(row) + " |" for row in report.rows]
    lines += [
        "",
        "Budgets from spec D4. A run starts at the first sample after a gap of more than "
        f"{MAX_GAP_SECONDS / 60:.0f} minutes (the app was not running).",
        "",
    ]
    return "\n".join(lines)


def report_name(now: float) -> str:
    return "soak-" + datetime.fromtimestamp(now, UTC).strftime("%Y%m%d-%H%M%S") + ".md"
