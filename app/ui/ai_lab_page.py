"""The AI Lab page (spec C13, F3.9, docs/AI_LAB_AGENT.md, docs/NOCURVE_V2.md 20e), laid out
like the owner's No Curve v2 design.

At the top the page's place, its title, PAPER ONLY and REAL ORDERS: NEVER, then the strip of
the four steps of the loop with an outside AI; a step turns green only when it is really
done. Below, the chat (760 px, `app.ui.ai_chat`) and the inspector (`app.ui.lab_inspector`).

The manual loop runs inside the chat as cards:

1. Export for AI (the Settings panel, the Files panel or the words "Build the report for the
   AI"): the trades and a report with an analysis prompt for the AI of your choice. Or ask
   your own OpenAI-compatible endpoint from the Settings panel (optional, off by default):
   only a compact summary is sent, never the account number.
2. Paste the AI's JSON answer into the message box: every change is checked against the
   strategy's parameter schema and shown as a difference to the current values.
3. Run comparison: the suggestion against the current settings on the same symbol and
   period, every metric and the three rules of the verdict.
4. Activate in Paper (or Analysis-only) after the four checks: the settings are saved, a new
   config version (created by "ai_suggestion") and an audit row are written.

The chat's agent reads the app with read-only tools and can draw chart cards from the app's
own data (stats, equity, R distribution, Monte Carlo, trades, candles); the quick prompts
draw them directly, and the parameter map and the walk-forward windows run backtests. Every
checked suggestion is an experiment, every request is in the usage ledger. An AI answer is
advice only, and this page never sends real orders.
"""

from __future__ import annotations

import math
import re
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
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ai.agent import Tool, Turn
from app.ai.chat_store import CHAT_FOLDER, ChatStore
from app.ai.experiments import Experiment, ExperimentStore
from app.ai.lab_tools import LabData, lab_tools
from app.ai.prompt_store import PromptStore
from app.ai.transport import host_of
from app.ai.usage_store import UsageRecord, UsageStore
from app.analytics.ai_export import PROMPT, ExportData, rejected_counts, select_trades, write_export
from app.analytics.ai_import import (
    MIN_TRADES,
    Check,
    RunSummary,
    Suggestion,
    Verdict,
    activation_block,
    apply_suggestion,
    audit_row,
    compare_runs,
    comparison_checks,
    config_rows,
    diff_rows,
    parse_suggestion,
    summary_rows,
)
from app.analytics.ai_visuals import (
    FAN_PATHS,
    MIN_FOR_FAN,
    candles,
    markers,
    monte_carlo_fan,
    r_histogram,
    trade_returns,
    trade_stats,
    trade_table,
)
from app.analytics.llm_client import SUMMARY_LIMIT, LlmAnswer, LlmContext
from app.analytics.trades import TradeRecord
from app.backtest.costs import BacktestCosts
from app.backtest.service import (
    BacktestReport,
    BacktestRequest,
    SensitivityOptions,
    WalkForwardOptions,
    run_report,
    strategy_params,
)
from app.calendar.models import CalendarEvent
from app.core.execution_settings import ExecutionSettingsSource
from app.core.strategy_settings import StrategySettings, StrategySettingsSource
from app.domain.signals import SignalRecord
from app.observability.log_reader import read_entries
from app.storage.repositories import Store
from app.storage.signal_store import SignalRepository
from app.strategies.registry import STRATEGIES
from app.ui.ai_chat import ChatPanel, chat_words
from app.ui.analytics_page import AnalyticsContext, start_balance
from app.ui.backtest_page import BacktestContext
from app.ui.lab_cards import (
    ActivateCard,
    ChartCard,
    CompareCard,
    PasteCard,
    VerdictBox,
    ask_card,
    card_words,
    export_card,
    note_card,
    stats_card,
)
from app.ui.lab_charts import BarChart, CandleChart, FanChart, HeatMap, Line, LineChart
from app.ui.lab_inspector import (
    ExperimentsPanel,
    FilesPanel,
    HistoryPanel,
    Inspector,
    PromptsPanel,
    UsagePanel,
    field as panel_field,
    inspector_words,
    panel,
    section,
)
from app.ui.lab_parts import (
    LTR,
    RTL,
    STEP_NAMES,
    GridTable,
    LabCard,
    StepStrip,
    apply_tree,
    lab_button,
    lab_label,
    lab_qss,
)
from app.ui.llm_panel import SETTINGS_TIP, LlmPanel
from app.ui.pages import page_header_for
from app.ui.theme import DEFAULT, ThemeTokens
from app.ui.v2 import Tag

ALL = "All"
DAY = 86_400
EXPORT_FOLDER = "exports"
LAB_FOLDER = "ai_lab"
SIGNALS_FOR_AI = 300
DEFAULT_DAYS = 90
CANDLE_FRAME = "H1"
CANDLE_DAYS = 10
MAX_CHARTS = 3
SUBTITLE = "The loop with an outside AI: export, test its suggestion, turn it on only in Paper"
SUBTITLE_FA = "حلقه‌ی کار با یک AI بیرونی: خروجی بگیرید، پیشنهادش را بسنجید، فقط در Paper فعال کنید"
NOT_BETTER = (
    "The suggestion did not clearly beat your current settings in the backtest.\n\n"
    "Activate it in Paper anyway, to watch it on live prices?"
)
NO_CONTEXT = "The AI Lab needs the local database and the settings."
WRITES = "Writes trades_full.csv, trades_full.json and report.md (the prompt is at the top)."
PAGE_FA: dict[str, str] = {
    WRITES: "trades_full.csv، trades_full.json و report.md را می‌نویسد (پرامپت در بالای آن است).",
    "Export scope (step 1)": "محدوده خروجی (مرحله ۱)",
    "Strategy": "Strategy",
    "Mode": "Mode",
    "Last N days (0 = all time)": "Last N days (0 = all time)",
    "Ask AI (optional)": "Ask AI (اختیاری)",
    "AI connection settings": "تنظیمات اتصال AI",
    "Kept in Windows Credential Manager, never in a file or a log.": (
        "ذخیره در Windows Credential Manager، هرگز در فایل یا لاگ."
    ),
    "Only https, or http for a server on this PC.": "فقط https، یا http برای سروری روی همین PC.",
    "The account number is never sent to the AI.": "شماره‌ی حساب هرگز به AI فرستاده نمی‌شود.",
    "Comparison (step 3)": "مقایسه (مرحله ۳)",
    "Symbol": "Symbol",
    "From (UTC)": "From (UTC)",
    "To (UTC)": "To (UTC)",
    "The verdict is better only with at least 30 trades, a higher expectancy and a drawdown "
    "of at most 1.25x + 1 point.": (
        "حکم «بهتر» فقط با حداقل 30 معامله، امید ریاضی بالاتر و افت سرمایه حداکثر 1.25× + 1 درصد."
    ),
    "The prompt is on the clipboard.": "پرامپت روی کلیپ‌بورد است.",
    "Type a prompt in the message box first.": "اول یک پرامپت در کادر پیام بنویسید.",
    "The prompt was saved.": "پرامپت ذخیره شد.",
    "Ignored: nothing changed.": "نادیده گرفته شد: چیزی تغییر نکرد.",
    "Check a valid suggestion first.": "اول یک پیشنهاد معتبر را بررسی کنید.",
    "Run a comparison first: the equity of the current settings and of the suggestion comes "
    "from its two backtests.": (
        "اول مقایسه را اجرا کنید: منحنی سرمایه‌ی تنظیمات فعلی و پیشنهاد از همان دو بک‌تست می‌آید."
    ),
    "No closed trades in the export scope.": "در محدوده‌ی خروجی معامله‌ی بسته‌ای نیست.",
    "The Monte Carlo needs at least {count} closed trades.": (
        "مونت‌کارلو دست‌کم {count} معامله‌ی بسته می‌خواهد."
    ),
    "Connect to MT5 first: the chart reads the history.": (
        "اول به MT5 وصل شوید: نمودار تاریخچه را می‌خواند."
    ),
    "Wait for the running backtest to finish.": "صبر کنید بک‌تست در حال اجرا تمام شود.",
    "Working...": "در حال کار...",
    "Equity curve": "منحنی سرمایه",
    "Current": "فعلی",
    "Suggested": "پیشنهاد",
    "R distribution": "توزیع نتایج R",
    "Monte Carlo": "مونت‌کارلو",
    "Trades": "معاملات",
    "Stats": "آمار",
    "Candles": "نمودار کندلی",
    "Parameter sensitivity": "نقشه‌ی حساسیت پارامتر",
    "Walk-forward windows": "پنجره‌های walk-forward",
    "No two numeric parameters to map for {strategy}.": (
        "برای {strategy} دو پارامتر عددی برای نقشه نیست."
    ),
    "The chart failed: {error}": "نمودار ساخته نشد: {error}",
    "same period": "همان بازه",
    "Verdict": "حکم",
    "Not tested yet.": "هنوز آزموده نشده.",
}
CHECK_NAMES = {
    "trades": (f"At least {MIN_TRADES} trades", f"حداقل {MIN_TRADES} معامله"),
    "expectancy": ("Higher expectancy (R)", "امید ریاضی (R) بالاتر"),
    "drawdown": ("Drawdown <= 1.25x + 1 point", "افت سرمایه ≤ 1.25× + 1 درصد"),
}
VISUAL_WORDS: dict[str, str] = {
    "EUR/USD candle chart": "candles",
    "Equity curve now and with the AI": "equity",
    "Parameter sensitivity map": "sensitivity",
    "Run the Monte Carlo": "monte_carlo",
    "Distribution of R results": "r_distribution",
    "Trade table": "trades",
    "Recent trades": "trades",
}
EXPORT_WORDS = ("Build the report for the AI", "Export for AI")
COMPARE_WORDS = ("Compare the suggestion", "Run comparison")
CHART_KINDS = (
    "stats",
    "equity",
    "r_distribution",
    "monte_carlo",
    "trades",
    "candles",
    "sensitivity",
    "walk_forward",
)
_TITLES = {
    "stats": "Stats",
    "equity": "Equity curve",
    "r_distribution": "R distribution",
    "monte_carlo": "Monte Carlo",
    "trades": "Trades",
    "candles": "Candles",
    "sensitivity": "Parameter sensitivity",
    "walk_forward": "Walk-forward windows",
}
SYMBOL = re.compile(r"\b([A-Z]{3})/?([A-Z]{3})\b")
TRADE_HEAD = ("CLOSED", "SYMBOL", "STRATEGY", "SIDE", "R", "NET")
TRADE_WIDTHS = (86, 72, 0, 44, 52, 76)


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


def page_words(fa: bool) -> Callable[[str], str]:
    cards = card_words(fa)
    inspector = inspector_words(fa)
    chat = chat_words(fa)

    def word(english: str) -> str:
        if not fa:
            return english
        if english in PAGE_FA:
            return PAGE_FA[english]
        for source in (cards, inspector, chat):
            found = source(english)
            if found != english:
                return found
        return english

    return word


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


@dataclass(frozen=True)
class VisualResult:
    kind: str
    report: BacktestReport | None = None
    bars: Any = None
    digits: int = 5
    symbol: str = ""
    strategy: str = ""


class _Bridge(QObject):
    progress = Signal(str, int, int)
    finished = Signal(object)
    failed = Signal(str)
    visual = Signal(object)
    visual_failed = Signal(str)
    visual_progress = Signal(str, int, int)


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def fold_tip(start: float, end: float, trades: int, expectancy: float | None) -> str:
    """One walk-forward window's out-of-sample result, for the bar's tooltip."""
    first = datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%d")
    last = datetime.fromtimestamp(end, UTC).strftime("%Y-%m-%d")
    value = "n/a" if expectancy is None else f"{expectancy:+.2f} R"
    return f"{first}..{last}: {trades} trades, {value}"


def numeric_params(name: str, current: Mapping[str, Any]) -> list[tuple[str, list[float]]]:
    """The strategy's numeric parameters with three values each (75, 100 and 125 percent of
    the current one, kept inside the schema's limits): the axes of the sensitivity map."""
    schema = STRATEGIES[name].params_model.model_json_schema()
    properties = schema.get("properties")
    found: list[tuple[str, list[float]]] = []
    if not isinstance(properties, Mapping):
        return found
    for key, spec in properties.items():
        value = current.get(key)
        if not isinstance(spec, Mapping) or isinstance(value, bool):
            continue
        kind = spec.get("type")
        if kind not in ("integer", "number") or not isinstance(value, int | float) or not value:
            continue
        low = spec.get("minimum", spec.get("exclusiveMinimum"))
        high = spec.get("maximum", spec.get("exclusiveMaximum"))
        values: list[float] = []
        for share in (0.75, 1.0, 1.25):
            candidate = float(value) * share
            if kind == "integer":
                candidate = float(round(candidate))
            if isinstance(low, int | float):
                candidate = max(candidate, float(low))
            if isinstance(high, int | float):
                candidate = min(candidate, float(high))
            candidate = round(candidate, 6)
            if candidate not in values:
                values.append(candidate)
        if len(values) >= 2:
            found.append((str(key), values))
    return found


class AiLabPage(QWidget):
    def __init__(
        self,
        context: AiLabContext | None,
        parent: QWidget | None = None,
        *,
        persian: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page_ai_lab")
        self.right_to_left_ready = True  # the page follows the window's language
        self.persian = persian
        self.word = page_words(persian)
        self.context = context
        self.tokens: ThemeTokens = DEFAULT
        self.logs_dir: Path | None = None  # None: the main window's log folder
        self.suggestion: Suggestion | None = None
        self.verdict: Verdict | None = None
        self.checks: list[Check] = []
        self.comparison: Comparison | None = None
        self.export_paths: list[Path] = []
        self.asked = False
        self.activated: Suggestion | None = None
        self.experiment: Experiment | None = None
        self.answer_text = ""
        self.confirm: Callable[[str], bool] = self._ask
        self._tested: Suggestion | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._visual_busy = False
        self._visual_card: LabCard | None = None
        self._charts: list[tuple[str, str]] = []
        self._charts_lock = threading.Lock()
        self._scope: tuple[str, str, int] = (ALL, ALL, DEFAULT_DAYS)
        folder = context.export_dir / LAB_FOLDER if context is not None else None
        self.lab_dir = folder
        self.chats = ChatStore(folder / CHAT_FOLDER) if folder is not None else None
        self.prompts = PromptStore(folder) if folder is not None else None
        self.usage = UsageStore(folder) if folder is not None else None
        self.experiments = ExperimentStore(folder) if folder is not None else None
        self.bridge = _Bridge()
        queued = Qt.ConnectionType.QueuedConnection
        self.bridge.progress.connect(self.show_progress, queued)
        self.bridge.finished.connect(self.show_comparison, queued)
        self.bridge.failed.connect(self.show_failure, queued)
        self.bridge.visual.connect(self.show_visual, queued)
        self.bridge.visual_failed.connect(self.visual_failed, queued)
        self.bridge.visual_progress.connect(self.visual_progress, queued)
        if persian:
            self.setLayoutDirection(RTL)
        word = self.word
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 22)
        outer.setSpacing(20)
        self.header = page_header_for("ai_lab")
        self.header.subtitle.setText(SUBTITLE)
        self.paper_tag = Tag("PAPER ONLY", "ink")
        self.never_tag = Tag("REAL ORDERS: NEVER", "neutral")
        self.header.add_action(self.paper_tag)
        self.header.add_action(self.never_tag)
        outer.addWidget(self.header)
        self.steps = StepStrip([word(name) for name in STEP_NAMES])
        outer.addWidget(self.steps)
        self.answer = QPlainTextEdit(self)  # the last pasted or received answer, as text
        self.answer.setObjectName("AiAnswer")
        self.answer.hide()
        self.llm_panel = LlmPanel(self.llm_data, self.take_answer)
        self.llm_panel.bridge.answered.connect(self.show_ask, queued)
        self.chat = ChatPanel(
            self.llm_panel.make_client,
            self.agent_tools,
            self._language,
            fa=persian,
            store=self.chats,
        )
        self.chat.intercept = self.intercept
        self.chat.turn_done.connect(self.after_turn)
        self.chat.chat_changed.connect(self._chat_changed)
        work = QHBoxLayout()
        work.setSpacing(30)
        work.addWidget(self.chat, 1)
        self.inspector = self._build_inspector()
        work.addWidget(self.inspector)
        outer.addLayout(work, 1)
        self._build_cards()
        if context is None:
            for button in (self.export_button, self.check_button, self.copy_button):
                button.setEnabled(False)
            self.export_status.setText(NO_CONTEXT)
        self.inspector.show_panel("settings")
        self._update_buttons()

    # Building ---------------------------------------------------------------------------
    def _build_inspector(self) -> Inspector:
        fa = self.persian
        inspector = Inspector(fa)
        inspector.add_panel("settings", self._build_settings())
        self.history_panel = HistoryPanel(self.chats, fa)
        self.history_panel.open_chat.connect(self.open_chat)
        self.history_panel.new_chat.connect(self.chat.new_chat)
        inspector.add_panel("history", self.history_panel)
        self.prompts_panel = PromptsPanel(self.prompts, fa)
        self.prompts_panel.use_prompt.connect(self.chat.set_text)
        self.prompts_panel.save_current.connect(self.save_prompt)
        inspector.add_panel("prompts", self.prompts_panel)
        self.files_panel = FilesPanel(self.context.export_dir if self.context else None, fa)
        self.files_panel.export.connect(self.export_for_ai)
        self.files_panel.copy_prompt.connect(self.copy_prompt)
        inspector.add_panel("files", self.files_panel)
        self.experiments_panel = ExperimentsPanel(self.experiments, fa)
        inspector.add_panel("experiments", self.experiments_panel)
        self.usage_panel = UsagePanel(self.usage, self._daily_cap, fa)
        inspector.add_panel("usage", self.usage_panel)
        inspector.opened.connect(self.refresh_panel)
        for key in ("history", "prompts", "experiments", "usage"):
            if self.lab_dir is None:
                inspector.buttons[key].setEnabled(False)
        return inspector

    def _build_settings(self) -> QWidget:
        word = self.word
        body, layout = panel(word("Settings"))
        section(layout, word("Export scope (step 1)"))
        self.export_strategy = QComboBox()
        self.export_strategy.setObjectName("AiExportStrategy")
        self.export_strategy.setProperty("lab", "in")
        self.export_strategy.addItems([ALL, *STRATEGIES, "manual"])
        panel_field(layout, word("Strategy"), self.export_strategy)
        self.export_mode = QComboBox()
        self.export_mode.setObjectName("AiExportMode")
        self.export_mode.setProperty("lab", "in")
        self.export_mode.addItems([ALL, "live", "paper"])
        panel_field(layout, word("Mode"), self.export_mode)
        self.export_days = QSpinBox()
        self.export_days.setObjectName("AiExportDays")
        self.export_days.setProperty("lab", "in")
        self.export_days.setRange(0, 3650)
        self.export_days.setValue(DEFAULT_DAYS)
        self.export_days.setSpecialValueText("all time")
        self.export_days.setSuffix(" days")
        panel_field(layout, word("Last N days (0 = all time)"), self.export_days)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.export_button = lab_button(word("Export for AI"), "primary")
        self.export_button.setObjectName("AiExport")
        self.export_button.clicked.connect(self.export_for_ai)
        self.copy_button = lab_button(word("Copy prompt"), "ghost")
        self.copy_button.setObjectName("AiCopyPrompt")
        self.copy_button.clicked.connect(self.copy_prompt)
        row.addWidget(self.export_button, 1)
        row.addWidget(self.copy_button)
        layout.addLayout(row)
        self.export_status = lab_label(word(WRITES), "note", wrap=True)
        self.export_status.setObjectName("AiExportStatus")
        self.export_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.export_status)
        section(layout, word("Ask AI (optional)"), "OFF BY DEFAULT")
        layout.addWidget(self.llm_panel)
        self.settings_button = lab_button("\u2699  " + word("AI connection settings"), "ghost")
        self.settings_button.setObjectName("AiLabSettings")
        self.settings_button.setAccessibleName("AI Lab settings")
        self.settings_button.setToolTip(SETTINGS_TIP)
        self.settings_button.clicked.connect(self.llm_panel.open_settings)
        layout.addWidget(self.settings_button)
        for note in (
            "Kept in Windows Credential Manager, never in a file or a log.",
            "Only https, or http for a server on this PC.",
            "The account number is never sent to the AI.",
        ):
            layout.addWidget(lab_label(word(note), "note", wrap=True))
        section(layout, word("Comparison (step 3)"))
        self.symbol = QComboBox()
        self.symbol.setObjectName("AiTestSymbol")
        self.symbol.setProperty("lab", "in")
        self.symbol.setEditable(True)
        backtest = self.context.backtest if self.context is not None else None
        names = list(backtest.symbols()) if backtest is not None else []
        self.symbol.addItems(names or ["EURUSD"])
        panel_field(layout, word("Symbol"), self.symbol)
        today = datetime.now(UTC).date()
        self.start = QDateEdit(_qdate(today - timedelta(days=181)))
        self.end = QDateEdit(_qdate(today - timedelta(days=1)))
        for edit, name in ((self.start, "From (UTC)"), (self.end, "To (UTC)")):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setProperty("lab", "in")
            edit.setLayoutDirection(LTR)
            panel_field(layout, word(name), edit)
        rule = (
            "The verdict is better only with at least 30 trades, a higher expectancy and a "
            "drawdown of at most 1.25x + 1 point."
        )
        layout.addWidget(lab_label(word(rule), "note", wrap=True))
        layout.addStretch(1)
        for combo in (self.export_strategy, self.export_mode, self.export_days, self.symbol):
            combo.setLayoutDirection(LTR)
        return body

    def _build_cards(self) -> None:
        word = card_words(self.persian)
        self.paste_card = PasteCard(word)
        self.diff: GridTable = self.paste_card.diff
        self.check_status = self.paste_card.status
        self.check_button = self.paste_card.check_button
        self.check_button.clicked.connect(self.check_again)
        self.test_button = self.paste_card.test_button
        self.test_button.clicked.connect(self.start_test)
        self.compare_card = CompareCard(word)
        self.progress = self.compare_card.progress
        self.test_status = self.compare_card.status
        self.cancel_button = self.compare_card.cancel_button
        self.cancel_button.clicked.connect(self.cancel_test)
        self.results: GridTable = self.compare_card.results
        self.verdict_box = VerdictBox(word)
        self.verdict_label = self.verdict_box.label
        self.activate_card = ActivateCard(word)
        self.activate_button = self.activate_card.activate_button
        self.activate_button.clicked.connect(self.activate)
        self.activate_card.ignore_button.clicked.connect(self.ignore)
        self.activate_status = self.activate_card.status
        for card in (self.paste_card, self.compare_card, self.verdict_box, self.activate_card):
            card.setParent(self)
            card.hide()

    def dress_header(self) -> None:
        """The design's subtitle, after the main window has dressed the page headers."""
        self.header.subtitle.setText(SUBTITLE_FA if self.persian else SUBTITLE)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.setStyleSheet(lab_qss(tokens))
        apply_tree(self, tokens)
        for card in (self.paste_card, self.compare_card, self.verdict_box, self.activate_card):
            apply_tree(card, tokens)
            apply = getattr(card, "apply_tokens", None)
            if callable(apply):
                apply(tokens)

    def _language(self) -> str:
        return "fa" if self.persian else ""

    def _daily_cap(self) -> float:
        return self.llm_panel.ai_settings().daily_cost_cap

    def _add(self, card: QWidget) -> None:
        apply_tree(card, self.tokens)
        apply = getattr(card, "apply_tokens", None)
        if callable(apply):
            apply(self.tokens)
        self.chat.add_extra(card)

    def _say(self, text: str) -> None:
        self.chat.status.setText(text)

    # The inspector ----------------------------------------------------------------------
    def refresh_panel(self, key: str) -> None:
        if key == "history":
            self.history_panel.refresh()
        elif key == "prompts":
            self.prompts_panel.refresh()
        elif key == "files":
            self.files_panel.refresh()
        elif key == "experiments":
            self.experiments_panel.refresh()
        elif key == "usage":
            self.usage_panel.refresh()

    def _refresh_open(self, *keys: str) -> None:
        if self.inspector.current in keys:
            self.refresh_panel(self.inspector.current)

    def open_chat(self, chat_id: str) -> bool:
        return self.chat.open_chat(chat_id)

    def _chat_changed(self, chat_id: str) -> None:
        self.history_panel.set_current(chat_id)
        self._refresh_open("history")

    def save_prompt(self) -> bool:
        text = self.chat.input.toPlainText().strip()
        if self.prompts is None or not text:
            self._say(self.word("Type a prompt in the message box first."))
            return False
        try:
            saved = self.prompts.save(text, time.time())
        except OSError as error:
            self._say(f"The prompt was not saved: {error}")
            return False
        self.prompts_panel.refresh()
        self._say(self.word("The prompt was saved."))
        return saved is not None

    def record_usage(self, record: UsageRecord) -> None:
        if self.usage is None:
            return
        try:
            self.usage.add(record)
        except OSError as error:
            if self.context is not None:
                self.context.log("WARNING", f"AI Lab: the usage was not written: {error}")
        self._refresh_open("usage")

    # The chat ---------------------------------------------------------------------------
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
        """The chat's read-only tools over this page's data (none without a context), and
        `chart`: a chart card under the answer, drawn from the app's own data."""
        context = self.context
        if context is None:
            return []
        strategies = context.strategies
        execution = context.execution
        records = self.saved_signals()
        self._scope = self.export_scope()  # read here: the tools run in the worker thread
        tools = lab_tools(
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
        chart = Tool(
            name="chart",
            description=(
                "Draw a chart card under your answer from the app's own data and get the "
                "numbers it shows. kind: stats, equity (current vs suggested, from the last "
                "comparison), r_distribution, monte_carlo, trades, candles (with symbol)."
            ),
            args="kind (str), symbol (str, for candles)",
            run=self._chart_tool,
            title="Draw a chart",
        )
        return [*tools, chart]

    def _chart_tool(self, args: Mapping[str, Any]) -> str:
        """Runs in the agent's thread: only reads, and asks for the card after the turn."""
        kind = str(args.get("kind") or "").strip().lower()
        symbol = str(args.get("symbol") or "").strip().upper()
        if kind not in CHART_KINDS[:6]:
            known = ", ".join(CHART_KINDS[:6])
            return f"Unknown chart kind {kind or '(none)'}; use one of {known}."
        with self._charts_lock:
            if len(self._charts) >= MAX_CHARTS:
                return f"At most {MAX_CHARTS} charts per answer."
            self._charts.append((kind, symbol))
        return self.visual_summary(kind, symbol, self._scope)

    def after_turn(self, turn: object) -> None:
        if not isinstance(turn, Turn):
            return
        cost = turn.cost if turn.cost > 0 else math.nan
        usage = turn.usage
        self.record_usage(
            UsageRecord(
                time.time(),
                "chat",
                turn.model,
                usage.input_tokens,
                usage.cached_tokens,
                usage.output_tokens,
                max(turn.calls, 1),
                cost,
            ),
        )
        with self._charts_lock:
            wanted = list(self._charts)
            self._charts.clear()
        for kind, symbol in wanted:
            self.visual(kind, symbol)

    def intercept(self, text: str) -> bool:
        """The page's own words in the composer: a pasted answer, an export, a comparison or
        a chart run here, without an AI call."""
        clean = text.strip()
        key = clean.rstrip(".!?\u061f").strip()
        chat = chat_words(self.persian)
        if '"changes"' in clean or ('"strategy"' in clean and '"params"' in clean):
            self.chat.add_bubble(clean)
            self.check_suggestion(clean)
            return True
        if key in EXPORT_WORDS or key in {chat(item) for item in EXPORT_WORDS}:
            self.chat.add_bubble(clean)
            self.export_for_ai()
            return True
        if key in COMPARE_WORDS or key in {chat(item) for item in COMPARE_WORDS}:
            self.chat.add_bubble(clean)
            if self.suggestion is None or not self.suggestion.valid:
                text = self.word("Check a valid suggestion first.")
                self._add(note_card("compare", self.word("Run comparison"), text))
            else:
                self.start_test()
            return True
        for english, kind in VISUAL_WORDS.items():
            if key in (english, chat(english)):
                self.chat.add_bubble(clean)
                symbol = ""
                if kind == "candles":
                    found = SYMBOL.search(english)
                    symbol = "".join(found.groups()) if found else ""
                self.visual(kind, symbol)
                return True
        lowered = key.lower()
        if "walk-forward" in lowered or "walk forward" in lowered:
            self.chat.add_bubble(clean)
            self.visual("walk_forward")
            return True
        if "candle" in lowered or "\u06a9\u0646\u062f\u0644" in key:
            found = SYMBOL.search(clean.upper())
            self.chat.add_bubble(clean)
            self.visual("candles", "".join(found.groups()) if found else "")
            return True
        return False

    # 1. Export ----------------------------------------------------------------------------
    def export_data(self, context: AiLabContext, now: float) -> ExportData:
        """The trades and settings chosen in step 1 (the export and the summary use them)."""
        strategy = self.export_strategy.currentText()
        mode = self.export_mode.currentText()
        days = self.export_days.value()
        trades = self.scoped_trades(context, now)
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

    def export_scope(self) -> tuple[str, str, int]:
        """Strategy, mode and days of step 1 (read in the UI thread)."""
        strategy = self.export_strategy.currentText()
        return strategy, self.export_mode.currentText(), self.export_days.value()

    def scoped_trades(
        self,
        context: AiLabContext,
        now: float,
        scope: tuple[str, str, int] | None = None,
    ) -> list[TradeRecord]:
        """The closed trades of the export scope (strategy, mode, last N days)."""
        strategy, mode, days = scope or self.export_scope()
        return select_trades(
            list(context.trades()),
            now,
            strategy="" if strategy == ALL else strategy,
            mode="" if mode == ALL else mode,
            days=days,
        )

    def scope_note(self) -> str:
        strategy = self.export_strategy.currentText().lower()
        mode = self.export_mode.currentText().lower()
        days = self.export_days.value()
        return f"strategy: {strategy} \u00b7 {mode} \u00b7 {f'{days}d' if days else 'all time'}"

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
            self._say(self.export_status.text())
            return []
        self.export_paths = paths
        self.export_status.setText(
            f"Saved {len(data.trades)} trades in {folder}. Give the AI all three files, or "
            "paste report.md and attach the CSV.",
        )
        context.log("INFO", f"AI export: {len(data.trades)} trades to {folder}")
        self._add(export_card(paths, folder.name, self.scope_note(), card_words(self.persian)))
        self.files_panel.refresh()
        self._update_buttons()
        return paths

    def copy_prompt(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(PROMPT)
        self.export_status.setText(self.word("The prompt is on the clipboard."))
        self._say(self.word("The prompt is on the clipboard."))

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
        found = self.check_suggestion(text)
        if found is not None and found.valid:
            return "Step 2 shows its valid suggestion; backtest it next."
        if found is not None and found.changes:
            return "Step 2 shows its suggestion, but it has problems."
        return "It suggests no valid change; read it in step 2."

    def show_ask(self, answer: object) -> None:
        """The Ask AI card of a request, over the checked answer it brought."""
        if not isinstance(answer, LlmAnswer):
            return
        llm = self.llm_panel.llm
        base = llm.source.settings.base_url if llm is not None else ""
        usage = answer.usage
        card = ask_card(
            model=answer.model,
            host=host_of(base) if base else "",
            sent_chars=self.llm_panel.sent_chars,
            cap=SUMMARY_LIMIT,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost=usage.cost,
            seconds=answer.seconds,
            word=card_words(self.persian),
        )
        self.asked = True
        self._add(card)
        if self.chat.shown(self.paste_card):
            self.chat.add_card(self.paste_card)  # the answer's check under its request
        self.record_usage(
            UsageRecord(
                time.time(),
                "ask",
                answer.model,
                usage.prompt_tokens,
                0,
                usage.completion_tokens,
                1,
                usage.cost,
            ),
        )
        self._update_buttons()

    # 2. Import ----------------------------------------------------------------------------
    def check_again(self) -> Suggestion | None:
        return self.check_suggestion(self.answer.toPlainText())

    def check_suggestion(self, text: str | None = None) -> Suggestion | None:
        context = self.context
        if context is None:
            return None
        if text is not None:
            self.answer.setPlainText(text)
        self.answer_text = self.answer.toPlainText()
        found = parse_suggestion(self.answer_text, context.strategies.settings)
        self.suggestion = found
        self.verdict = None
        self.checks = []
        self.comparison = None
        self._tested = None
        rows = diff_rows(found)
        self.paste_card.show_result(rows, len(found.changes), found.valid)
        self.results.set_rows([])
        self.compare_card.checks.clear()
        self.compare_card.checks_frame.hide()
        self.verdict_box.set_verdict(None, self.word("Not tested yet."))
        lines: list[str] = []
        for change in found.changes:
            if self.persian:
                text_line = f"{change.strategy}: دلیل AI: «{change.reason}»."
                if change.expected_impact:
                    text_line += f" اثر مورد انتظار: «{change.expected_impact}»"
            else:
                text_line = f"{change.strategy}: {change.reason}"
                if change.expected_impact:
                    text_line += f" Expected: {change.expected_impact}"
            lines.append(text_line)
        lines += [f"Problem: {problem}" for problem in found.problems]
        if found.valid:
            lines.append("Valid. Next: run the backtest comparison.")
        elif found.changes:
            lines.append("Fix the problems (or remove those changes) before testing.")
        self.check_status.setText("\n".join(lines))
        for card in (self.compare_card, self.verdict_box, self.activate_card):
            self.chat.remove_card(card)
        self.chat.add_card(self.paste_card)
        self.experiment = None
        if found.valid and self.experiments is not None:
            changes = [(a, b, c, d) for a, b, c, d in rows]
            reasons = " ".join(change.reason for change in found.changes)
            try:
                self.experiment = self.experiments.start(changes, reasons, time.time())
            except OSError as error:
                context.log("WARNING", f"AI Lab: the experiment was not written: {error}")
        self._refresh_open("experiments")
        self._update_buttons()
        return found

    # 3. Test ------------------------------------------------------------------------------
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

    def _test_note(self, text: str) -> None:
        self.test_status.setText(text)
        self.chat.add_card(self.compare_card)

    def start_test(self) -> None:
        context = self.context
        suggestion = self.suggestion
        if context is None or self.running:
            return
        backtest = context.backtest
        if backtest is None:
            self._test_note("The backtest needs the MT5 connection.")
            return
        if suggestion is None or not suggestion.valid:
            self._test_note("Check a valid suggestion first.")
            return
        if self._visual_busy:
            self._test_note(self.word("Wait for the running backtest to finish."))
            return
        if not backtest.connected():
            self._test_note("Connect to MT5 first: the backtest reads the history.")
            return
        try:
            request = self.build_request(suggestion)
        except ValueError as error:
            self._test_note(f"Check the dates: {error}")
            return
        current = context.strategies.settings
        proposed = apply_suggestion(current, suggestion)
        self._cancel.clear()
        self._busy = True
        self.progress.setValue(0)
        self.compare_card.set_scope(request.symbol, request.period)
        self.results.set_rows([])
        self.compare_card.checks_frame.hide()
        self._test_note(f"Loading {request.symbol} history...")
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
        self.results.set_rows(summary_rows(current, proposed))
        request = comparison.current.request
        self.test_status.setText(f"Done: {request.symbol} {request.period}, both runs saved.")
        self.checks = comparison_checks(current, proposed)
        verdict = compare_runs(current, proposed)
        if self.experiment is not None and self.experiments is not None:
            try:
                self.experiment = self.experiments.record_result(
                    self.experiment.number,
                    better=verdict.better,
                    symbol=request.symbol,
                    period=request.period,
                    current=current.numbers(),
                    proposed=proposed.numbers(),
                    current_curve=[float(value) for value in comparison.current.result.equity],
                    proposed_curve=[float(value) for value in comparison.proposed.result.equity],
                )
            except OSError as error:
                if self.context is not None:
                    self.context.log("WARNING", f"AI Lab: the experiment was not written: {error}")
            self._refresh_open("experiments")
        self.set_verdict(verdict, comparison.suggestion)

    def _show_checks(self) -> None:
        checks = self.compare_card.checks
        checks.clear()
        for check in self.checks:
            names = CHECK_NAMES.get(check.key, (check.key, check.key))
            checks.add(check.ok, names[1] if self.persian else names[0], check.value)
        self.compare_card.checks_frame.setVisible(bool(self.checks))

    def verdict_text(self, verdict: Verdict) -> str:
        if not self.persian:
            return "\n".join(verdict.lines)
        if verdict.better:
            parts = ["پیشنهاد در این بک‌تست از تنظیمات فعلی بهتر بود."]
        else:
            parts = ["پیشنهاد به‌وضوح بهتر از تنظیمات فعلی نبود."]
        for check in self.checks:
            if check.ok:
                continue
            if check.key == "trades":
                parts.append(f"فقط {check.value} معامله: برای قضاوت کم است (حداقل {MIN_TRADES}).")
            elif check.key == "expectancy":
                parts.append(
                    f"امید ریاضی بالاتر نشد ({check.value})، پس پیشنهاد «بهتر» حساب نمی‌شود.",
                )
            else:
                parts.append(f"افت سرمایه خیلی عمیق‌تر شد ({check.value}).")
        parts.append(
            "یک بازه اثبات نیست، این حکم هم همین را می‌گوید. برای اطمینان بیشتر، walk-forward را "
            "در صفحه‌ی Backtest اجرا کنید.",
        )
        return " ".join(parts)

    def set_verdict(self, verdict: Verdict, suggestion: Suggestion) -> None:
        """The result of a comparison for this suggestion (tests set it directly)."""
        self.verdict = verdict
        self._tested = suggestion
        self._show_checks()
        self.verdict_box.set_verdict(verdict.better, self.verdict_text(verdict))
        if self.comparison is not None:
            self.chat.add_card(self.compare_card)
        self.chat.add_card(self.verdict_box)
        self.chat.add_card(self.activate_card)
        self._update_buttons()

    # 4. Activate --------------------------------------------------------------------------
    def _moved(self, suggestion: Suggestion) -> list[str]:
        context = self.context
        if context is None:
            return []
        current = context.strategies.settings
        return [
            change.strategy
            for change in suggestion.changes
            if strategy_params(current, change.strategy) != dict(change.old_params)
        ]

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
        moved = self._moved(suggestion)
        if moved:
            self.activate_status.setText(
                f"The settings of {', '.join(moved)} changed since the check. Check again.",
            )
            self._update_buttons()
            return False
        current = context.strategies.settings
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
        self.activated = suggestion
        if self.experiment is not None and self.experiments is not None:
            try:
                self.experiments.update(self.experiment.number, activated=time.time())
            except OSError as error:
                context.log("WARNING", f"AI Lab: the experiment was not written: {error}")
            self._refresh_open("experiments")
        self._update_buttons()
        return True

    def ignore(self) -> None:
        """Ignore the suggestion: nothing changes; its experiment says so."""
        if self.experiment is not None and self.experiments is not None and self.activated is None:
            try:
                self.experiments.update(self.experiment.number, ignored=True)
            except OSError:
                pass
            self._refresh_open("experiments")
        self.chat.remove_card(self.activate_card)
        self._say(self.word("Ignored: nothing changed."))

    def _ask(self, text: str) -> bool:
        answer = QMessageBox.question(self, "Activate the suggestion", text)
        return answer == QMessageBox.StandardButton.Yes

    def _activation_checks(self) -> None:
        context = self.context
        checks = self.activate_card.checks
        checks.clear()
        suggestion = self.suggestion
        if context is None or suggestion is None:
            return
        fa = self.persian
        mode = context.execution.mode
        real = mode.places_real_orders
        tone = "warning" if real else "neutral"
        self.activate_card.mode_tag.set(f"{mode.label.upper()} MODE", tone)
        if fa:
            checks.add(not real, f"حالت فعلی {mode.label} است (در Semi-auto یا Auto رد می‌شود)")
        else:
            checks.add(not real, f"The mode is {mode.label} (refused in Semi-auto or Auto)")
        tested = self.verdict is not None and self._tested is suggestion
        checks.add(
            tested,
            "مقایسه دقیقاً برای همین پیشنهاد انجام شده"
            if fa
            else "The comparison was run for exactly this suggestion",
        )
        unchanged = not self._moved(suggestion)
        checks.add(
            unchanged,
            "پارامترها از زمان بررسی تغییر نکرده‌اند"
            if fa
            else "The parameters have not changed since the check",
        )
        better = self.verdict is not None and self.verdict.better
        if better:
            checks.add(True, "حکم «بهتر» است" if fa else "The verdict is better")
        else:
            checks.add(
                False,
                "حکم «بهتر نیست» تأیید دستی می‌خواهد"
                if fa
                else 'A "not better" verdict needs your confirmation',
            )

    def _update_buttons(self) -> None:
        context = self.context
        valid = self.suggestion is not None and self.suggestion.valid
        running = self.running
        can_test = context is not None and context.backtest is not None and valid
        self.test_button.setEnabled(can_test and not running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setVisible(running)
        self.check_button.setEnabled(context is not None)
        tested = self.verdict is not None and self._tested is self.suggestion
        self.activate_button.setEnabled(context is not None and valid and tested and not running)
        active = self.activated is not None and self.activated is self.suggestion
        self.steps.set_done(
            (
                bool(self.export_paths) or self.asked,
                valid,
                tested,
                active,
            ),
        )
        self._activation_checks()

    # Charts -----------------------------------------------------------------------------
    def visual_summary(
        self,
        kind: str,
        symbol: str = "",
        scope: tuple[str, str, int] | None = None,
    ) -> str:
        """The numbers a chart shows, as text for the agent (read only, any thread)."""
        context = self.context
        if context is None:
            return "No data: the AI Lab has no context."
        now = time.time()
        if kind == "equity":
            comparison = self.comparison
            if comparison is None:
                return "No comparison yet: the equity chart needs one (Run comparison)."
            current = RunSummary.from_metrics(comparison.current.metrics)
            proposed = RunSummary.from_metrics(comparison.proposed.metrics)
            rows = summary_rows(current, proposed)
            return "\n".join(f"{name}: current {a}, suggested {b}" for name, a, b in rows)
        if kind == "candles":
            name = symbol or "the symbol"
            return f"The card shows {CANDLE_DAYS} days of {name} {CANDLE_FRAME} bars and entries."
        trades = self.scoped_trades(context, now, scope)
        balance = start_balance(context.balance(), trades)
        if kind == "stats":
            stats = trade_stats(trades, balance)
            net = "n/a" if stats.net_percent is None else f"{stats.net_percent:+.2f}%"
            r = "n/a" if stats.expectancy_r is None else f"{stats.expectancy_r:+.3f} R"
            return (
                f"{stats.trades} trades, net {stats.net:+,.2f} ({net}), max drawdown "
                f"{stats.max_drawdown_percent:.2f}%, expectancy {r} ({stats.with_r} with R)"
            )
        if kind == "r_distribution":
            found = r_histogram([trade.r_multiple for trade in trades])
            pairs = ", ".join(
                f"[{found.edges[i]:g},{found.edges[i + 1]:g}): {count}"
                for i, count in enumerate(found.counts)
                if count
            )
            return f"{found.total} trades with R; {found.losing} below 0 R; bins {pairs or 'none'}"
        if kind == "monte_carlo":
            fan = monte_carlo_fan(trade_returns(trades, balance))
            if fan.empty:
                return f"Too few trades for a Monte Carlo ({len(trades)}, needs {MIN_FOR_FAN})."
            return (
                f"{FAN_PATHS} random orders of {fan.trades} trades: final {fan.real[-1]:+.2f}%, "
                f"median deepest drawdown {fan.drawdown_median:.2f}%, "
                f"worst {fan.drawdown_worst:.2f}%"
            )
        rows = trade_table(trades, 10)
        return "\n".join(" | ".join(row) for row in rows) or "No closed trades."

    def visual(self, kind: str, symbol: str = "") -> None:
        """A chart card from the app's own data (or a card that says why there is none)."""
        word = self.word
        context = self.context
        if context is None:
            self._add(note_card("bars", word("Stats"), NO_CONTEXT))
            return
        now = time.time()
        if kind in ("candles", "sensitivity", "walk_forward"):
            self._start_visual(kind, symbol)
            return
        if kind == "equity":
            self._equity_card()
            return
        trades = self.scoped_trades(context, now)
        balance = start_balance(context.balance(), trades)
        note = self.scope_note()
        if not trades:
            text = word("No closed trades in the export scope.")
            self._add(note_card("bars", word(_TITLES.get(kind, "Stats")), text))
            return
        if kind == "stats":
            self._add(self._stats_card(trades, balance, note))
        elif kind == "r_distribution":
            self._add(self._r_card(trades, note))
        elif kind == "monte_carlo":
            self._add(self._fan_card(trades, balance, note))
        else:
            self._add(self._trades_card(trades, note))

    def _stats_card(self, trades: Sequence[TradeRecord], balance: float, note: str) -> LabCard:
        stats = trade_stats(trades, balance)
        fa = self.persian
        net = "n/a" if stats.net_percent is None else f"{stats.net_percent:+.1f}%"
        expectancy = "n/a" if stats.expectancy_r is None else f"{stats.expectancy_r:+.2f}R"
        enough = stats.trades >= MIN_TRADES
        cells = [
            ("NET", net, "profit" if stats.net >= 0 else "loss", f"{stats.net:+,.2f}"),
            ("MAX DD", f"\u2212{stats.max_drawdown_percent:.1f}%", "loss", ""),
            (
                "TRADES",
                str(stats.trades),
                "",
                ("کافی برای تحلیل" if enough else "کم برای تحلیل")
                if fa
                else ("enough to judge" if enough else "too few to judge"),
            ),
            ("EXPECTANCY", expectancy, "", "هر معامله" if fa else "per trade"),
        ]
        return stats_card(cells, self.word("Stats"), note)

    def _r_card(self, trades: Sequence[TradeRecord], note: str) -> LabCard:
        found = r_histogram([trade.r_multiple for trade in trades])
        starts = found.edges[:-1]
        labels = [f"{edge:g}" if index % 2 == 0 else "" for index, edge in enumerate(starts)]
        tips = [
            f"[{found.edges[i]:g}, {found.edges[i + 1]:g}) R: {count}"
            for i, count in enumerate(found.counts)
        ]

        def build() -> QWidget:
            chart = BarChart(160, self.word("R distribution"))
            counts = [float(count) for count in found.counts]
            chart.set_bars(counts, labels, signed=False, tips=tips)
            return chart

        mean = "n/a" if found.mean is None else f"{found.mean:+.2f} R"
        median = "n/a" if found.median is None else f"{found.median:+.2f} R"
        footer = (
            f"N={found.total} \u00b7 mean {mean} \u00b7 median {median} \u00b7 "
            f"{found.losing} below 0 R"
        )
        data = {"edges": list(found.edges), "counts": list(found.counts)}
        return ChartCard(
            "bars",
            self.word("R distribution"),
            build(),
            word=card_words(self.persian),
            tags=[f"N={found.total}"],
            note=note,
            data=data,
            enlarge=build,
            footer=footer,
        )

    def _fan_card(self, trades: Sequence[TradeRecord], balance: float, note: str) -> LabCard:
        fan = monte_carlo_fan(trade_returns(trades, balance))
        if fan.empty:
            text = self.word("The Monte Carlo needs at least {count} closed trades.")
            return note_card("bars", self.word("Monte Carlo"), text.format(count=MIN_FOR_FAN))

        def build() -> QWidget:
            chart = FanChart(200, self.word("Monte Carlo"))
            chart.set_fan(fan.paths, fan.low, fan.middle, fan.high, fan.real)
            return chart

        footer = (
            f"{FAN_PATHS} random orders of {fan.trades} trades (each once): the final result is "
            f"the same ({fan.real[-1]:+.2f}%), the path is not. Deepest drawdown: median "
            f"{fan.drawdown_median:.1f}%, worst {fan.drawdown_worst:.1f}%."
        )
        data = {
            "real": list(fan.real),
            "p5": list(fan.low),
            "p50": list(fan.middle),
            "p95": list(fan.high),
        }
        return ChartCard(
            "bars",
            self.word("Monte Carlo"),
            build(),
            word=card_words(self.persian),
            tags=[f"{FAN_PATHS} paths", f"N={fan.trades}"],
            note=note,
            data=data,
            enlarge=build,
            footer=footer,
        )

    def _trades_card(self, trades: Sequence[TradeRecord], note: str) -> LabCard:
        rows = trade_table(trades)
        table = GridTable(TRADE_HEAD, TRADE_WIDTHS)
        table.setObjectName("AiTradeTable")
        table.set_rows(rows)
        card = ChartCard(
            "file",
            self.word("Trades"),
            table,
            word=card_words(self.persian),
            tags=[f"{len(rows)} / {len(trades)}"],
            note=note,
            data=[dict(zip(TRADE_HEAD, row, strict=True)) for row in rows],
        )
        return card

    def _equity_card(self) -> None:
        comparison = self.comparison
        word = self.word
        if comparison is None:
            text = word(
                "Run a comparison first: the equity of the current settings and of the "
                "suggestion comes from its two backtests.",
            )
            self._add(note_card("compare", word("Equity curve"), text))
            return
        current = [float(value) for value in comparison.current.result.equity]
        proposed = [float(value) for value in comparison.proposed.result.equity]
        request = comparison.current.request

        def build() -> QWidget:
            chart = LineChart(200, word("Equity curve"))
            chart.set_lines(
                [
                    Line(tuple(current), word("Current"), "ink"),
                    Line(tuple(proposed), word("Suggested"), "muted", dashed=True),
                ],
            )
            return chart

        footer = (
            f"{word('Current')}: {current[-1]:,.2f} \u00b7 {word('Suggested')}: {proposed[-1]:,.2f}"
            if current and proposed
            else ""
        )
        self._add(
            ChartCard(
                "compare",
                word("Equity curve"),
                build(),
                word=card_words(self.persian),
                tags=[request.symbol, request.period, word("same period")],
                data={"current": current[::10], "suggested": proposed[::10]},
                enlarge=build,
                footer=footer,
            ),
        )

    # Charts that run backtests or read MT5 bars -------------------------------------------
    def _start_visual(self, kind: str, symbol: str) -> None:
        context = self.context
        word = self.word
        title = word(_TITLES[kind])
        backtest = context.backtest if context is not None else None
        if context is None or backtest is None or not backtest.connected():
            text = word("Connect to MT5 first: the chart reads the history.")
            self._add(note_card("bars", title, text))
            return
        if self._busy or self._visual_busy:
            self._add(note_card("bars", title, word("Wait for the running backtest to finish.")))
            return
        name = self._visual_strategy()
        try:
            request = self._visual_request(kind, symbol, name)
        except ValueError as error:
            text = word("The chart failed: {error}").format(error=error)
            self._add(note_card("bars", title, text))
            return
        if request is None:
            text = word("No two numeric parameters to map for {strategy}.").format(strategy=name)
            self._add(note_card("bars", title, text))
            return
        card = note_card("bars", title, word("Working..."))
        self._visual_card = card
        self._add(card)
        self._visual_busy = True
        self._cancel.clear()
        settings = context.strategies.settings
        threading.Thread(
            target=self._visual_work,
            args=(kind, backtest, request, settings, name),
            name="ai-lab-visual",
            daemon=True,
        ).start()
        self._update_buttons()

    def _visual_strategy(self) -> str:
        """The strategy of a map or walk-forward: the suggestion's, the export scope's, or
        the first one that is on."""
        if self.suggestion is not None and self.suggestion.valid:
            return self.suggestion.strategies[0]
        chosen = self.export_strategy.currentText()
        if chosen in STRATEGIES:
            return chosen
        context = self.context
        if context is not None:
            for name in STRATEGIES:
                if context.strategies.settings.entry(name).enabled:
                    return name
        return next(iter(STRATEGIES))

    def _visual_request(self, kind: str, symbol: str, name: str) -> BacktestRequest | None:
        context = self.context
        if kind == "candles":
            today = datetime.now(UTC).date()
            return BacktestRequest(
                symbol=symbol or self.symbol.currentText().strip() or "EURUSD",
                start=today - timedelta(days=CANDLE_DAYS),
                end=today,
                strategies=[name],
                monte_carlo_runs=0,
            )
        base = self.build_request_for(name)
        if kind == "walk_forward":
            options = WalkForwardOptions(
                strategy=name,
                in_days=60,
                out_days=30,
                minimum_in_trades=1,
            )
            return base.model_copy(update={"walk_forward": options})
        if context is None:
            return None
        axes = numeric_params(name, strategy_params(context.strategies.settings, name))
        if len(axes) < 2:
            return None
        (x_name, x_values), (y_name, y_values) = axes[0], axes[1]
        options_map = SensitivityOptions(
            strategy=name,
            x_name=x_name,
            x_values=x_values,
            y_name=y_name,
            y_values=y_values,
            minimum_trades=10,
        )
        return base.model_copy(update={"sensitivity": options_map})

    def build_request_for(self, name: str) -> BacktestRequest:
        return BacktestRequest(
            symbol=self.symbol.currentText().strip() or "EURUSD",
            start=_to_date(self.start.date()),
            end=_to_date(self.end.date()),
            strategies=[name],
            costs=BacktestCosts(),
            monte_carlo_runs=0,
        )

    def _visual_work(
        self,
        kind: str,
        backtest: BacktestContext,
        request: BacktestRequest,
        settings: StrategySettings,
        name: str,
    ) -> None:
        log = backtest.log or _quiet
        try:
            history, notes = backtest.load(
                request,
                lambda text: self.bridge.visual_progress.emit(text, 0, 0),
            )
            if kind == "candles":
                bars = history.bars.get(CANDLE_FRAME)
                digits = history.spec.digits
                found = VisualResult(kind, bars=bars, digits=digits, symbol=request.symbol)
                self.bridge.visual.emit(found)
                return
            events: Sequence[CalendarEvent] = ()
            if backtest.events is not None:
                events = backtest.events(request.utc_start - 2 * DAY, request.utc_end + 7 * DAY)

            def progress(stage: str, done: int, total: int) -> None:
                self.bridge.visual_progress.emit(stage, done, total)

            report = run_report(
                history,
                request,
                settings=settings,
                risk=backtest.risk.config,
                execution=backtest.execution.config,
                events=events,
                progress=progress,
                cancelled=self._cancel.is_set,
                log=log,
                notes=(*notes, f"AI Lab: {kind}"),
            )
            done = VisualResult(kind, report=report, symbol=request.symbol, strategy=name)
            self.bridge.visual.emit(done)
        except Exception as error:
            log("ERROR", f"AI Lab chart failed: {type(error).__name__}: {error}")
            self.bridge.visual_failed.emit(f"{type(error).__name__}: {error}")

    def visual_progress(self, stage: str, done: int, total: int) -> None:
        card = self._visual_card
        if card is not None and card.footer_label is not None:
            card.footer_label.setText(f"{stage}: {done} of {total}" if total > 0 else stage)

    def visual_failed(self, text: str) -> None:
        self._visual_busy = False
        card = self._visual_card
        self._visual_card = None
        if card is not None and card.footer_label is not None:
            card.footer_label.setText(self.word("The chart failed: {error}").format(error=text))
        self._update_buttons()

    def show_visual(self, result: object) -> None:
        self._visual_busy = False
        card = self._visual_card
        self._visual_card = None
        if card is not None:
            card.hide()
            self.chat.extras = [item for item in self.chat.extras if item is not card]
            self.chat.messages.removeWidget(card)
            card.deleteLater()
        self._update_buttons()
        if not isinstance(result, VisualResult):
            return
        if result.kind == "candles":
            self._candle_card(result)
        elif result.kind == "sensitivity" and result.report is not None:
            self._heat_card(result.report, result.strategy)
        elif result.kind == "walk_forward" and result.report is not None:
            self._walk_card(result.report, result.strategy)

    def _candle_card(self, result: VisualResult) -> None:
        word = self.word
        bars = result.bars
        context = self.context
        if bars is None or not len(bars) or context is None:
            self._add(note_card("bars", word("Candles"), word("No data")))
            return
        shown = candles(
            bars.time.tolist(),
            bars.open.tolist(),
            bars.high.tolist(),
            bars.low.tolist(),
            bars.close.tolist(),
        )
        marks = markers(shown, list(context.trades()), result.symbol, float(bars.seconds))
        digits = result.digits

        def build() -> QWidget:
            chart = CandleChart(220, word("Candles"))
            chart.set_candles(shown, marks, digits)
            return chart

        data = [
            {
                "time": bar.time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }
            for bar in shown
        ]
        footer = (
            f"{len(shown)} {CANDLE_FRAME} bars \u00b7 {len(marks)} entries "
            "(\u25b2 buy, \u25bc sell)"
        )
        self._add(
            ChartCard(
                "bars",
                word("Candles"),
                build(),
                word=card_words(self.persian),
                tags=[result.symbol, CANDLE_FRAME],
                data=data,
                enlarge=build,
                footer=footer,
            ),
        )

    def _heat_card(self, report: BacktestReport, name: str) -> None:
        word = self.word
        grid = report.sensitivity
        if grid is None:
            self._add(note_card("bars", word("Parameter sensitivity"), word("No data")))
            return
        x_labels = [f"{value:g}" for value in grid.x_values]
        y_labels = [f"{value:g}" for value in grid.y_values]

        def build() -> QWidget:
            chart = HeatMap(220, word("Parameter sensitivity"))
            chart.set_grid(grid.values, x_labels, y_labels, grid.x_name, grid.y_name, grid.best)
            return chart

        self._add(
            ChartCard(
                "bars",
                word("Parameter sensitivity"),
                build(),
                word=card_words(self.persian),
                tags=[name, f"{grid.x_name} \u00d7 {grid.y_name}", grid.metric],
                note=f"{report.request.symbol} {report.request.period}",
                data=grid.to_json(),
                enlarge=build,
                footer=grid.verdict(),
            ),
        )

    def _walk_card(self, report: BacktestReport, name: str) -> None:
        word = self.word
        wf = report.walk_forward
        if wf is None or not wf.folds:
            self._add(note_card("bars", word("Walk-forward windows"), word("No data")))
            return
        values = [fold.out_expectancy_r or 0.0 for fold in wf.folds]
        labels = [f"W{index + 1}" for index in range(len(wf.folds))]
        tips = [
            fold_tip(f.window.out_start, f.window.out_end, f.out_trades, f.out_expectancy_r)
            for f in wf.folds
        ]

        def build() -> QWidget:
            chart = BarChart(160, word("Walk-forward windows"))
            chart.set_bars(values, labels, tips=tips, unit=" R")
            return chart

        metrics = wf.metrics
        expectancy = "n/a" if metrics.expectancy_r is None else f"{metrics.expectancy_r:+.3f} R"
        verdict = "PASSED" if wf.passed else "NOT PASSED"
        footer = (
            f"{len(wf.folds)} windows (60 days in, 30 out), {metrics.trades} out-of-sample "
            f"trades, expectancy {expectancy} -> {verdict} (needs >= {wf.minimum_trades} trades "
            "and a positive expectancy)"
        )
        self._add(
            ChartCard(
                "bars",
                word("Walk-forward windows"),
                build(),
                word=card_words(self.persian),
                tags=[name, report.request.symbol, verdict],
                note=report.request.period,
                data=wf.to_json(),
                enlarge=build,
                footer=footer,
            ),
        )
