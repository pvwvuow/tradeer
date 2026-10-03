"""The Backtest page (spec F3 page 7): the form, the results, walk-forward, Monte-Carlo, the
sensitivity heatmap and the saved runs to compare.

A run reads the history from MT5 (through the gateway, cached on disk) and replays the live
strategy, risk and execution code on it in a background thread, so the window never waits.
Progress and the result come back through Qt signals. The MT5 Strategy Tester cannot be
driven from Python; this engine replaces it.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QDate, QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QGroupBox,
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

from app.backtest import metrics as metric_text
from app.backtest import monte_carlo as carlo_text
from app.backtest import walk_forward as walk_text
from app.backtest.costs import BacktestCosts
from app.backtest.engine import History
from app.backtest.metrics import DAY, Group, drawdown_curve
from app.backtest.service import (
    BacktestReport,
    BacktestRequest,
    SensitivityOptions,
    WalkForwardOptions,
    default_request,
    parse_values,
    run_report,
)
from app.calendar.models import CalendarEvent
from app.core.execution_settings import ExecutionSettingsSource
from app.core.param_fields import param_fields
from app.core.strategy_settings import StrategySettingsSource
from app.risk.settings import RiskSettingsSource
from app.storage.backtest_store import BacktestRepository, SavedRun
from app.strategies.registry import STRATEGIES
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.strategies_page import ParamsForm

INTRO = (
    "Replays the live strategy, risk and execution code bar by bar on MT5 history: entries at "
    "the next bar open, the bar's spread, commission, slippage and swap, and a stop loss and "
    "take profit inside one candle count as a loss. The MT5 Strategy Tester cannot be driven "
    "from Python, so this engine replaces it. Past results do not promise future ones."
)
NOT_CONNECTED = "Connect to MT5 first: the backtest reads the history from MT5."
GROUPS = ("Month", "Session", "Weekday", "Strategy", "Symbol")

Note = Callable[[str], None]
Loader = Callable[[BacktestRequest, Note], tuple[History, tuple[str, ...]]]


@dataclass
class BacktestContext:
    load: Loader  # the history for a request (from MT5 in the app, synthetic in tests)
    strategies: StrategySettingsSource
    risk: RiskSettingsSource
    execution: ExecutionSettingsSource
    symbols: Callable[[], Sequence[str]]
    connected: Callable[[], bool]
    events: Callable[[float, float], Sequence[CalendarEvent]] | None = None
    runs: BacktestRepository | None = None
    account: Callable[[], str | None] | None = None
    log: Callable[[str, str], None] | None = None


class _Bridge(QObject):
    progress = Signal(str, int, int)
    finished = Signal(object)
    failed = Signal(str)


def _table(columns: Sequence[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, text in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(text))


def _number(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}"


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M")


def _to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def numeric_params(name: str) -> list[str]:
    fields = param_fields(STRATEGIES[name].params_model)
    return [spec.name for spec in fields if spec.kind in ("int", "float")]


def group_rows(groups: Sequence[Group]) -> list[list[str]]:
    return [
        [
            item.key,
            str(item.trades),
            f"{item.win_rate * 100:.1f}%",
            _number(item.net_profit),
            _number(item.expectancy_r, 3),
            _number(item.profit_factor),
        ]
        for item in groups
    ]


def heat_color(value: float | None, low: float, high: float) -> QColor:
    """Red for the worst cell, green for the best, grey without a result."""
    if value is None:
        return QColor(90, 90, 90)
    share = 0.5 if high <= low else (value - low) / (high - low)
    return QColor(int(200 * (1 - share)) + 30, int(170 * share) + 40, 70)


class _ParamPicker(QWidget):
    """A strategy's numeric parameter and the values to try (comma separated)."""

    def __init__(self, label: str, default: str, values: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(styled_label(label, "muted"))
        self.name = QComboBox()
        self.values = QLineEdit(values)
        self.values.setPlaceholderText("values, e.g. 1.5, 2, 2.5")
        row.addWidget(self.name)
        row.addWidget(self.values, 1)
        self._default = default

    def set_strategy(self, strategy: str) -> None:
        current = self.name.currentText() or self._default
        self.name.clear()
        names = numeric_params(strategy)
        self.name.addItems(names)
        if current in names:
            self.name.setCurrentText(current)

    def read(self) -> tuple[str, list[float]]:
        return self.name.currentText(), parse_values(self.values.text())


class BacktestPage(QWidget):
    def __init__(self, context: BacktestContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_backtest")
        self.context = context
        self.bridge = _Bridge()
        self.bridge.progress.connect(self.show_progress, Qt.ConnectionType.QueuedConnection)
        self.bridge.finished.connect(self.show_report, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        self.report: BacktestReport | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        defaults = default_request()
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
        layout.addWidget(styled_label("Backtest", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        layout.addWidget(self._build_form(defaults))
        layout.addWidget(self._build_results())
        layout.addStretch(1)
        if context is None:
            self.run_button.setEnabled(False)
            self.status.setText("The backtest needs the MT5 connection and the settings.")
        self.refresh_runs()

    # Form ---------------------------------------------------------------------------------
    def _build_form(self, defaults: BacktestRequest) -> QWidget:
        card, layout = card_frame()
        top = QFormLayout()
        self.symbol = QComboBox()
        self.symbol.setObjectName("BacktestSymbol")
        self.symbol.setEditable(True)
        names = list(self.context.symbols()) if self.context is not None else []
        self.symbol.addItems(names or [defaults.symbol])
        self.start = QDateEdit(_qdate(defaults.start))
        self.end = QDateEdit(_qdate(defaults.end))
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        top.addRow("Symbol", self.symbol)
        top.addRow("From (UTC)", self.start)
        top.addRow("To (UTC, inclusive)", self.end)
        layout.addLayout(top)
        strategies = QHBoxLayout()
        self.strategy_boxes: dict[str, QCheckBox] = {}
        for name, kind in STRATEGIES.items():
            box = QCheckBox(kind.title)
            box.setObjectName(f"backtest_{name}")
            box.setChecked(True)
            self.strategy_boxes[name] = box
            strategies.addWidget(box)
        strategies.addStretch(1)
        note = "Strategies (with the settings of the Strategies page)"
        layout.addWidget(styled_label(note, "muted"))
        layout.addLayout(strategies)
        costs = QGroupBox("Costs and account")
        costs_layout = QVBoxLayout(costs)
        self.costs = ParamsForm(BacktestCosts, BacktestCosts().model_dump(mode="json"))
        costs_layout.addWidget(self.costs)
        layout.addWidget(costs)
        layout.addWidget(self._build_robustness())
        buttons = QHBoxLayout()
        self.run_button = QPushButton("Run backtest")
        self.run_button.setObjectName("BacktestRun")
        self.run_button.setProperty("variant", "primary")
        self.run_button.clicked.connect(self.start_run)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("BacktestCancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("BacktestStatus")
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        return card

    def _build_robustness(self) -> QWidget:
        box = QGroupBox("Robustness")
        layout = QVBoxLayout(box)
        carlo = QHBoxLayout()
        carlo.addWidget(styled_label("Monte-Carlo runs", "muted"))
        self.carlo_runs = QSpinBox()
        self.carlo_runs.setRange(0, 20_000)
        self.carlo_runs.setValue(1000)
        carlo.addWidget(self.carlo_runs)
        carlo.addStretch(1)
        layout.addLayout(carlo)
        names = list(STRATEGIES)
        self.walk_enabled = QCheckBox("Walk-forward: choose on the past window, trade the next one")
        self.walk_enabled.setObjectName("BacktestWalkForward")
        self.walk_strategy = QComboBox()
        self.walk_strategy.addItems(names)
        self.walk_param = _ParamPicker("parameter", "reward_r", "1.5, 2, 2.5")
        self.in_days = QSpinBox()
        self.in_days.setRange(1, 3650)
        self.in_days.setValue(90)
        self.out_days = QSpinBox()
        self.out_days.setRange(1, 3650)
        self.out_days.setValue(30)
        walk = QHBoxLayout()
        for widget in (self.walk_strategy, self.walk_param):
            walk.addWidget(widget)
        walk.addWidget(styled_label("in-sample days", "muted"))
        walk.addWidget(self.in_days)
        walk.addWidget(styled_label("out-of-sample days", "muted"))
        walk.addWidget(self.out_days)
        layout.addWidget(self.walk_enabled)
        layout.addLayout(walk)
        self.sens_enabled = QCheckBox("Sensitivity heatmap of two parameters")
        self.sens_enabled.setObjectName("BacktestSensitivity")
        self.sens_strategy = QComboBox()
        self.sens_strategy.addItems(names)
        self.sens_x = _ParamPicker("x", "reward_r", "1.5, 2, 2.5")
        self.sens_y = _ParamPicker("y", "sl_atr", "1, 1.5, 2")
        sens = QHBoxLayout()
        for widget in (self.sens_strategy, self.sens_x, self.sens_y):
            sens.addWidget(widget)
        layout.addWidget(self.sens_enabled)
        layout.addLayout(sens)
        self.walk_strategy.currentTextChanged.connect(self.walk_param.set_strategy)
        self.sens_strategy.currentTextChanged.connect(self.sens_x.set_strategy)
        self.sens_strategy.currentTextChanged.connect(self.sens_y.set_strategy)
        for picker, strategy in (
            (self.walk_param, self.walk_strategy),
            (self.sens_x, self.sens_strategy),
            (self.sens_y, self.sens_strategy),
        ):
            picker.set_strategy(strategy.currentText())
        return box

    def build_request(self) -> BacktestRequest:
        """The form as a request. Raises ValueError (or ValidationError) on bad input."""
        costs, problems = self.costs.validated()
        if costs is None:
            raise ValueError("costs: " + "; ".join(problems))
        chosen = [name for name, box in self.strategy_boxes.items() if box.isChecked()]
        walk: WalkForwardOptions | None = None
        if self.walk_enabled.isChecked():
            name, values = self.walk_param.read()
            walk = WalkForwardOptions(
                strategy=self.walk_strategy.currentText(),
                grid={name: values},
                in_days=self.in_days.value(),
                out_days=self.out_days.value(),
            )
        sens: SensitivityOptions | None = None
        if self.sens_enabled.isChecked():
            x_name, x_values = self.sens_x.read()
            y_name, y_values = self.sens_y.read()
            sens = SensitivityOptions(
                strategy=self.sens_strategy.currentText(),
                x_name=x_name,
                x_values=x_values,
                y_name=y_name,
                y_values=y_values,
            )
        return BacktestRequest(
            symbol=self.symbol.currentText().strip() or "EURUSD",
            start=_to_date(self.start.date()),
            end=_to_date(self.end.date()),
            strategies=chosen,
            costs=BacktestCosts.model_validate(costs.model_dump()),
            monte_carlo_runs=self.carlo_runs.value(),
            walk_forward=walk,
            sensitivity=sens,
        )

    # Running ------------------------------------------------------------------------------
    def start_run(self) -> None:
        context = self.context
        if context is None or self.running:
            return
        if not context.connected():
            self.status.setText(NOT_CONNECTED)
            return
        try:
            request = self.build_request()
        except ValueError as error:  # pydantic's ValidationError is a ValueError
            self.status.setText(f"Check the form: {error}")
            return
        self._cancel.clear()
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText(f"Loading {request.symbol} history from MT5...")
        self._thread = threading.Thread(
            target=self._work,
            args=(context, request),
            name="backtest",
            daemon=True,
        )
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel_run(self) -> None:
        self._cancel.set()
        self.status.setText("Cancelling after the current day...")

    def _work(self, context: BacktestContext, request: BacktestRequest) -> None:
        log = context.log or (lambda level, message: None)
        try:
            history, notes = context.load(
                request,
                lambda text: self.bridge.progress.emit(text, 0, 0),
            )
            events: Sequence[CalendarEvent] = ()
            if context.events is not None:
                events = context.events(request.utc_start - 2 * DAY, request.utc_end + 7 * DAY)
            report = run_report(
                history,
                request,
                settings=context.strategies.settings,
                risk=context.risk.config,
                execution=context.execution.config,
                events=events,
                progress=self.bridge.progress.emit,
                cancelled=self._cancel.is_set,
                log=log,
                notes=notes,
            )
            if context.runs is not None and not report.result.cancelled:
                account = context.account() if context.account is not None else None
                context.runs.save(account, **report.storage_row())
            for line in metric_text.summary_lines(report.metrics):
                log("INFO", f"Backtest {request.symbol} {request.period}: {line}")
            self.bridge.finished.emit(report)
        except Exception as error:
            log("ERROR", f"Backtest failed: {type(error).__name__}: {error}")
            self.bridge.failed.emit(f"{type(error).__name__}: {error}")

    def show_progress(self, stage: str, done: int, total: int) -> None:
        if total > 0:
            self.progress.setValue(int(done * 100 / total))
            self.status.setText(f"{stage}: {done} of {total}")
        else:
            self.status.setText(stage)

    def show_failure(self, text: str) -> None:
        self.run_button.setEnabled(self.context is not None)
        self.cancel_button.setEnabled(False)
        self.status.setText(f"The backtest failed: {text}")

    # Results ------------------------------------------------------------------------------
    def _build_results(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setObjectName("BacktestTabs")
        self.tabs.setMinimumHeight(460)
        self.summary = styled_label("No backtest yet.", "muted", wrap=True)
        self.summary.setObjectName("BacktestSummary")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.addWidget(self.summary)
        summary_layout.addStretch(1)
        self.tabs.addTab(summary, "Summary")
        curves = QWidget()
        curves_layout = QVBoxLayout(curves)
        self.equity_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.equity_plot.setLabel("left", "Equity")
        self.drawdown_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.drawdown_plot.setLabel("left", "Drawdown %")
        self.drawdown_plot.setXLink(self.equity_plot)
        curves_layout.addWidget(self.equity_plot, 3)
        curves_layout.addWidget(self.drawdown_plot, 1)
        self.tabs.addTab(curves, "Equity")
        self.trades = _table(
            (
                "Opened (UTC)",
                "Strategy",
                "Side",
                "Lots",
                "Entry",
                "Exit",
                "Net",
                "R",
                "Exit reason",
            ),
        )
        self.trades.setObjectName("BacktestTrades")
        self.tabs.addTab(self.trades, "Trades")
        breakdown = QWidget()
        breakdown_layout = QVBoxLayout(breakdown)
        self.group_choice = QComboBox()
        self.group_choice.addItems(list(GROUPS))
        self.group_choice.currentTextChanged.connect(lambda _: self._show_groups())
        columns = ("Group", "Trades", "Win rate", "Net", "Expectancy R", "Profit factor")
        self.groups = _table(columns)
        breakdown_layout.addWidget(self.group_choice)
        breakdown_layout.addWidget(self.groups)
        self.tabs.addTab(breakdown, "Breakdowns")
        walk = QWidget()
        walk_layout = QVBoxLayout(walk)
        self.walk_verdict = styled_label("Walk-forward was not run.", "muted", wrap=True)
        self.walk_verdict.setObjectName("BacktestWalkVerdict")
        self.folds = _table(
            (
                "In-sample",
                "Out-of-sample",
                "Chosen",
                "In score",
                "Out trades",
                "Out R",
                "Out net",
            ),
        )
        walk_layout.addWidget(self.walk_verdict)
        walk_layout.addWidget(self.folds)
        self.tabs.addTab(walk, "Walk-forward")
        carlo = QWidget()
        carlo_layout = QVBoxLayout(carlo)
        self.carlo_text = styled_label("Monte-Carlo was not run.", "muted", wrap=True)
        self.carlo_text.setObjectName("BacktestCarlo")
        self.carlo_plot = pg.PlotWidget()
        self.carlo_plot.setLabel("bottom", "Max drawdown % per run")
        carlo_layout.addWidget(self.carlo_text)
        carlo_layout.addWidget(self.carlo_plot, 1)
        self.tabs.addTab(carlo, "Monte-Carlo")
        heat = QWidget()
        heat_layout = QVBoxLayout(heat)
        self.sens_verdict = styled_label("The sensitivity test was not run.", "muted", wrap=True)
        self.sens_verdict.setObjectName("BacktestSensVerdict")
        self.heatmap = _table(())
        self.heatmap.setObjectName("BacktestHeatmap")
        heat_layout.addWidget(self.sens_verdict)
        heat_layout.addWidget(self.heatmap)
        self.tabs.addTab(heat, "Sensitivity")
        self.saved = _table(
            (
                "Saved (UTC)",
                "Run",
                "Trades",
                "Expectancy R",
                "Profit factor",
                "Net",
                "Max DD %",
                "Walk-forward",
            ),
        )
        self.saved.setObjectName("BacktestSaved")
        self.tabs.addTab(self.saved, "Saved runs")
        return self.tabs

    def show_report(self, report: BacktestReport) -> None:
        self.report = report
        self.run_button.setEnabled(self.context is not None)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(100)
        result, metrics = report.result, report.metrics
        state = "cancelled" if result.cancelled else "done"
        self.status.setText(
            f"Backtest {state}: {result.bars} bars, {result.analyses} closed-bar checks, "
            f"{result.seconds:.0f} s.",
        )
        currency = report.request.costs.account_currency
        lines = metric_text.summary_lines(metrics, currency)
        if report.monte_carlo is not None:
            lines += carlo_text.summary_lines(report.monte_carlo)
        if report.walk_forward is not None:
            lines.append(walk_text.summary_lines(report.walk_forward)[0])
        if report.sensitivity is not None:
            lines.append(report.sensitivity.verdict())
        states = sorted(result.counts().items())
        counts = ", ".join(f"{name.lower()} {count}" for name, count in states)
        lines.append(f"Signals: {counts or 'none'}")
        lines += [*report.notes, *result.notes]
        self.summary.setText("\n".join(lines))
        self._show_curves(report)
        _fill(
            self.trades,
            [
                [
                    _utc(t.open_time),
                    t.strategy,
                    t.direction,
                    f"{t.volume:g}",
                    f"{t.open_price:g}",
                    f"{t.close_price:g}",
                    _number(t.net_profit),
                    _number(t.r_multiple),
                    t.exit_reason,
                ]
                for t in result.trades
            ],
        )
        self._show_groups()
        self._show_walk(report)
        self._show_carlo(report)
        self._show_heatmap(report)
        self.refresh_runs()

    def _show_curves(self, report: BacktestReport) -> None:
        result = report.result
        self.equity_plot.clear()
        self.drawdown_plot.clear()
        if not len(result.times):
            return
        step = max(1, len(result.times) // 4000)  # at most ~4000 points per curve
        times = result.times[::step]
        self.equity_plot.plot(times, result.equity[::step], pen=pg.mkPen("#4f8cff", width=2))
        self.equity_plot.plot(times, result.balance[::step], pen=pg.mkPen("#8a8f98", width=1))
        curve = drawdown_curve(result.equity)[::step]
        self.drawdown_plot.plot(
            times,
            curve,
            pen=pg.mkPen("#e5534b", width=1),
            fillLevel=0,
            brush=(229, 83, 75, 60),
        )

    def _show_groups(self) -> None:
        if self.report is None:
            return
        metrics = self.report.metrics
        by_name = {
            "Month": metrics.by_month,
            "Session": metrics.by_session,
            "Weekday": metrics.by_weekday,
            "Strategy": metrics.by_strategy,
            "Symbol": metrics.by_symbol,
        }
        _fill(self.groups, group_rows(by_name[self.group_choice.currentText()]))

    def _show_walk(self, report: BacktestReport) -> None:
        found = report.walk_forward
        if found is None:
            self.walk_verdict.setText("Walk-forward was not run.")
            _fill(self.folds, [])
            return
        self.walk_verdict.setText(walk_text.summary_lines(found)[0])
        options = report.request.walk_forward
        tried = set(options.grid) if options is not None else set()

        def day(seconds: float) -> str:
            return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")

        _fill(
            self.folds,
            [
                [
                    f"{day(f.window.in_start)}..{day(f.window.in_end)}",
                    f"{day(f.window.out_start)}..{day(f.window.out_end)}",
                    ", ".join(f"{k}={v}" for k, v in f.params.items() if k in tried),
                    _number(f.in_score, 3),
                    str(f.out_trades),
                    _number(f.out_expectancy_r, 3),
                    _number(f.out_net),
                ]
                for f in found.folds
            ],
        )

    def _show_carlo(self, report: BacktestReport) -> None:
        self.carlo_plot.clear()
        found = report.monte_carlo
        if found is None or not found.trades:
            self.carlo_text.setText("Monte-Carlo was not run (or there were no trades).")
            return
        self.carlo_text.setText("\n".join(carlo_text.summary_lines(found)))
        counts, edges = np.histogram(np.asarray(found.max_drawdowns), bins=30)
        width = float(edges[1] - edges[0]) if len(edges) > 1 else 1.0
        bars = pg.BarGraphItem(
            x=edges[:-1] + width / 2,
            height=counts,
            width=width * 0.9,
            brush="#4f8cff",
        )
        self.carlo_plot.addItem(bars)

    def _show_heatmap(self, report: BacktestReport) -> None:
        found = report.sensitivity
        if found is None:
            self.sens_verdict.setText("The sensitivity test was not run.")
            self.heatmap.setRowCount(0)
            self.heatmap.setColumnCount(0)
            return
        self.sens_verdict.setText(found.verdict())
        self.heatmap.setColumnCount(len(found.x_values))
        self.heatmap.setRowCount(len(found.y_values))
        self.heatmap.setHorizontalHeaderLabels([f"{found.x_name}={v:g}" for v in found.x_values])
        self.heatmap.setVerticalHeaderLabels([f"{found.y_name}={v:g}" for v in found.y_values])
        self.heatmap.verticalHeader().setVisible(True)
        known = [value for row in found.values for value in row if value is not None]
        low, high = (min(known), max(known)) if known else (0.0, 0.0)
        for y, row in enumerate(found.values):
            for x, value in enumerate(row):
                trades = found.trades[y][x]
                item = QTableWidgetItem(f"{_number(value, 3)}\n{trades} trades")
                item.setBackground(heat_color(value, low, high))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.heatmap.setItem(y, x, item)
        self.heatmap.resizeRowsToContents()

    def refresh_runs(self) -> None:
        runs: list[SavedRun] = []
        if self.context is not None and self.context.runs is not None:
            try:
                runs = self.context.runs.recent()
            except Exception:
                runs = []
        rows: list[list[str]] = []
        for run in runs:
            m = run.metrics
            dd = m.get("max_drawdown") or {}
            walk = run.walk_forward or {}
            verdict = "" if not walk else ("passed" if walk.get("passed") else "not passed")
            rows.append(
                [
                    _utc(run.created_at),
                    run.title,
                    str(m.get("trades", "")),
                    _number(m.get("expectancy_r"), 3),
                    _number(m.get("profit_factor")),
                    _number(m.get("net_profit")),
                    _number(dd.get("depth_percent")),
                    verdict,
                ],
            )
        _fill(self.saved, rows)
