"""The AI Lab page (spec C13, F3.9, docs/AI_LAB_AGENT.md).

Two tabs. **Chat** (Phase 18b, 18c): ask in your own words; the agent reads the trades, the
settings, the signals with their decision traces and the saved logs with read-only tools,
shows each step and answers. **Manual**: the loop with an outside AI, in four steps.

1. Export the trades and a report with an analysis prompt for the AI of your choice, or
   (optional, off by default) ask your own OpenAI-compatible endpoint from here: only a
   compact summary is sent and the answer lands in step 2.
2. Paste its JSON answer: every change is checked against the strategy's parameter schema
   and shown as a difference to the current values.
3. Backtest the suggestion against the current settings on the same symbol and period.
4. Activate it, only in Paper or Analysis-only mode: the settings are saved, a new config
   version (created by "ai_suggestion") and an audit row are written.

The gear at the top opens the AI Lab settings window (Phase 18a).
An AI answer is advice only, and this page never sends real orders.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai.agent import Tool
from app.ai.lab_tools import LabData, lab_tools
from app.analytics.ai_export import PROMPT, ExportData, rejected_counts, select_trades, write_export
from app.analytics.ai_import import (
    RunSummary,
    Suggestion,
    Verdict,
    activation_block,
    apply_suggestion,
    audit_row,
    compare_runs,
    config_rows,
    diff_rows,
    parse_suggestion,
    summary_rows,
)
from app.analytics.llm_client import LlmContext
from app.analytics.trades import TradeRecord
from app.backtest.costs import BacktestCosts
from app.backtest.service import BacktestReport, BacktestRequest, run_report, strategy_params
from app.calendar.models import CalendarEvent
from app.core.execution_settings import ExecutionSettingsSource
from app.core.strategy_settings import StrategySettings, StrategySettingsSource
from app.domain.signals import SignalRecord
from app.observability.log_reader import read_entries
from app.storage.repositories import Store
from app.storage.signal_store import SignalRepository
from app.strategies.registry import STRATEGIES
from app.ui.ai_chat import ChatPanel
from app.ui.analytics_page import AnalyticsContext, start_balance
from app.ui.backtest_page import BacktestContext
from app.ui.llm_panel import SETTINGS_TIP, LlmPanel
from app.ui.pages import PAGE_MARGIN, card_frame, page_header_for, styled_label
from app.ui.tables import fill_table, make_table

ALL = "All"
DAY = 86_400
EXPORT_FOLDER = "exports"
SIGNALS_FOR_AI = 300
INTRO = (
    "Ask an AI to review your trading. Export the trades and a report, give them to the AI "
    "you like, paste its JSON answer back, backtest it against your current settings and "
    "turn it on in Paper. The app checks every value against the strategy's limits; nothing "
    "here sends real orders."
)
NOT_BETTER = (
    "The suggestion did not clearly beat your current settings in the backtest.\n\n"
    "Activate it in Paper anyway, to watch it on live prices?"
)


def _quiet(level: str, message: str) -> None:
    return None


def _no_balance() -> float:
    return math.nan


def _no_currency() -> str:
    return ""


def _no_account() -> str | None:
    return None


def _no_runs() -> Sequence[tuple[str, Mapping[str, Any]]]:
    return ()


def log_reader(root: Path | None) -> Callable[[float], Sequence[Mapping[str, Any]]]:
    """The saved log entries since a time, read in the agent's worker thread."""

    def read(since: float) -> Sequence[Mapping[str, Any]]:
        if root is None:
            return []
        return read_entries(root, since=since)

    return read


@dataclass
class AiLabContext:
    trades: Callable[[], Sequence[TradeRecord]]
    strategies: StrategySettingsSource
    execution: ExecutionSettingsSource
    export_dir: Path
    backtest: BacktestContext | None = None
    store: Store | None = None
    balance: Callable[[], float] = field(default=_no_balance)
    currency: Callable[[], str] = field(default=_no_currency)
    backtests: Callable[[], Sequence[tuple[str, Mapping[str, Any]]]] = field(default=_no_runs)
    account: Callable[[], str | None] = field(default=_no_account)
    log: Callable[[str, str], None] = field(default=_quiet)


def ai_lab_context(
    analytics: AnalyticsContext | None,
    backtest: BacktestContext | None,
) -> AiLabContext | None:
    """The page's context from the Analytics and Backtest ones (both are needed)."""
    if analytics is None or backtest is None:
        return None
    return AiLabContext(
        trades=analytics.trades,
        strategies=backtest.strategies,
        execution=backtest.execution,
        export_dir=analytics.export_dir,
        backtest=backtest,
        store=backtest.runs.store if backtest.runs is not None else None,
        balance=analytics.balance,
        currency=analytics.currency,
        backtests=analytics.backtests,
        account=backtest.account or _no_account,
        log=analytics.log,
    )


@dataclass(frozen=True)
class Comparison:
    suggestion: Suggestion
    current: BacktestReport
    proposed: BacktestReport


class _Bridge(QObject):
    progress = Signal(str, int, int)
    finished = Signal(object)
    failed = Signal(str)


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


class AiLabPage(QWidget):
    def __init__(self, context: AiLabContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_ai_lab")
        self.context = context
        self.logs_dir: Path | None = None  # None: the main window's log folder
        self.suggestion: Suggestion | None = None
        self.verdict: Verdict | None = None
        self.comparison: Comparison | None = None
        self.export_paths: list[Path] = []
        self.confirm: Callable[[str], bool] = self._ask
        self._tested: Suggestion | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        self.bridge = _Bridge()
        self.bridge.progress.connect(self.show_progress, Qt.ConnectionType.QueuedConnection)
        self.bridge.finished.connect(self.show_comparison, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        outer.setSpacing(12)
        self.header = page_header_for("ai_lab")
        self.settings_button = QPushButton("\u2699  Settings")
        self.settings_button.setObjectName("AiLabSettings")
        self.settings_button.setAccessibleName("AI Lab settings")
        self.settings_button.setToolTip(SETTINGS_TIP)
        self.header.add_action(self.settings_button)
        outer.addWidget(self.header)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("AiLabTabs")
        outer.addWidget(self.tabs, 1)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        scroll_area.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        layout.addWidget(self._build_export())
        self.llm_panel = LlmPanel(self.llm_data, self.take_answer)
        self.settings_button.clicked.connect(self.llm_panel.open_settings)
        layout.addWidget(self.llm_panel)
        layout.addWidget(self._build_import())
        layout.addWidget(self._build_test())
        layout.addWidget(self._build_activate())
        layout.addStretch(1)
        self.chat = ChatPanel(self.llm_panel.make_client, self.agent_tools)
        self.tabs.addTab(self.chat, "Chat")
        self.tabs.addTab(scroll_area, "Manual (export and paste)")
        if context is None:
            for button in (self.export_button, self.check_button):
                button.setEnabled(False)
            self.export_status.setText("The AI Lab needs the local database and the settings.")
        self._update_buttons()

    def open_settings(self) -> None:
        """The AI Lab settings window (the gear)."""
        self.llm_panel.open_settings()

    def log_root(self) -> Path | None:
        """The folder of the saved logs: set on the page, or the main window's."""
        if self.logs_dir is not None:
            return self.logs_dir
        controls = getattr(self.window(), "log_controls", None)
        found = getattr(controls, "log_dir", None)
        return found if isinstance(found, Path) else None

    def saved_signals(self) -> list[SignalRecord]:
        """The newest saved signals with their traces, read here in the UI thread."""
        context = self.context
        if context is None or context.store is None:
            return []
        try:
            return SignalRepository(context.store).recent(SIGNALS_FOR_AI)
        except Exception as error:
            context.log("WARNING", f"AI Lab: the signals could not be read: {error}")
            return []

    def agent_tools(self) -> list[Tool]:
        """The chat's read-only tools over this page's data (none without a context)."""
        context = self.context
        if context is None:
            return []
        strategies = context.strategies
        execution = context.execution
        records = self.saved_signals()
        return lab_tools(
            LabData(
                trades=context.trades,
                settings=lambda: strategies.settings,
                mode=lambda: execution.mode.label,
                balance=context.balance,
                currency=context.currency,
                backtests=context.backtests,
                signals=lambda: records,
                log_entries=log_reader(self.log_root()),
            ),
        )

    # 1. Export ----------------------------------------------------------------------------
    def _build_export(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("1. Export for the AI", "heading"))
        row = QHBoxLayout()
        self.export_strategy = QComboBox()
        self.export_strategy.setObjectName("AiExportStrategy")
        self.export_strategy.addItems([ALL, *STRATEGIES, "manual"])
        self.export_mode = QComboBox()
        self.export_mode.setObjectName("AiExportMode")
        self.export_mode.addItems([ALL, "live", "paper"])
        self.export_days = QSpinBox()
        self.export_days.setObjectName("AiExportDays")
        self.export_days.setRange(0, 3650)
        self.export_days.setValue(180)
        self.export_days.setSpecialValueText("all time")
        self.export_days.setSuffix(" days")
        self.export_button = QPushButton("Export for AI")
        self.export_button.setObjectName("AiExport")
        self.export_button.setProperty("variant", "primary")
        self.export_button.clicked.connect(self.export_for_ai)
        self.copy_button = QPushButton("Copy prompt")
        self.copy_button.setObjectName("AiCopyPrompt")
        self.copy_button.clicked.connect(self.copy_prompt)
        widgets: list[QWidget] = [
            styled_label("Strategy", "muted"),
            self.export_strategy,
            styled_label("Mode", "muted"),
            self.export_mode,
            styled_label("Last", "muted"),
            self.export_days,
            self.export_button,
            self.copy_button,
        ]
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self.export_status = styled_label(
            "Writes trades_full.csv, trades_full.json and report.md (the prompt is at the top).",
            "muted",
            wrap=True,
        )
        self.export_status.setObjectName("AiExportStatus")
        self.export_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.export_status)
        return card

    def export_data(self, context: AiLabContext, now: float) -> ExportData:
        """The trades and settings chosen in step 1 (the export and the summary use them)."""
        strategy = self.export_strategy.currentText()
        mode = self.export_mode.currentText()
        days = self.export_days.value()
        trades = select_trades(
            list(context.trades()),
            now,
            strategy="" if strategy == ALL else strategy,
            mode="" if mode == ALL else mode,
            days=days,
        )
        rejected: list[tuple[str, int]] = []
        if context.store is not None:
            rejected = rejected_counts(context.store.db)
        scope = f"strategy {strategy.lower()}, mode {mode.lower()}, "
        scope += f"last {days} days" if days else "all time"
        return ExportData(
            trades=trades,
            settings=context.strategies.settings,
            mode=context.execution.mode,
            start_balance=start_balance(context.balance(), trades),
            currency=context.currency(),
            backtests=context.backtests(),
            rejected=rejected,
            scope=scope,
        )

    def export_for_ai(self) -> list[Path]:
        context = self.context
        if context is None:
            return []
        now = time.time()
        try:
            data = self.export_data(context, now)
            stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%d_%H%M%S")
            folder = context.export_dir / EXPORT_FOLDER / f"ai_{stamp}"
            paths = write_export(folder, data, now)
        except Exception as error:
            self.export_status.setText(f"The export failed: {type(error).__name__}: {error}")
            context.log("ERROR", f"AI export failed: {type(error).__name__}: {error}")
            return []
        self.export_paths = paths
        self.export_status.setText(
            f"Saved {len(data.trades)} trades in {folder}. Give the AI all three files, or "
            "paste report.md and attach the CSV.",
        )
        context.log("INFO", f"AI export: {len(data.trades)} trades to {folder}")
        return paths

    def copy_prompt(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(PROMPT)
        self.export_status.setText("The prompt is on the clipboard.")

    # 1b. Optional request to the user's own AI --------------------------------------------
    def attach_llm(self, llm: LlmContext) -> None:
        """Turn on the optional "Ask AI" card (it stays off until the user saves it on)."""
        self.llm_panel.attach(llm)

    def llm_data(self, now: float) -> ExportData | None:
        context = self.context
        return self.export_data(context, now) if context is not None else None

    def take_answer(self, text: str) -> str:
        """An AI answer from the card: into step 2 and checked like a pasted one."""
        self.answer.setPlainText(text)
        found = self.check_suggestion()
        if found is not None and found.valid:
            return "Step 2 shows its valid suggestion; backtest it next."
        if found is not None and found.changes:
            return "Step 2 shows its suggestion, but it has problems."
        return "It suggests no valid change; read it in step 2."

    # 2. Import ----------------------------------------------------------------------------
    def _build_import(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("2. Paste the AI's answer", "heading"))
        self.answer = QPlainTextEdit()
        self.answer.setObjectName("AiAnswer")
        self.answer.setPlaceholderText(
            'The whole answer or just its JSON block: {"changes": [{"strategy": '
            '"trend_pullback", "params": {"min_adx_h1": 25}, "reason": "..."}]}',
        )
        self.answer.setMinimumHeight(140)
        layout.addWidget(self.answer)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check suggestion")
        self.check_button.setObjectName("AiCheck")
        self.check_button.setProperty("variant", "accent")
        self.check_button.clicked.connect(self.check_suggestion)
        row.addWidget(self.check_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.diff = make_table(("Strategy", "Parameter", "Now", "Suggested"))
        self.diff.setObjectName("AiDiff")
        self.diff.setMinimumHeight(160)
        layout.addWidget(self.diff)
        self.check_status = styled_label("", "muted", wrap=True)
        self.check_status.setObjectName("AiCheckStatus")
        layout.addWidget(self.check_status)
        return card

    def check_suggestion(self) -> Suggestion | None:
        context = self.context
        if context is None:
            return None
        found = parse_suggestion(self.answer.toPlainText(), context.strategies.settings)
        self.suggestion = found
        self.verdict = None
        self.comparison = None
        self._tested = None
        fill_table(self.diff, diff_rows(found))
        fill_table(self.results, [])
        self.verdict_label.setText("Not tested yet.")
        lines: list[str] = []
        for change in found.changes:
            text = f"{change.strategy}: {change.reason}"
            if change.expected_impact:
                text += f" Expected: {change.expected_impact}"
            lines.append(text)
        lines += [f"Problem: {problem}" for problem in found.problems]
        if found.valid:
            lines.append("Valid. Next: run the backtest comparison.")
        elif found.changes:
            lines.append("Fix the problems (or remove those changes) before testing.")
        self.check_status.setText("\n".join(lines))
        self._update_buttons()
        return found

    # 3. Test ------------------------------------------------------------------------------
    def _build_test(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("3. Backtest it against the current settings", "heading"))
        row = QHBoxLayout()
        self.symbol = QComboBox()
        self.symbol.setObjectName("AiTestSymbol")
        self.symbol.setEditable(True)
        backtest = self.context.backtest if self.context is not None else None
        names = list(backtest.symbols()) if backtest is not None else []
        self.symbol.addItems(names or ["EURUSD"])
        today = datetime.now(UTC).date()
        self.start = QDateEdit(_qdate(today - timedelta(days=181)))
        self.end = QDateEdit(_qdate(today - timedelta(days=1)))
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        self.test_button = QPushButton("Run comparison")
        self.test_button.setObjectName("AiTest")
        self.test_button.setProperty("variant", "primary")
        self.test_button.clicked.connect(self.start_test)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("AiTestCancel")
        self.cancel_button.clicked.connect(self.cancel_test)
        widgets: list[QWidget] = [
            styled_label("Symbol", "muted"),
            self.symbol,
            styled_label("From (UTC)", "muted"),
            self.start,
            styled_label("to", "muted"),
            self.end,
            self.test_button,
            self.cancel_button,
        ]
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.test_status = styled_label("", "muted", wrap=True)
        self.test_status.setObjectName("AiTestStatus")
        layout.addWidget(self.test_status)
        self.results = make_table(("Metric", "Current settings", "Suggested"))
        self.results.setObjectName("AiResults")
        self.results.setMinimumHeight(220)
        layout.addWidget(self.results)
        self.verdict_label = styled_label("Not tested yet.", "muted", wrap=True)
        self.verdict_label.setObjectName("AiVerdict")
        layout.addWidget(self.verdict_label)
        return card

    @property
    def running(self) -> bool:
        """A comparison is running (until its result or failure reached the page)."""
        return self._busy

    def build_request(self, suggestion: Suggestion) -> BacktestRequest:
        """Only the changed strategies, no Monte-Carlo. Raises ValueError on bad dates."""
        return BacktestRequest(
            symbol=self.symbol.currentText().strip() or "EURUSD",
            start=_to_date(self.start.date()),
            end=_to_date(self.end.date()),
            strategies=suggestion.strategies,
            costs=BacktestCosts(),
            monte_carlo_runs=0,
        )

    def start_test(self) -> None:
        context = self.context
        suggestion = self.suggestion
        if context is None or self.running:
            return
        backtest = context.backtest
        if backtest is None:
            self.test_status.setText("The backtest needs the MT5 connection.")
            return
        if suggestion is None or not suggestion.valid:
            self.test_status.setText("Check a valid suggestion first.")
            return
        if not backtest.connected():
            self.test_status.setText("Connect to MT5 first: the backtest reads the history.")
            return
        try:
            request = self.build_request(suggestion)
        except ValueError as error:
            self.test_status.setText(f"Check the dates: {error}")
            return
        current = context.strategies.settings
        proposed = apply_suggestion(current, suggestion)
        self._cancel.clear()
        self._busy = True
        self.progress.setValue(0)
        self.test_status.setText(f"Loading {request.symbol} history...")
        self._thread = threading.Thread(
            target=self._work,
            args=(backtest, request, suggestion, current, proposed),
            name="ai-lab-backtest",
            daemon=True,
        )
        self._thread.start()
        self._update_buttons()

    def cancel_test(self) -> None:
        self._cancel.set()
        self.test_status.setText("Cancelling...")

    def _stage(self, prefix: str) -> Callable[[str, int, int], None]:
        def report(stage: str, done: int, total: int) -> None:
            self.bridge.progress.emit(f"{prefix}: {stage}", done, total)

        return report

    def _work(
        self,
        backtest: BacktestContext,
        request: BacktestRequest,
        suggestion: Suggestion,
        current: StrategySettings,
        proposed: StrategySettings,
    ) -> None:
        log = backtest.log or _quiet
        try:
            history, notes = backtest.load(
                request,
                lambda text: self.bridge.progress.emit(text, 0, 0),
            )
            events: Sequence[CalendarEvent] = ()
            if backtest.events is not None:
                events = backtest.events(request.utc_start - 2 * DAY, request.utc_end + 7 * DAY)
            reports: list[BacktestReport] = []
            for prefix, settings in (("Current", current), ("Suggested", proposed)):
                reports.append(
                    run_report(
                        history,
                        request,
                        settings=settings,
                        risk=backtest.risk.config,
                        execution=backtest.execution.config,
                        events=events,
                        progress=self._stage(prefix),
                        cancelled=self._cancel.is_set,
                        log=log,
                        notes=(*notes, f"AI Lab: {prefix.lower()} settings"),
                    ),
                )
            if any(report.result.cancelled for report in reports):
                self.bridge.failed.emit("cancelled")
                return
            if backtest.runs is not None:
                account = backtest.account() if backtest.account is not None else None
                for report in reports:
                    backtest.runs.save(account, **report.storage_row())
            self.bridge.finished.emit(Comparison(suggestion, reports[0], reports[1]))
        except Exception as error:
            log("ERROR", f"AI Lab backtest failed: {type(error).__name__}: {error}")
            self.bridge.failed.emit(f"{type(error).__name__}: {error}")

    def show_progress(self, stage: str, done: int, total: int) -> None:
        if total > 0:
            self.progress.setValue(int(done * 100 / total))
            self.test_status.setText(f"{stage}: {done} of {total}")
        else:
            self.test_status.setText(stage)

    def show_failure(self, text: str) -> None:
        self._busy = False
        self.test_status.setText(
            "The comparison was cancelled." if text == "cancelled" else f"It failed: {text}",
        )
        self._update_buttons()

    def show_comparison(self, comparison: Comparison) -> None:
        self._busy = False
        self.comparison = comparison
        self.progress.setValue(100)
        current = RunSummary.from_metrics(comparison.current.metrics)
        proposed = RunSummary.from_metrics(comparison.proposed.metrics)
        fill_table(self.results, summary_rows(current, proposed))
        request = comparison.current.request
        self.test_status.setText(f"Done: {request.symbol} {request.period}, both runs saved.")
        self.set_verdict(compare_runs(current, proposed), comparison.suggestion)

    def set_verdict(self, verdict: Verdict, suggestion: Suggestion) -> None:
        """The result of a comparison for this suggestion (tests set it directly)."""
        self.verdict = verdict
        self._tested = suggestion
        self.verdict_label.setText("\n".join(verdict.lines))
        self._update_buttons()

    # 4. Activate --------------------------------------------------------------------------
    def _build_activate(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("4. Turn it on in Paper", "heading"))
        note = (
            "Saves the new parameters for the strategy (the Strategies page shows them), "
            "records a new config version and an audit entry. Only in Paper or Analysis-only "
            "mode: switch back any time by pasting the old values."
        )
        layout.addWidget(styled_label(note, "muted", wrap=True))
        row = QHBoxLayout()
        self.activate_button = QPushButton("Activate in Paper")
        self.activate_button.setObjectName("AiActivate")
        self.activate_button.setProperty("variant", "primary")
        self.activate_button.clicked.connect(self.activate)
        row.addWidget(self.activate_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.activate_status = styled_label("", "muted", wrap=True)
        self.activate_status.setObjectName("AiActivateStatus")
        layout.addWidget(self.activate_status)
        return card

    def activate(self) -> bool:
        context = self.context
        suggestion = self.suggestion
        if context is None or suggestion is None or not suggestion.valid:
            self.activate_status.setText("Check a valid suggestion first.")
            return False
        block = activation_block(context.execution.mode)
        if block:
            self.activate_status.setText(block)
            return False
        verdict = self.verdict
        if verdict is None or self._tested is not suggestion:
            self.activate_status.setText("Run the backtest comparison first.")
            return False
        if not verdict.better and not self.confirm(NOT_BETTER):
            self.activate_status.setText("Not activated.")
            return False
        current = context.strategies.settings
        moved = [
            change.strategy
            for change in suggestion.changes
            if strategy_params(current, change.strategy) != dict(change.old_params)
        ]
        if moved:
            self.activate_status.setText(
                f"The settings of {', '.join(moved)} changed since the check. Check again.",
            )
            return False
        context.strategies.save(apply_suggestion(current, suggestion))
        note = ""
        if context.store is not None:
            account = context.account()
            try:
                for row in config_rows(suggestion, account):
                    context.store.upsert("strategy_configs", row)
                audit = audit_row(suggestion, account, time.time(), verdict)
                context.store.upsert("audit_log", audit)
            except Exception as error:
                note = f" The config history could not be written: {type(error).__name__}."
                context.log("WARNING", f"AI Lab: config rows not written: {error}")
        names = ", ".join(suggestion.strategies)
        context.log("INFO", f"AI suggestion activated for {names} ({context.execution.mode.label})")
        self.activate_status.setText(
            f"Active for {names} in {context.execution.mode.label}. New signals use the new "
            f"values.{note}",
        )
        self._update_buttons()
        return True

    def _ask(self, text: str) -> bool:
        answer = QMessageBox.question(self, "Activate the suggestion", text)
        return answer == QMessageBox.StandardButton.Yes

    def _update_buttons(self) -> None:
        context = self.context
        valid = self.suggestion is not None and self.suggestion.valid
        running = self.running
        can_test = context is not None and context.backtest is not None and valid
        self.test_button.setEnabled(can_test and not running)
        self.cancel_button.setEnabled(running)
        tested = self.verdict is not None and self._tested is self.suggestion
        self.activate_button.setEnabled(context is not None and valid and tested and not running)
