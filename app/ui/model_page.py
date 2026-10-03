"""The Model page (spec C10, F3): train the win-probability model, see its out-of-sample
report next to the baseline, and choose which version the app uses.

Training reads the history from MT5 in a background thread, then trains in a separate
process (`app.ml.job.TrainingJob`), polled by a timer, so the window stays smooth. A model
that does not beat the baseline out of sample is saved but cannot be activated.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QDate, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.backtest.costs import BacktestCosts
from app.backtest.engine import History
from app.backtest.service import strategy_params
from app.calendar.models import CalendarEvent
from app.core.strategy_settings import StrategySettingsSource
from app.ml.job import JobUpdate, TrainingJob, TrainingRequest
from app.ml.labeler import LabelSettings
from app.ml.registry import ModelInfo
from app.ml.service import ModelService
from app.ml.trainer import TrainingOutcome, TrainSettings
from app.risk.settings import RiskConfig, RiskSettingsSource
from app.strategies.registry import STRATEGIES
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label

INTRO = (
    "The model estimates each signal's chance to reach its target before its stop, from "
    "what was known when the signal was made. It is trained on every signal the strategies "
    "made on MT5 history, checked with purged walk-forward tests, calibrated, and compared "
    "with the baseline (the strategy's own win rate). A model that is not better than the "
    "baseline out of sample cannot be used. It is an estimate, never a promise."
)
NOT_CONNECTED = "Connect to MT5 first: training reads the history from MT5."
DAY = 86_400
POLL_MS = 500

Note = Callable[[str], None]
Loader = Callable[[str, float, float, Note], History]


@dataclass
class ModelContext:
    service: ModelService
    load: Loader  # (symbol, start, end, note) -> History; MT5 in the app, synthetic in tests
    strategies: StrategySettingsSource
    risk: RiskSettingsSource
    symbols: Callable[[], Sequence[str]]
    connected: Callable[[], bool]
    events: Callable[[float, float], Sequence[CalendarEvent]] | None = None
    log: Callable[[str, str], None] | None = None
    job_factory: Callable[[], TrainingJob] = TrainingJob


class _Bridge(QObject):
    loaded = Signal(object)
    failed = Signal(str)
    note = Signal(str)


def _table(columns: Sequence[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


def _fill(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))


def _number(value: Any, digits: int = 3) -> str:
    if isinstance(value, int | float):
        return f"{value:.{digits}f}"
    return "n/a"


def _percent(value: Any) -> str:
    return f"{value * 100:.0f}%" if isinstance(value, int | float) else "n/a"


def _day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")


def _to_utc(value: QDate) -> float:
    day = date(value.year(), value.month(), value.day())
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


def score_rows(report: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for key, title in (
        ("model", "Model (calibrated)"),
        ("baseline", "Baseline (win rate)"),
        ("raw", "Model (uncalibrated)"),
    ):
        scores = report.get(key)
        if not isinstance(scores, dict):
            continue
        rows.append(
            [
                title,
                str(scores.get("count", 0)),
                _number(scores.get("auc")),
                _number(scores.get("log_loss"), 4),
                _number(scores.get("brier"), 4),
                _percent(scores.get("win_rate")),
            ],
        )
    return rows


def bucket_rows(report: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for bucket in report.get("buckets", []):
        low, high = bucket.get("low", 0.0), bucket.get("high", 1.0)
        interval = "n/a"
        if bucket.get("win_low") is not None and bucket.get("win_high") is not None:
            interval = f"{_percent(bucket['win_low'])} to {_percent(bucket['win_high'])}"
        rows.append(
            [
                f"{low * 100:.0f}-{high * 100:.0f}%",
                str(bucket.get("count", 0)),
                _percent(bucket.get("win_rate")),
                interval,
                _number(bucket.get("expectancy_r")),
                _number(bucket.get("profit_factor"), 2),
            ],
        )
    return rows


def report_text(report: Mapping[str, Any]) -> str:
    status = report.get("status", "")
    reason = report.get("reason", "")
    samples = report.get("samples", 0)
    wins = report.get("wins", 0)
    period = ""
    if report.get("period_start"):
        start = _day(float(report["period_start"]))
        end = _day(float(report.get("period_end", 0.0)))
        period = f", {start} to {end}"
    head = f"{samples} labelled signals ({wins} won){period}. "
    if status != "trained":
        return head + f"Baseline only: {reason}."
    if report.get("beats_baseline"):
        return head + f"Beats the baseline out of sample: {reason}."
    return head + f"Not better than the baseline out of sample, so it cannot be used: {reason}."


class ModelPage(QWidget):
    def __init__(self, context: ModelContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_model")
        self.context = context
        self.bridge = _Bridge()
        self.bridge.loaded.connect(self._start_job, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        self.bridge.note.connect(self._note, Qt.ConnectionType.QueuedConnection)
        self.job: TrainingJob | None = None
        self._thread: threading.Thread | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll_job)
        self.last_outcome: TrainingOutcome | None = None
        self._shown: list[ModelInfo] = []
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
        layout.addWidget(styled_label("Model", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        layout.addWidget(self._build_form())
        layout.addWidget(self._build_report())
        layout.addWidget(self._build_versions())
        layout.addStretch(1)
        if context is None:
            self.train_button.setEnabled(False)
            self.status.setText("Training needs the MT5 connection and the settings.")
        self.refresh_versions()

    # Form ---------------------------------------------------------------------------------
    def _build_form(self) -> QWidget:
        frame, box = card_frame()
        box.addWidget(styled_label("Train a model", "section"))
        form = QFormLayout()
        symbols = list(self.context.symbols()) if self.context is not None else ["EURUSD"]
        self.symbols = QLineEdit(", ".join(symbols[:3]) or "EURUSD")
        form.addRow("Symbols", self.symbols)
        today = datetime.now(UTC).date()
        first = today - timedelta(days=730)
        self.start = QDateEdit(QDate(first.year, first.month, first.day))
        self.start.setCalendarPopup(True)
        last = today - timedelta(days=1)
        self.end = QDateEdit(QDate(last.year, last.month, last.day))
        self.end.setCalendarPopup(True)
        form.addRow("From (UTC)", self.start)
        form.addRow("To (UTC, included)", self.end)
        row = QHBoxLayout()
        self.strategy_boxes: dict[str, QCheckBox] = {}
        for name in STRATEGIES:
            check = QCheckBox(name.replace("_", " "))
            check.setChecked(True)
            self.strategy_boxes[name] = check
            row.addWidget(check)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Strategies", holder)
        self.min_samples = QSpinBox()
        self.min_samples.setRange(50, 1_000_000)
        self.min_samples.setValue(TrainSettings().min_samples)
        form.addRow("Signals needed", self.min_samples)
        self.folds = QSpinBox()
        self.folds.setRange(2, 20)
        self.folds.setValue(TrainSettings().folds)
        form.addRow("Walk-forward folds", self.folds)
        self.max_bars = QSpinBox()
        self.max_bars.setRange(1, 2000)
        self.max_bars.setValue(LabelSettings().max_bars)
        form.addRow("Timeout (bars)", self.max_bars)
        box.addLayout(form)
        buttons = QHBoxLayout()
        self.train_button = QPushButton("Train model")
        self.train_button.setObjectName("PrimaryButton")
        self.train_button.clicked.connect(self.start_training)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_training)
        buttons.addWidget(self.train_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        box.addWidget(self.progress)
        self.status = styled_label("No training yet.", "muted", wrap=True)
        box.addWidget(self.status)
        return frame

    def build_request(self, histories: Sequence[History]) -> TrainingRequest:
        context = self.context
        names = [name for name, check in self.strategy_boxes.items() if check.isChecked()]
        settings = context.strategies.settings if context is not None else None
        params: dict[str, Mapping[str, Any]] = {
            name: strategy_params(settings, name) if settings is not None else {} for name in names
        }
        start, end = _to_utc(self.start.date()), _to_utc(self.end.date()) + DAY
        events: tuple[CalendarEvent, ...] = ()
        if context is not None and context.events is not None:
            events = tuple(context.events(start - 2 * DAY, end + 7 * DAY))
        risk = context.risk.config if context is not None else RiskConfig()
        return TrainingRequest(
            histories=tuple(histories),
            strategies=params,
            start=start,
            end=end,
            costs=BacktestCosts(),
            label=LabelSettings(max_bars=self.max_bars.value()),
            train=TrainSettings(min_samples=self.min_samples.value(), folds=self.folds.value()),
            risk=risk,
            events=events,
        )

    def chosen_symbols(self) -> list[str]:
        return [part.strip().upper() for part in self.symbols.text().split(",") if part.strip()]

    @property
    def running(self) -> bool:
        loading = self._thread is not None and self._thread.is_alive()
        return loading or (self.job is not None and self.job.running)

    def start_training(self) -> None:
        context = self.context
        if context is None or self.running:
            return
        if not context.connected():
            self.status.setText(NOT_CONNECTED)
            return
        symbols = self.chosen_symbols()
        names = [name for name, check in self.strategy_boxes.items() if check.isChecked()]
        if not symbols or not names:
            self.status.setText("Choose at least one symbol and one strategy.")
            return
        start, end = _to_utc(self.start.date()), _to_utc(self.end.date()) + DAY
        if end - start < 30 * DAY:
            self.status.setText("Choose at least 30 days: a model needs hundreds of signals.")
            return
        self.train_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("Loading the history from MT5...")
        self._thread = threading.Thread(
            target=self._load,
            args=(context, symbols, start, end),
            name="model-history",
            daemon=True,
        )
        self._thread.start()

    def _load(self, context: ModelContext, symbols: list[str], start: float, end: float) -> None:
        try:
            histories = [
                context.load(symbol, start, end, self.bridge.note.emit) for symbol in symbols
            ]
            self.bridge.loaded.emit(histories)
        except Exception as error:
            self.bridge.failed.emit(f"{type(error).__name__}: {error}")

    def _note(self, text: str) -> None:
        self.status.setText(text)

    def _start_job(self, histories: list[History]) -> None:
        context = self.context
        if context is None:
            return
        try:
            request = self.build_request(histories)
            self.job = context.job_factory()
            self.job.start(request)
        except Exception as error:
            self.show_failure(f"{type(error).__name__}: {error}")
            return
        self.status.setText("Training in a separate process...")
        self.timer.start()

    def poll_job(self) -> None:
        job = self.job
        if job is None:
            self.timer.stop()
            return
        for update in job.poll():
            self.apply_update(update)
        if job.finished:
            self.timer.stop()

    def apply_update(self, update: JobUpdate) -> None:
        if update.kind == "progress":
            self.progress.setValue(int(update.share * 100))
            self.status.setText(update.message)
            return
        self.train_button.setEnabled(self.context is not None)
        self.cancel_button.setEnabled(False)
        if update.kind == "error" or update.outcome is None:
            self.show_failure(update.message)
            return
        self.show_outcome(update.outcome)

    def show_outcome(self, outcome: TrainingOutcome) -> None:
        self.last_outcome = outcome
        self.progress.setValue(100)
        report = outcome.report.to_json()
        context = self.context
        saved = ""
        if outcome.model is not None and context is not None:
            info = context.service.save(outcome.model)
            saved = f" Saved as v{info.version}."
        self.show_report(report)
        self.status.setText(outcome.report.verdict() + "." + saved)
        self.refresh_versions()

    def cancel_training(self) -> None:
        if self.job is not None:
            self.job.cancel()
        self.timer.stop()
        self.train_button.setEnabled(self.context is not None)
        self.cancel_button.setEnabled(False)
        self.status.setText("Training cancelled.")

    def show_failure(self, text: str) -> None:
        self.timer.stop()
        self.train_button.setEnabled(self.context is not None)
        self.cancel_button.setEnabled(False)
        self.status.setText(f"Training failed: {text}")
        log = self.context.log if self.context is not None else None
        if log is not None:
            log("ERROR", f"Model training failed: {text}")

    # Report -------------------------------------------------------------------------------
    def _build_report(self) -> QWidget:
        frame, box = card_frame()
        box.addWidget(styled_label("Out-of-sample report", "section"))
        self.verdict = styled_label("No report yet.", "muted", wrap=True)
        box.addWidget(self.verdict)
        self.tabs = QTabWidget()
        self.scores = _table(["Forecast", "Signals", "ROC-AUC", "Log-loss", "Brier", "Won"])
        self.calibration = _table(["Predicted", "Actual", "Signals"])
        self.buckets = _table(
            ["Win chance", "Signals", "Won", "95% range", "Expectancy (R)", "Profit factor"],
        )
        self.importance = _table(["Feature", "Mean |SHAP| (log-odds)"])
        self.folds_table = _table(["Fold", "Train", "Test", "From", "To", "ROC-AUC"])
        self.tabs.addTab(self.scores, "Scores")
        self.tabs.addTab(self.calibration, "Calibration")
        self.tabs.addTab(self.buckets, "Buckets")
        self.tabs.addTab(self.importance, "Importance")
        self.tabs.addTab(self.folds_table, "Folds")
        box.addWidget(self.tabs)
        return frame

    def show_report(self, report: Mapping[str, Any]) -> None:
        self.verdict.setText(report_text(report))
        _fill(self.scores, score_rows(report))
        _fill(
            self.calibration,
            [
                [_percent(p.get("predicted")), _percent(p.get("actual")), str(p.get("count", 0))]
                for p in report.get("calibration_curve", [])
            ],
        )
        _fill(self.buckets, bucket_rows(report))
        _fill(
            self.importance,
            [[str(name), _number(value, 4)] for name, value in report.get("importance", [])[:15]],
        )
        _fill(
            self.folds_table,
            [
                [
                    str(fold.get("index", 0) + 1),
                    str(fold.get("train", 0)),
                    str(fold.get("test", 0)),
                    _day(float(fold.get("test_start", 0.0))),
                    _day(float(fold.get("test_end", 0.0))),
                    _number(fold.get("raw_auc")),
                ]
                for fold in report.get("folds", [])
            ],
        )

    # Versions -----------------------------------------------------------------------------
    def _build_versions(self) -> QWidget:
        frame, box = card_frame()
        box.addWidget(styled_label("Versions", "section"))
        self.active_label = styled_label("Using the baseline.", "muted", wrap=True)
        box.addWidget(self.active_label)
        self.versions = _table(
            ["Version", "Trained", "Strategies", "Symbols", "Period", "Beats baseline", "In use"],
        )
        self.versions.itemSelectionChanged.connect(self._show_selected)
        box.addWidget(self.versions)
        buttons = QHBoxLayout()
        self.activate_button = QPushButton("Use selected model")
        self.activate_button.clicked.connect(self.activate_selected)
        self.rollback_button = QPushButton("Roll back")
        self.rollback_button.clicked.connect(self.rollback)
        self.baseline_button = QPushButton("Use the baseline")
        self.baseline_button.clicked.connect(self.use_baseline)
        for button in (self.activate_button, self.rollback_button, self.baseline_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        self.version_status = styled_label("", "muted", wrap=True)
        box.addWidget(self.version_status)
        box.addWidget(styled_label("Drift", "section"))
        self.drift_label = styled_label("No active model.", "muted", wrap=True)
        box.addWidget(self.drift_label)
        return frame

    def _infos(self) -> list[ModelInfo]:
        if self.context is None:
            return []
        try:
            return self.context.service.models()
        except Exception:
            return []

    def refresh_versions(self) -> None:
        infos = self._infos()
        self._shown = infos
        _fill(
            self.versions,
            [
                [
                    f"v{info.version}",
                    _day(info.created_at) if info.created_at else "",
                    ", ".join(info.strategies),
                    ", ".join(info.symbols),
                    info.period,
                    "yes" if info.beats_baseline else "no",
                    "yes" if info.active else "",
                ]
                for info in infos
            ],
        )
        active = next((info for info in infos if info.active), None)
        enabled = self.context is not None
        for button in (self.activate_button, self.rollback_button, self.baseline_button):
            button.setEnabled(enabled)
        if active is None:
            self.active_label.setText("Using the baseline: the strategy's own win rate.")
            self.drift_label.setText("No active model.")
            return
        self.active_label.setText(f"Using model {active.title}.")
        self.refresh_drift()

    def refresh_drift(self) -> None:
        context = self.context
        if context is None:
            return
        try:
            report = context.service.drift()
        except Exception as error:
            self.drift_label.setText(f"Drift could not be checked: {error}")
            return
        if report is None:
            self.drift_label.setText("No active model.")
            return
        self.drift_label.setText("\n".join(report.lines()))

    def selected(self) -> ModelInfo | None:
        rows = {index.row() for index in self.versions.selectedIndexes()}
        infos = self._shown
        if len(rows) != 1:
            return None
        row = rows.pop()
        return infos[row] if row < len(infos) else None

    def _show_selected(self) -> None:
        info = self.selected()
        if info is None:
            return
        self.show_report(info.report)
        block = info.activation_block()
        text = f"v{info.version}: " + (f"cannot be used, {block}." if block else "can be used.")
        self.version_status.setText(text)

    def activate_selected(self) -> None:
        info = self.selected()
        if self.context is None or info is None:
            self.version_status.setText("Select a version first.")
            return
        refused = self.context.service.activate(info.id)
        self.version_status.setText(refused or f"Now using model v{info.version}.")
        self.refresh_versions()

    def rollback(self) -> None:
        if self.context is None:
            return
        refused = self.context.service.rollback()
        self.version_status.setText(refused or "Rolled back.")
        self.refresh_versions()

    def use_baseline(self) -> None:
        if self.context is None:
            return
        self.context.service.use_baseline()
        self.version_status.setText("Now using the baseline.")
        self.refresh_versions()
