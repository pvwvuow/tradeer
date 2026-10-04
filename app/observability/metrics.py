"""Performance metrics (spec E3, D4): CPU, memory, latencies and queues against budgets.

The probes collect raw samples while the app runs: `LatencyWindow` keeps the newest MT5 call
times and closed-bar processing times (thread-safe, bounded). Every minute the performance
monitor turns them, with this process's CPU and memory, into a `PerfInputs` snapshot, and
`evaluate_metrics` compares each value with its budget from spec D4:

- idle CPU under 3 % of the PC (the average of the last five minutes),
- memory (the working set) under 500 MB,
- the closed bars of 10 symbols processed in under 1 s,
- a cold start (launch to main window) under 5 s.

D4 has no budget for MT5 calls; a p95 over 1 s (the "slow request" line of the MT5 log) or
more than 20 requests waiting for the gateway is a warning, like over 1,000 rows waiting to
sync. Over a budget is a WARNING, never CRITICAL: performance alone never stops trading.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.observability.health import HealthStatus

MB = 1024.0**2
MINUTE = 60.0
SNAPSHOT_SECONDS = 15 * MINUTE
CPU_BUDGET_PERCENT = 3.0
MEMORY_BUDGET_MB = 500.0
BAR_BUDGET_MS = 1000.0
BAR_BUDGET_SYMBOLS = 10
STARTUP_BUDGET_SECONDS = 5.0
MT5_BUDGET_MS = 1000.0
MT5_QUEUE_BUDGET = 20
SYNC_QUEUE_BUDGET = 1_000
WINDOW_SIZE = 500


def percentile(values: Sequence[float], share: float) -> float | None:
    """The `share` (0 to 1) percentile with linear interpolation; None without values."""
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    position = min(max(share, 0.0), 1.0) * (len(clean) - 1)
    low = math.floor(position)
    high = min(low + 1, len(clean) - 1)
    return clean[low] + (clean[high] - clean[low]) * (position - low)


class LatencyWindow:
    """The newest `size` samples: milliseconds and how many items each one covered."""

    def __init__(self, size: int = WINDOW_SIZE) -> None:
        self._lock = threading.Lock()
        self._samples: deque[tuple[float, int]] = deque(maxlen=size)

    def add(self, milliseconds: float, items: int = 1) -> None:
        if not math.isfinite(milliseconds) or milliseconds < 0:
            return
        with self._lock:
            self._samples.append((float(milliseconds), max(int(items), 1)))

    def samples(self) -> list[tuple[float, int]]:
        with self._lock:
            return list(self._samples)

    def values(self) -> list[float]:
        return [milliseconds for milliseconds, _items in self.samples()]


def _window() -> LatencyWindow:
    return LatencyWindow()


@dataclass
class PerfProbes:
    """What the running parts report into (one per app run)."""

    mt5: LatencyWindow = field(default_factory=_window)
    bars: LatencyWindow = field(default_factory=_window)
    startup_seconds: float | None = None

    def mt5_call(self, milliseconds: float) -> None:
        self.mt5.add(milliseconds)

    def bar_cycle(self, milliseconds: float, symbols: int) -> None:
        self.bars.add(milliseconds, symbols)


@dataclass(frozen=True)
class PerfInputs:
    """One snapshot of the raw numbers (times in UTC seconds, latencies in ms)."""

    now: float
    cpu_percent: float | None = None
    memory_bytes: float | None = None
    mt5_ms: Sequence[float] = ()
    bar_cycles: Sequence[tuple[float, int]] = ()  # (milliseconds, symbols processed)
    mt5_queue: int | None = None
    sync_queue: int | None = None
    startup_seconds: float | None = None


@dataclass(frozen=True)
class Metric:
    name: str
    title: str
    value: float | None
    unit: str
    budget: str
    status: HealthStatus
    text: str = ""

    @property
    def over(self) -> bool:
        return self.status is HealthStatus.WARNING

    def value_text(self) -> str:
        if self.value is None or not math.isfinite(self.value):
            return "n/a"
        number = f"{self.value:,.0f}" if abs(self.value) >= 100 else f"{self.value:,.1f}"
        return f"{number} {self.unit}".strip()


def _status(value: float | None, limit: float) -> HealthStatus:
    if value is None or not math.isfinite(value):
        return HealthStatus.UNKNOWN
    return HealthStatus.WARNING if value > limit else HealthStatus.OK


def cpu_metric(inputs: PerfInputs) -> Metric:
    value = inputs.cpu_percent
    status = _status(value, CPU_BUDGET_PERCENT)
    text = "This app's share of the whole PC (like Task Manager), last 5 minutes."
    if value is None:
        text = "Measured from the second minute on."
    elif status is HealthStatus.WARNING:
        text += " A backtest or a model training uses more; an idle app should not."
    budget = f"< {CPU_BUDGET_PERCENT:g} % when idle"
    return Metric("cpu_percent", "CPU", value, "%", budget, status, text)


def memory_metric(inputs: PerfInputs) -> Metric:
    found = inputs.memory_bytes
    value = found / MB if found is not None else None
    status = _status(value, MEMORY_BUDGET_MB)
    text = "This app's working set (the RAM it uses)."
    if value is None:
        text = "Could not be read on this system."
    elif status is HealthStatus.WARNING:
        text += " If it keeps growing, restart the app and send a debug bundle."
    budget = f"< {MEMORY_BUDGET_MB:,.0f} MB"
    return Metric("memory_mb", "Memory", value, "MB", budget, status, text)


def _bar_budget(symbols: int) -> float:
    return BAR_BUDGET_MS * max(1.0, symbols / BAR_BUDGET_SYMBOLS)


def bar_metrics(inputs: PerfInputs) -> list[Metric]:
    """The closed-bar cycle: a cycle of more than 10 symbols gets a bigger budget."""
    cycles = [(ms, symbols) for ms, symbols in inputs.bar_cycles if math.isfinite(ms)]
    budget = f"< {BAR_BUDGET_MS / 1000:g} s per {BAR_BUDGET_SYMBOLS} symbols"
    times = [ms for ms, _symbols in cycles]
    ratios = [ms / _bar_budget(symbols) for ms, symbols in cycles]
    text = "No closed bar processed yet."
    if cycles:
        most = max(symbols for _ms, symbols in cycles)
        text = f"{len(cycles)} recent cycles, up to {most} symbol(s) each."
    found: list[Metric] = []
    for share, name, title in (
        (0.5, "bar_ms_p50", "Closed-bar processing p50"),
        (0.95, "bar_ms_p95", "Closed-bar processing p95"),
    ):
        status = _status(percentile(ratios, share), 1.0)
        found.append(Metric(name, title, percentile(times, share), "ms", budget, status, text))
    return found


def mt5_metrics(inputs: PerfInputs) -> list[Metric]:
    values = [value for value in inputs.mt5_ms if math.isfinite(value)]
    budget = f"< {MT5_BUDGET_MS:,.0f} ms"
    text = f"{len(values)} recent MT5 calls. History downloads for a backtest take longer."
    if not values:
        text = "No MT5 call yet."
    found: list[Metric] = []
    for share, name, title in (
        (0.5, "mt5_ms_p50", "MT5 call p50"),
        (0.95, "mt5_ms_p95", "MT5 call p95"),
    ):
        value = percentile(values, share)
        found.append(Metric(name, title, value, "ms", budget, _status(value, MT5_BUDGET_MS), text))
    return found


def queue_metrics(inputs: PerfInputs) -> list[Metric]:
    mt5 = float(inputs.mt5_queue) if inputs.mt5_queue is not None else None
    sync = float(inputs.sync_queue) if inputs.sync_queue is not None else None
    return [
        Metric(
            "mt5_queue",
            "MT5 requests waiting",
            mt5,
            "requests",
            f"<= {MT5_QUEUE_BUDGET}",
            _status(mt5, MT5_QUEUE_BUDGET),
            "Requests queued for the MT5 gateway thread.",
        ),
        Metric(
            "sync_queue",
            "Rows waiting to sync",
            sync,
            "rows",
            f"<= {SYNC_QUEUE_BUDGET:,}",
            _status(sync, SYNC_QUEUE_BUDGET),
            "Rows on this PC not yet uploaded to Supabase.",
        ),
    ]


def startup_metric(inputs: PerfInputs) -> Metric:
    value = inputs.startup_seconds
    text = "From the launch to the main window." if value is not None else "Not measured."
    return Metric(
        "startup_seconds",
        "Start-up",
        value,
        "s",
        f"< {STARTUP_BUDGET_SECONDS:g} s",
        _status(value, STARTUP_BUDGET_SECONDS),
        text,
    )


def evaluate_metrics(inputs: PerfInputs) -> list[Metric]:
    """Every metric, in the order the Health page shows them."""
    return [
        cpu_metric(inputs),
        memory_metric(inputs),
        *bar_metrics(inputs),
        *mt5_metrics(inputs),
        *queue_metrics(inputs),
        startup_metric(inputs),
    ]


def perf_summary(metrics: Sequence[Metric]) -> str:
    measured = [metric for metric in metrics if metric.status is not HealthStatus.UNKNOWN]
    if not measured:
        return "Performance: not measured yet"
    over = [metric for metric in metrics if metric.over]
    if not over:
        return "Performance: within the budgets"
    names = ", ".join(metric.title for metric in over[:3])
    more = f" and {len(over) - 3} more" if len(over) > 3 else ""
    return f"Performance: {len(over)} over budget: {names}{more}"


class PerfRecorder:
    """What to save: changed metrics at once, all measured ones every `snapshot_seconds`."""

    def __init__(self, snapshot_seconds: float = SNAPSHOT_SECONDS) -> None:
        self.snapshot_seconds = snapshot_seconds
        self._last: dict[str, HealthStatus] = {}
        self._snapshot_at = -math.inf

    def select(self, metrics: Sequence[Metric], now: float) -> list[Metric]:
        measured = [metric for metric in metrics if metric.value is not None]
        if now - self._snapshot_at >= self.snapshot_seconds:
            chosen = measured
            self._snapshot_at = now
        else:
            chosen = [item for item in measured if self._last.get(item.name) != item.status]
        for metric in metrics:
            self._last[metric.name] = metric.status
        return chosen

    def changes(
        self,
        before: Sequence[Metric],
        after: Sequence[Metric],
    ) -> list[tuple[Metric | None, Metric]]:
        """(old, new) for every metric whose status changed, for the log."""
        old = {metric.name: metric for metric in before}
        return [
            (old.get(metric.name), metric)
            for metric in after
            if old.get(metric.name) is None or old[metric.name].status is not metric.status
        ]
