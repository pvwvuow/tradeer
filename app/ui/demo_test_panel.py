"""Health > Demo test: runs `app.brokers.demo_test` from the window (asked for on 5 October
2026). The test runs in a background thread and hands over each step when it ends; a timer
shows them, so the window never waits for MT5. The report is saved as Markdown in the
reports folder."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.brokers.demo_test import DEMO_MAGIC, DemoReport, Step, save_report
from app.ui.pages import card_frame, styled_label
from app.ui.tables import fill_table, make_table

POLL_MS = 200
ALL_SYMBOLS = "All watched symbols"
COLUMNS = ("Symbol", "Step", "Result", "Time", "Details")
NO_CONTEXT = "The demo test starts with the app's services (MT5 and its gateway)."
INTRO = (
    "Trades like the bot, for real, on your DEMO account: a market buy and sell with SL and "
    "TP, moving the SL, closing part and all of a position, the closed trade in the history, "
    "all four pending order types, a breakout pair where one side fills and the other is "
    "cancelled, an order that expires by itself, a refused order and closing everything like "
    f"the kill switch. Minimum lot, magic number {DEMO_MAGIC} (the bot never touches these "
    "trades), 2 to 4 minutes per symbol. It refuses a real account and closes everything it "
    "opened at the end."
)


class DemoRunner(Protocol):
    def run(
        self,
        symbols: Sequence[str],
        on_step: Callable[[Step], None] | None = None,
    ) -> DemoReport: ...

    def stop(self) -> None: ...


@dataclass
class DemoTestContext:
    create: Callable[[], DemoRunner]  # a new demo test on the app's MT5 gateway
    symbols: Callable[[], Sequence[str]]  # the watchlist
    folder: Path  # where the reports are saved


def step_row(step: Step) -> list[str]:
    result = f"{step.outcome.mark} {step.outcome.label}"
    return [step.symbol or "MT5", step.name, result, f"{step.seconds:.1f} s", step.detail]


def _open_folder(folder: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class DemoTestPanel(QWidget):
    """The Demo test card of the Health page."""

    def __init__(self, context: DemoTestContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DemoTest")
        self.context = context
        self.open_folder: Callable[[Path], None] = _open_folder
        self.runner: DemoRunner | None = None
        self.last_report: DemoReport | None = None
        self.last_path: Path | None = None
        self._lock = threading.Lock()
        self._steps: list[Step] = []
        self._done: tuple[DemoReport | None, Path | None, str] | None = None
        self._stopping = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card, layout = card_frame()
        outer.addWidget(card)
        layout.addWidget(styled_label("Demo test (real orders on the demo account)", "heading"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        row = QHBoxLayout()
        self.symbol_box = QComboBox()
        self.symbol_box.setObjectName("DemoTestSymbol")
        self.symbol_box.setAccessibleName("Symbol for the demo test")
        row.addWidget(self.symbol_box)
        self.run_button = QPushButton("Run demo test")
        self.run_button.setObjectName("DemoTestRun")
        self.run_button.setProperty("variant", "primary")
        self.run_button.clicked.connect(self.start)
        row.addWidget(self.run_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DemoTestStop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        row.addWidget(self.stop_button)
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setObjectName("DemoTestFolder")
        self.folder_button.setEnabled(False)
        self.folder_button.clicked.connect(self.open_report_folder)
        row.addWidget(self.folder_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("DemoTestStatus")
        layout.addWidget(self.status)
        self.table = make_table(COLUMNS)
        self.table.setObjectName("DemoTestSteps")
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        self.refresh_symbols()
        if context is None:
            self.run_button.setEnabled(False)
            self.symbol_box.setEnabled(False)
            self.status.setText(NO_CONTEXT)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def refresh_symbols(self) -> None:
        """The watchlist and "All watched symbols", keeping the current choice."""
        names = list(self.context.symbols()) if self.context is not None else []
        current = self.symbol_box.currentText()
        self.symbol_box.clear()
        if names:
            self.symbol_box.addItems([*names, ALL_SYMBOLS])
        index = self.symbol_box.findText(current)
        if index >= 0:
            self.symbol_box.setCurrentIndex(index)

    def chosen(self) -> list[str]:
        if self.context is None:
            return []
        choice = self.symbol_box.currentText()
        if choice == ALL_SYMBOLS:
            return list(self.context.symbols())
        return [choice] if choice else []

    def start(self) -> bool:
        """Start a run in the background; `poll` shows its steps and the result."""
        context = self.context
        if context is None or self.running:
            return False
        symbols = self.chosen()
        if not symbols:
            self.status.setText("Add a symbol to the watchlist first.")
            return False
        runner = context.create()
        folder = context.folder
        self.runner = runner
        self._stopping = False
        with self._lock:
            self._steps = []
            self._done = None
        fill_table(self.table, [])
        self.run_button.setEnabled(False)
        self.symbol_box.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText(f"Running on {', '.join(symbols)}: real orders on the demo account.")

        def collect(step: Step) -> None:
            with self._lock:
                self._steps.append(step)

        def work() -> None:
            report: DemoReport | None = None
            path: Path | None = None
            error = ""
            try:
                report = runner.run(symbols, collect)
                path = save_report(report, folder)
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
            with self._lock:
                self._done = (report, path, error)

        threading.Thread(target=work, name="demo-test", daemon=True).start()
        self._timer.start()
        return True

    def stop(self) -> None:
        if self.runner is None or not self.running:
            return
        self.runner.stop()
        self._stopping = True
        self.stop_button.setEnabled(False)
        self.status.setText("Stopping after this step, then closing what the test opened.")

    def poll(self) -> None:
        with self._lock:
            steps = list(self._steps)
            done = self._done
        fill_table(self.table, [step_row(step) for step in steps])
        if done is None:
            if steps and not self._stopping:
                self.status.setText(f"Running: {len(steps)} steps done, the last: {steps[-1].name}")
            return
        self._timer.stop()
        self.runner = None
        self.run_button.setEnabled(True)
        self.symbol_box.setEnabled(True)
        self.stop_button.setEnabled(False)
        report, path, error = done
        if report is None:
            self.status.setText(f"The demo test could not run: {error}")
            return
        self.last_report = report
        lines = [report.summary()]
        if path is not None:
            self.last_path = path
            self.folder_button.setEnabled(True)
            lines.append(f"Report saved: {path}")
        elif error:
            lines.append(f"The report could not be saved: {error}")
        self.status.setText("\n".join(lines))

    def open_report_folder(self) -> None:
        if self.last_path is not None:
            self.open_folder(self.last_path.parent)
