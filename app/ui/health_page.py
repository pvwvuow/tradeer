"""The Health page (spec F3 page 13, E3): checks, performance, workers, the debug bundle,
the soak test report and the demo test (real orders on the demo account, 0.23.0).

It only reads: the monitors' last snapshots, the watchdog's worker list and the saved
problems. "Check now" asks the monitor's own thread to run (no MT5 call on the UI thread).
"Create debug bundle" and "Create soak report" run in short-lived background threads so a
big log folder or database never freezes the window.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.engine.health_monitor import HealthMonitor
from app.engine.perf_monitor import PerfMonitor
from app.observability.debug_bundle import BundleResult
from app.observability.health import LABELS, HealthCheck, HealthStatus
from app.observability.metrics import Metric
from app.observability.soak import SoakResult
from app.observability.watchdog import WorkerStatus
from app.storage.health_store import SavedCheck
from app.ui.demo_test_panel import DemoTestContext, DemoTestPanel
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.style import chip, set_chip
from app.ui.tables import fill_table, make_table

REFRESH_MS = 2000
BUNDLE_POLL_MS = 200
NO_CONTEXT = "Health checks start with the app's services (MT5, storage, market analysis)."
BUNDLE_TEXT = (
    "A zip of the recent logs, crash reports, masked settings, health, performance, versions "
    "and the last decision traces, with README_DEBUG.md and a prompt for an AI. Attach it to "
    "a bug report."
)
SOAK_TEXT = (
    "Leave the app running on a demo account for 24 hours on market days, then create the "
    "report: it checks the run against the budgets (memory, leaks, CPU, MT5 calls, bar "
    "processing, crashes) and saves it as Markdown in the reports folder."
)
TONES: dict[HealthStatus, str] = {
    HealthStatus.OK: "profit",
    HealthStatus.WARNING: "warning",
    HealthStatus.CRITICAL: "loss",
    HealthStatus.UNKNOWN: "neutral",
}
PERF_LABELS: dict[HealthStatus, str] = {
    HealthStatus.OK: "Within budget",
    HealthStatus.WARNING: "Over budget",
    HealthStatus.CRITICAL: "Over budget",
    HealthStatus.UNKNOWN: "n/a",
}


def _no_workers() -> Sequence[WorkerStatus]:
    return ()


def _no_history(limit: int) -> Sequence[SavedCheck]:
    return ()


@dataclass
class HealthContext:
    monitor: HealthMonitor
    workers: Callable[[], Sequence[WorkerStatus]] = field(default=_no_workers)
    history: Callable[[int], Sequence[SavedCheck]] = field(default=_no_history)
    perf: PerfMonitor | None = None
    bundle: Callable[[], BundleResult] | None = None
    soak: Callable[[], SoakResult] | None = None
    demo_test: DemoTestContext | None = None


def _clock(seconds: float) -> str:
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _open_folder(folder: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def check_rows(checks: Sequence[HealthCheck]) -> list[list[str]]:
    rows: list[list[str]] = []
    for check in checks:
        details = f"{check.text} {check.fix}".strip() if check.problem else check.text
        rows.append([check.title, LABELS[check.status], check.value_text(), details])
    return rows


def metric_rows(metrics: Sequence[Metric]) -> list[list[str]]:
    return [
        [metric.title, PERF_LABELS[metric.status], metric.value_text(), metric.budget, metric.text]
        for metric in metrics
    ]


def worker_rows(workers: Sequence[WorkerStatus]) -> list[list[str]]:
    rows: list[list[str]] = []
    for worker in sorted(workers, key=lambda item: item.name):
        state = "Not responding" if worker.frozen else "Running"
        rows.append(
            [
                worker.name,
                state,
                f"{worker.silent_seconds:,.0f} s ago",
                f"{worker.timeout_seconds:,.0f} s",
                str(worker.restarts),
            ],
        )
    return rows


def history_rows(saved: Sequence[SavedCheck]) -> list[list[str]]:
    return [[_clock(item.time), item.name, item.status, item.text] for item in saved]


def _failed(result: SoakResult) -> list[str]:
    """The checks that did not pass, one line each."""
    return [
        f"{check.name}: {check.value} (needs {check.needed})"
        for check in result.report.checks
        if not check.passed
    ]


class HealthPage(QWidget):
    def __init__(self, context: HealthContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_health")
        self.context = context
        self.now: Callable[[], float] = time.time
        self.open_folder: Callable[[Path], None] = _open_folder
        self.last_bundle: Path | None = None
        self._bundle_done: tuple[BundleResult | None, str] | None = None
        self.last_soak: Path | None = None
        self._soak_done: tuple[SoakResult | None, str] | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll_area)
        body = QWidget()
        scroll_area.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(16)
        layout.addWidget(styled_label("Health", "title"))
        layout.addWidget(self._build_checks())
        self.demo_panel = DemoTestPanel(context.demo_test if context is not None else None)
        layout.addWidget(self.demo_panel)
        layout.addWidget(self._build_perf())
        layout.addWidget(self._build_workers())
        layout.addWidget(self._build_history())
        layout.addWidget(self._build_bundle())
        layout.addWidget(self._build_soak())
        layout.addStretch(1)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._bundle_timer = QTimer(self)
        self._bundle_timer.setInterval(BUNDLE_POLL_MS)
        self._bundle_timer.timeout.connect(self.poll_bundle)
        self._soak_timer = QTimer(self)
        self._soak_timer.setInterval(BUNDLE_POLL_MS)
        self._soak_timer.timeout.connect(self.poll_soak)
        if context is None:
            self.check_button.setEnabled(False)
            self.summary.setText(NO_CONTEXT)
        else:
            self._timer.start()
        if context is None or context.bundle is None:
            self.bundle_button.setEnabled(False)
        if context is None or context.soak is None:
            self.soak_button.setEnabled(False)
        self.refresh()

    def _build_checks(self) -> QWidget:
        card, layout = card_frame()
        row = QHBoxLayout()
        row.addWidget(styled_label("Checks (every minute)", "heading"))
        self.status_chip = chip("Not checked", "neutral")
        self.status_chip.setObjectName("HealthChip")
        row.addWidget(self.status_chip)
        row.addStretch(1)
        self.check_button = QPushButton("Check now")
        self.check_button.setObjectName("HealthCheckNow")
        self.check_button.setProperty("variant", "primary")
        self.check_button.clicked.connect(self.check_now)
        row.addWidget(self.check_button)
        layout.addLayout(row)
        self.summary = styled_label("", "muted", wrap=True)
        self.summary.setObjectName("HealthSummary")
        layout.addWidget(self.summary)
        self.checks = make_table(("Check", "Status", "Value", "Details"))
        self.checks.setObjectName("HealthChecks")
        self.checks.setMinimumHeight(360)
        layout.addWidget(self.checks)
        return card

    def _build_perf(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Performance", "heading"))
        self.perf_summary = styled_label("", "muted", wrap=True)
        self.perf_summary.setObjectName("HealthPerfSummary")
        layout.addWidget(self.perf_summary)
        self.metrics = make_table(("Metric", "Status", "Value", "Budget", "Details"))
        self.metrics.setObjectName("HealthMetrics")
        self.metrics.setMinimumHeight(300)
        layout.addWidget(self.metrics)
        return card

    def _build_workers(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Background workers", "heading"))
        layout.addWidget(
            styled_label(
                "Each worker sends a heartbeat; the watchdog restarts one that stops answering.",
                "muted",
                wrap=True,
            ),
        )
        self.workers = make_table(("Worker", "State", "Last heartbeat", "Timeout", "Restarts"))
        self.workers.setObjectName("HealthWorkers")
        self.workers.setMinimumHeight(180)
        layout.addWidget(self.workers)
        return card

    def _build_history(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Recent issues", "heading"))
        self.history = make_table(("Time", "Check", "Status", "Details"))
        self.history.setObjectName("HealthHistory")
        self.history.setMinimumHeight(180)
        layout.addWidget(self.history)
        return card

    def _build_bundle(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Debug bundle", "heading"))
        layout.addWidget(styled_label(BUNDLE_TEXT, "muted", wrap=True))
        row = QHBoxLayout()
        self.bundle_button = QPushButton("Create debug bundle")
        self.bundle_button.setObjectName("HealthBundle")
        self.bundle_button.clicked.connect(self.create_bundle)
        row.addWidget(self.bundle_button)
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setObjectName("HealthBundleFolder")
        self.folder_button.setEnabled(False)
        self.folder_button.clicked.connect(self.open_bundle_folder)
        row.addWidget(self.folder_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.bundle_status = styled_label("", "muted", wrap=True)
        self.bundle_status.setObjectName("HealthBundleStatus")
        layout.addWidget(self.bundle_status)
        return card

    def _build_soak(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Soak test (24 hours)", "heading"))
        layout.addWidget(styled_label(SOAK_TEXT, "muted", wrap=True))
        row = QHBoxLayout()
        self.soak_button = QPushButton("Create soak report")
        self.soak_button.setObjectName("HealthSoak")
        self.soak_button.clicked.connect(self.create_soak)
        row.addWidget(self.soak_button)
        self.soak_folder_button = QPushButton("Open folder")
        self.soak_folder_button.setObjectName("HealthSoakFolder")
        self.soak_folder_button.setEnabled(False)
        self.soak_folder_button.clicked.connect(self.open_soak_folder)
        row.addWidget(self.soak_folder_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.soak_status = styled_label("", "muted", wrap=True)
        self.soak_status.setObjectName("HealthSoakStatus")
        layout.addWidget(self.soak_status)
        return card

    def check_now(self) -> None:
        if self.context is None:
            return
        self.context.monitor.check_now()
        self.summary.setText("Checking...")

    def create_bundle(self) -> bool:
        """Start writing the zip in the background; `poll_bundle` shows the result."""
        context = self.context
        if context is None or context.bundle is None or self._bundle_timer.isActive():
            return False
        build = context.bundle
        self._bundle_done = None
        self.bundle_button.setEnabled(False)
        self.bundle_status.setText("Creating the debug bundle...")

        def work() -> None:
            try:
                self._bundle_done = (build(), "")
            except Exception as error:
                self._bundle_done = (None, f"{type(error).__name__}: {error}")

        threading.Thread(target=work, name="debug-bundle", daemon=True).start()
        self._bundle_timer.start()
        return True

    def poll_bundle(self) -> None:
        done = self._bundle_done
        if done is None:
            return
        self._bundle_timer.stop()
        self._bundle_done = None
        self.bundle_button.setEnabled(True)
        result, error = done
        if result is None:
            self.bundle_status.setText(f"The debug bundle could not be created: {error}")
            return
        self.last_bundle = result.path
        self.folder_button.setEnabled(True)
        self.bundle_status.setText(result.text)

    def open_bundle_folder(self) -> None:
        if self.last_bundle is not None:
            self.open_folder(self.last_bundle.parent)

    def create_soak(self) -> bool:
        """Start the soak report in the background; `poll_soak` shows the result."""
        context = self.context
        if context is None or context.soak is None or self._soak_timer.isActive():
            return False
        build = context.soak
        self._soak_done = None
        self.soak_button.setEnabled(False)
        self.soak_status.setText("Reading the saved run...")

        def work() -> None:
            try:
                self._soak_done = (build(), "")
            except Exception as error:
                self._soak_done = (None, f"{type(error).__name__}: {error}")

        threading.Thread(target=work, name="soak-report", daemon=True).start()
        self._soak_timer.start()
        return True

    def poll_soak(self) -> None:
        done = self._soak_done
        if done is None:
            return
        self._soak_timer.stop()
        self._soak_done = None
        self.soak_button.setEnabled(True)
        result, error = done
        if result is None:
            self.soak_status.setText(f"The soak report could not be created: {error}")
            return
        self.last_soak = result.path
        self.soak_folder_button.setEnabled(True)
        lines = [result.text, *_failed(result)]
        self.soak_status.setText("\n".join(lines))

    def open_soak_folder(self) -> None:
        if self.last_soak is not None:
            self.open_folder(self.last_soak.parent)

    def refresh(self) -> None:
        context = self.context
        if context is None:
            return
        snapshot = context.monitor.snapshot
        fill_table(self.checks, check_rows(snapshot.checks))
        status = snapshot.status
        label = "Not checked" if not snapshot.checks else LABELS[status]
        set_chip(self.status_chip, label, TONES[status] if snapshot.checks else "neutral")
        if snapshot.checks:
            age = max(0.0, self.now() - snapshot.at)
            self.summary.setText(f"{snapshot.text}. Last check {age:,.0f} s ago.")
        else:
            self.summary.setText("The first check runs about 30 seconds after the start.")
        self._refresh_perf(context)
        fill_table(self.workers, worker_rows(context.workers()))
        fill_table(self.history, history_rows(context.history(50)))

    def _refresh_perf(self, context: HealthContext) -> None:
        if context.perf is None:
            self.perf_summary.setText("Performance metrics start with the app's services.")
            return
        found = context.perf.snapshot
        fill_table(self.metrics, metric_rows(found.metrics))
        if not found.metrics:
            self.perf_summary.setText("Measured with every health check.")
            return
        self.perf_summary.setText(
            f"{found.text}. Budgets from the spec (D4); over a budget is a warning in the log.",
        )
