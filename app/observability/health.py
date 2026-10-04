"""Health checks (spec E3, Phase 14): plain rules over a snapshot of the running app.

Every minute the health monitor collects a `HealthInputs` snapshot (MT5 connection, the
terminal's Algo Trading switch and ping, the newest quotes, the broker clock, the cloud
sync, the free disk space, the log folder size and the watchdog's workers) and `evaluate`
turns it into one `HealthCheck` per rule. Nothing here reads MT5, files or the network, so
every rule is tested with plain values.

`HealthRecorder` decides what is saved to the `health_checks` table: a check whenever its
status changes, and all checks every 15 minutes. A row per check per minute would upload
14,000 rows a day for no extra information.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.observability.watchdog import WorkerStatus

MINUTE = 60.0
SNAPSHOT_SECONDS = 15 * MINUTE
GIB = 1024.0**3
QUOTE_WARNING_SECONDS = 5 * MINUTE
QUOTE_CRITICAL_SECONDS = 15 * MINUTE
PING_WARNING_MS = 250.0
PING_CRITICAL_MS = 1000.0
CLOCK_WARNING_SECONDS = 30.0
CLOCK_CRITICAL_SECONDS = 120.0
QUEUE_WARNING = 1_000
QUEUE_CRITICAL = 10_000
DISK_WARNING_BYTES = 5 * GIB
DISK_CRITICAL_BYTES = 1 * GIB
LOG_WARNING_BYTES = 1 * GIB
DAY = 86_400.0


class HealthStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"  # not measurable right now (not connected, sync off, no prices)


RANK: dict[HealthStatus, int] = {
    HealthStatus.UNKNOWN: 0,
    HealthStatus.OK: 1,
    HealthStatus.WARNING: 2,
    HealthStatus.CRITICAL: 3,
}
LABELS: dict[HealthStatus, str] = {
    HealthStatus.OK: "OK",
    HealthStatus.WARNING: "Warning",
    HealthStatus.CRITICAL: "Problem",
    HealthStatus.UNKNOWN: "n/a",
}


@dataclass(frozen=True)
class HealthCheck:
    name: str
    title: str
    status: HealthStatus
    text: str
    value: float | None = None
    unit: str = ""
    fix: str = ""

    @property
    def problem(self) -> bool:
        return self.status in (HealthStatus.WARNING, HealthStatus.CRITICAL)

    def value_text(self) -> str:
        if self.value is None or not math.isfinite(self.value):
            return ""
        whole = abs(self.value) >= 10 or self.value == int(self.value)
        number = f"{self.value:,.0f}" if whole else f"{self.value:,.1f}"
        return f"{number} {self.unit}".strip()


@dataclass(frozen=True)
class HealthInputs:
    """One snapshot of everything the rules look at (all times UTC seconds)."""

    now: float
    mt5_state: str = "disconnected"  # ConnectionState value
    algo_trading: bool | None = None  # the terminal's Algo Trading button; None = unknown
    trade_api_disabled: bool = False
    ping_ms: float | None = None
    quote_times: Sequence[float] = ()  # the newest tick of each symbol, as UTC
    market_closed: bool = False
    clock_measured: bool = False
    clock_text: str = ""
    clock_changes: Sequence[float] = ()  # UTC times the broker offset jumped
    sync_state: str = "disabled"  # SyncState value
    sync_message: str = ""
    pending: int = 0
    failed: int = 0
    disk_free_bytes: float | None = None
    log_bytes: float | None = None
    workers: Sequence[WorkerStatus] = field(default_factory=tuple)


def worst(statuses: Iterable[HealthStatus]) -> HealthStatus:
    found = HealthStatus.UNKNOWN
    for status in statuses:
        if RANK[status] > RANK[found]:
            found = status
    return found


def overall(checks: Sequence[HealthCheck]) -> HealthStatus:
    return worst(check.status for check in checks)


def summary(checks: Sequence[HealthCheck]) -> str:
    """One plain line for the page header and the Dashboard."""
    problems = [check for check in checks if check.problem]
    if not checks:
        return "Health: not checked yet"
    if not problems:
        return "Health: all checks OK"
    names = ", ".join(check.title for check in problems[:3])
    more = f" and {len(problems) - 3} more" if len(problems) > 3 else ""
    return f"Health: {len(problems)} issue(s): {names}{more}"


def _gib(value: float) -> str:
    return f"{value / GIB:,.1f} GB"


def mt5_check(inputs: HealthInputs) -> HealthCheck:
    state = inputs.mt5_state
    title = "MT5 connected"
    if state == "connected":
        return HealthCheck("mt5_connected", title, HealthStatus.OK, "Connected to the terminal.")
    if state == "reconnecting":
        return HealthCheck(
            "mt5_connected",
            title,
            HealthStatus.CRITICAL,
            "The connection was lost; the app is reconnecting.",
            fix="Check that MetaTrader 5 is running and logged in to the broker.",
        )
    text = {
        "connecting": "Connecting...",
        "failed": "The last connection attempt failed.",
    }.get(state, "Not connected.")
    return HealthCheck(
        "mt5_connected",
        title,
        HealthStatus.WARNING,
        text,
        fix="Settings > Account & connection > Connect.",
    )


def algo_check(inputs: HealthInputs) -> HealthCheck:
    title = "Algo Trading on"
    if inputs.mt5_state != "connected" or inputs.algo_trading is None:
        return HealthCheck("algo_trading", title, HealthStatus.UNKNOWN, "Needs the connection.")
    if inputs.trade_api_disabled:
        return HealthCheck(
            "algo_trading",
            title,
            HealthStatus.WARNING,
            "The terminal blocks trading from Python (API trading is disabled).",
            fix="MT5 > Tools > Options > Expert Advisors: allow algorithmic trading.",
        )
    if not inputs.algo_trading:
        return HealthCheck(
            "algo_trading",
            title,
            HealthStatus.WARNING,
            "The Algo Trading button in MT5 is off: no order can be sent.",
            fix="Press Algo Trading in the MT5 toolbar (it turns green).",
        )
    return HealthCheck("algo_trading", title, HealthStatus.OK, "Orders can be sent.")


def quotes_check(inputs: HealthInputs) -> HealthCheck:
    title = "Quotes fresh"
    times = [value for value in inputs.quote_times if value > 0]
    if inputs.mt5_state != "connected" or not times:
        return HealthCheck("quotes_fresh", title, HealthStatus.UNKNOWN, "No prices yet.")
    age = max(0.0, inputs.now - max(times))
    if inputs.market_closed:
        return HealthCheck(
            "quotes_fresh",
            title,
            HealthStatus.OK,
            "The FX market is closed for the weekend: old prices are expected.",
            age,
            "s",
        )
    if age > QUOTE_CRITICAL_SECONDS:
        status = HealthStatus.CRITICAL
    elif age > QUOTE_WARNING_SECONDS:
        status = HealthStatus.WARNING
    else:
        status = HealthStatus.OK
    text = f"The newest price is {age:,.0f} s old ({len(times)} symbol(s))."
    fix = "" if status is HealthStatus.OK else "Check the terminal's connection to the broker."
    return HealthCheck("quotes_fresh", title, status, text, age, "s", fix)


def offset_check(inputs: HealthInputs) -> HealthCheck:
    title = "Broker offset stable"
    recent = [moment for moment in inputs.clock_changes if moment >= inputs.now - DAY]
    if recent:
        return HealthCheck(
            "broker_offset",
            title,
            HealthStatus.WARNING,
            f"The broker clock jumped {len(recent)} time(s) in the last 24 hours "
            f"({inputs.clock_text}).",
            float(len(recent)),
            "jumps",
            "Daily limits use the broker day: check the times on the Risk page.",
        )
    if not inputs.clock_measured:
        text = inputs.clock_text or "Assumed until the first fresh price."
        return HealthCheck("broker_offset", title, HealthStatus.UNKNOWN, text)
    return HealthCheck("broker_offset", title, HealthStatus.OK, inputs.clock_text or "Stable.")


def clock_check(inputs: HealthInputs) -> HealthCheck:
    """A tick newer than this PC's clock means the PC clock is behind (spec: clock drift)."""
    title = "PC clock"
    times = [value for value in inputs.quote_times if value > 0]
    if inputs.mt5_state != "connected" or not times or not inputs.clock_measured:
        return HealthCheck("clock_drift", title, HealthStatus.UNKNOWN, "Needs fresh prices.")
    lead = max(times) - inputs.now
    if lead > CLOCK_CRITICAL_SECONDS:
        status = HealthStatus.CRITICAL
    elif lead > CLOCK_WARNING_SECONDS:
        status = HealthStatus.WARNING
    else:
        status = HealthStatus.OK
    if status is HealthStatus.OK:
        text = "In step with the broker."
        return HealthCheck("clock_drift", title, status, text, max(lead, 0.0), "s")
    return HealthCheck(
        "clock_drift",
        title,
        status,
        f"This PC's clock is about {lead:,.0f} s behind the broker.",
        lead,
        "s",
        "Windows Settings > Time & language > Date & time > Sync now.",
    )


def ping_check(inputs: HealthInputs) -> HealthCheck:
    title = "Latency to the broker"
    ping = inputs.ping_ms
    if inputs.mt5_state != "connected" or ping is None or ping <= 0:
        return HealthCheck("latency", title, HealthStatus.UNKNOWN, "Needs the connection.")
    if ping > PING_CRITICAL_MS:
        status = HealthStatus.CRITICAL
    elif ping > PING_WARNING_MS:
        status = HealthStatus.WARNING
    else:
        status = HealthStatus.OK
    fix = "" if status is HealthStatus.OK else "A slow or unstable internet connection."
    return HealthCheck("latency", title, status, "The terminal's last ping.", ping, "ms", fix)


def sync_check(inputs: HealthInputs) -> HealthCheck:
    title = "Supabase reachable"
    state = inputs.sync_state
    if state == "disabled":
        return HealthCheck("supabase", title, HealthStatus.UNKNOWN, "Cloud sync is off.")
    if state in ("up_to_date", "syncing"):
        return HealthCheck("supabase", title, HealthStatus.OK, "Uploads work.")
    text = inputs.sync_message or f"Cloud sync: {state}."
    return HealthCheck(
        "supabase",
        title,
        HealthStatus.WARNING,
        text,
        fix="Settings > Data & cloud sync. Nothing is lost: rows wait on this PC.",
    )


def queue_check(inputs: HealthInputs) -> HealthCheck:
    title = "Sync queue"
    pending, failed = inputs.pending, inputs.failed
    if pending > QUEUE_CRITICAL:
        status = HealthStatus.CRITICAL
    elif pending > QUEUE_WARNING or failed:
        status = HealthStatus.WARNING
    else:
        status = HealthStatus.OK
    text = f"{pending:,} row(s) waiting, {failed:,} refused."
    fix = ""
    if failed:
        fix = "Settings > Data & cloud sync > Retry failed (the logs say why)."
    elif status is not HealthStatus.OK:
        fix = "The cloud is not taking rows: check the Supabase check."
    return HealthCheck("sync_queue", title, status, text, float(pending), "rows", fix)


def disk_check(inputs: HealthInputs) -> HealthCheck:
    title = "Disk space"
    free = inputs.disk_free_bytes
    if free is None:
        return HealthCheck("disk_space", title, HealthStatus.UNKNOWN, "Could not be read.")
    if free < DISK_CRITICAL_BYTES:
        status = HealthStatus.CRITICAL
    elif free < DISK_WARNING_BYTES:
        status = HealthStatus.WARNING
    else:
        status = HealthStatus.OK
    fix = "" if status is HealthStatus.OK else "Free some space on the app's drive."
    return HealthCheck(
        "disk_space",
        title,
        status,
        f"{_gib(free)} free on the app's drive.",
        free / GIB,
        "GB",
        fix,
    )


def log_check(inputs: HealthInputs) -> HealthCheck:
    title = "Log size"
    size = inputs.log_bytes
    if size is None:
        return HealthCheck("log_size", title, HealthStatus.UNKNOWN, "Could not be read.")
    status = HealthStatus.WARNING if size > LOG_WARNING_BYTES else HealthStatus.OK
    fix = "" if status is HealthStatus.OK else "Logs > Open log folder; old days can go."
    return HealthCheck(
        "log_size",
        title,
        status,
        f"The log folder uses {_gib(size)}.",
        size / GIB,
        "GB",
        fix,
    )


def workers_check(inputs: HealthInputs) -> HealthCheck:
    title = "Background workers"
    workers = list(inputs.workers)
    if not workers:
        return HealthCheck("workers", title, HealthStatus.UNKNOWN, "No worker registered.")
    frozen = [worker.name for worker in workers if worker.frozen]
    if frozen:
        return HealthCheck(
            "workers",
            title,
            HealthStatus.CRITICAL,
            f"Not responding: {', '.join(frozen)}.",
            float(len(frozen)),
            "frozen",
            "The watchdog restarts what it can; if it stays, restart the app.",
        )
    return HealthCheck(
        "workers",
        title,
        HealthStatus.OK,
        f"{len(workers)} worker(s) send heartbeats.",
        float(len(workers)),
        "workers",
    )


RULES = (
    mt5_check,
    algo_check,
    quotes_check,
    offset_check,
    sync_check,
    queue_check,
    disk_check,
    log_check,
    ping_check,
    clock_check,
    workers_check,
)


def evaluate(inputs: HealthInputs) -> list[HealthCheck]:
    """Every rule, in the order the Health page shows them."""
    return [rule(inputs) for rule in RULES]


class HealthRecorder:
    """What to save: changed checks at once, all of them every `snapshot_seconds`."""

    def __init__(self, snapshot_seconds: float = SNAPSHOT_SECONDS) -> None:
        self.snapshot_seconds = snapshot_seconds
        self._last: dict[str, HealthStatus] = {}
        self._snapshot_at = -math.inf

    def select(self, checks: Sequence[HealthCheck], now: float) -> list[HealthCheck]:
        if now - self._snapshot_at >= self.snapshot_seconds:
            chosen = list(checks)
            self._snapshot_at = now
        else:
            chosen = [check for check in checks if self._last.get(check.name) != check.status]
        for check in checks:
            self._last[check.name] = check.status
        return chosen

    def changes(
        self,
        before: Sequence[HealthCheck],
        after: Sequence[HealthCheck],
    ) -> list[tuple[HealthCheck | None, HealthCheck]]:
        """(old, new) for every check whose status changed, for the log."""
        old = {check.name: check for check in before}
        return [
            (old.get(check.name), check)
            for check in after
            if old.get(check.name) is None or old[check.name].status is not check.status
        ]
