"""The Health page (spec F3 page 13, E3): health checks, background workers, recent issues.

It only reads: the monitor's last snapshot, the watchdog's worker list and the saved
problems. "Check now" asks the monitor's own thread to run (no MT5 call on the UI thread).
Performance metrics and the debug bundle come in the next steps of Phase 14.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.engine.health_monitor import HealthMonitor
from app.observability.health import LABELS, HealthCheck, HealthStatus
from app.observability.watchdog import WorkerStatus
from app.storage.health_store import SavedCheck
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.style import chip, set_chip
from app.ui.tables import fill_table, make_table

REFRESH_MS = 2000
NO_CONTEXT = "Health checks start with the app's services (MT5, storage, market analysis)."
TONES: dict[HealthStatus, str] = {
    HealthStatus.OK: "profit",
    HealthStatus.WARNING: "warning",
    HealthStatus.CRITICAL: "loss",
    HealthStatus.UNKNOWN: "neutral",
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


def _clock(seconds: float) -> str:
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def check_rows(checks: Sequence[HealthCheck]) -> list[list[str]]:
    rows: list[list[str]] = []
    for check in checks:
        details = f"{check.text} {check.fix}".strip() if check.problem else check.text
        rows.append([check.title, LABELS[check.status], check.value_text(), details])
    return rows


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


class HealthPage(QWidget):
    def __init__(self, context: HealthContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_health")
        self.context = context
        self.now: Callable[[], float] = time.time
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
        layout.addWidget(self._build_workers())
        layout.addWidget(self._build_history())
        layout.addStretch(1)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        if context is None:
            self.check_button.setEnabled(False)
            self.summary.setText(NO_CONTEXT)
        else:
            self._timer.start()
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

    def check_now(self) -> None:
        if self.context is None:
            return
        self.context.monitor.check_now()
        self.summary.setText("Checking...")

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
        fill_table(self.workers, worker_rows(context.workers()))
        fill_table(self.history, history_rows(context.history(50)))
