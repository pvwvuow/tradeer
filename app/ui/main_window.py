"""Main window: Simple/Advanced views, grouped sidebar, status bar and command palette."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.analysis.sessions import duration_text, next_change, session_label
from app.core.ui_prefs import ThemeName, UiPrefs, ViewMode, save_prefs
from app.domain.config import TradingDefaults
from app.engine.execution import ExecutionSnapshot
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.mt5.models import AccountKind
from app.observability.controls import LogControls
from app.observability.crash_handler import CrashTestError
from app.storage.runtime import StorageRuntime
from app.storage.sync import SyncStatus
from app.ui.analytics_page import AnalyticsContext, AnalyticsPage
from app.ui.backtest_page import BacktestContext, BacktestPage
from app.ui.command_palette import CommandPalette
from app.ui.commands import Command
from app.ui.connection_page import ConnectionContext, ConnectionPage
from app.ui.crash_dialog import CrashDialog
from app.ui.dashboard_page import DashboardContext, DashboardPage
from app.ui.data_page import DataPage
from app.ui.home_page import HomePage
from app.ui.journal_page import JournalContext, JournalPage
from app.ui.logs_page import LogsPage
from app.ui.market_page import MarketContext, MarketPage
from app.ui.model_page import ModelContext, ModelPage
from app.ui.navigation import ADVANCED_GROUPS, ADVANCED_PAGES, SIMPLE_HOME, pages_in_group
from app.ui.notifications_page import NotificationsContext, NotificationsPage
from app.ui.pages import PlaceholderPage, styled_label
from app.ui.positions_page import KILL_TEXT, PositionsPage, TradingContext
from app.ui.risk_page import RiskContext, RiskPage
from app.ui.signals_page import SignalsContext, SignalsPage
from app.ui.strategies_page import StrategiesPage
from app.ui.theme import build_qss, tokens_for

SIDEBAR_WIDTH = 232
KILL_SHORTCUT = "Ctrl+Shift+K"


class MainWindow(QMainWindow):
    def __init__(
        self,
        prefs: UiPrefs,
        prefs_dir: Path | None = None,
        log_controls: LogControls | None = None,
        connection: ConnectionContext | None = None,
        storage: StorageRuntime | None = None,
        market: MarketContext | None = None,
        signals: SignalsContext | None = None,
        risk: RiskContext | None = None,
        trading: TradingContext | None = None,
        backtest: BacktestContext | None = None,
        model: ModelContext | None = None,
        dashboard: DashboardContext | None = None,
        analytics: AnalyticsContext | None = None,
        journal: JournalContext | None = None,
        notifications: NotificationsContext | None = None,
    ) -> None:
        super().__init__()
        self.prefs = prefs
        self.defaults = TradingDefaults()
        self.log_controls = log_controls
        self._prefs_dir = prefs_dir
        self._page_index: dict[str, int] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._palette: CommandPalette | None = None
        self._crash_dialog: CrashDialog | None = None
        # Plain data only: crash reports read this from other threads (never touch widgets).
        self._crash_state: dict[str, str] = {
            "page": "",
            "view_mode": prefs.view_mode.value,
            "theme": prefs.theme.value,
            "operating_mode": self.defaults.mode.value,
            "mt5": ConnectionState.DISCONNECTED.value,
        }
        self.setWindowTitle(f"MT5 Trading Workstation {__version__}")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_top_bar())
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.sidebar = self._build_sidebar()
        self.pages = QStackedWidget()
        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(self.pages, 1)
        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)
        self.home = HomePage(signals, risk, trading)
        self._add_page(SIMPLE_HOME.page_id, self.home)
        self.logs_page = LogsPage(log_controls) if log_controls is not None else None
        self.connection_page = ConnectionPage(connection) if connection is not None else None
        self.data_page = DataPage(storage) if storage is not None else None
        self.market_page = MarketPage(market)
        self.signals_page = SignalsPage(signals)
        self.strategies_page = StrategiesPage(signals.settings if signals is not None else None)
        self.risk_page = RiskPage(risk)
        self.positions_page = PositionsPage(trading)
        self.backtest_page = BacktestPage(backtest)
        self.model_page = ModelPage(model)
        self.dashboard_page = DashboardPage(dashboard)
        self.analytics_page = AnalyticsPage(analytics)
        self.journal_page = JournalPage(journal)
        self.notifications_page = (
            NotificationsPage(notifications) if notifications is not None else None
        )
        self.trading = trading
        if trading is not None:
            self.signals_page.mode_text = lambda: trading.settings.mode.label
        self._connected = False
        self.settings_tabs: QTabWidget | None = None
        for spec in ADVANCED_PAGES:
            if spec.page_id == "dashboard":
                self._add_page(spec.page_id, self.dashboard_page)
            elif spec.page_id == "analytics":
                self._add_page(spec.page_id, self.analytics_page)
            elif spec.page_id == "journal":
                self._add_page(spec.page_id, self.journal_page)
            elif spec.page_id == "market":
                self._add_page(spec.page_id, self.market_page)
            elif spec.page_id == "signals":
                self._add_page(spec.page_id, self.signals_page)
            elif spec.page_id == "strategies":
                self._add_page(spec.page_id, self.strategies_page)
            elif spec.page_id == "risk":
                self._add_page(spec.page_id, self.risk_page)
            elif spec.page_id == "positions":
                self._add_page(spec.page_id, self.positions_page)
            elif spec.page_id == "backtest":
                self._add_page(spec.page_id, self.backtest_page)
            elif spec.page_id == "model":
                self._add_page(spec.page_id, self.model_page)
            elif spec.page_id == "logs" and self.logs_page is not None:
                self._add_page(spec.page_id, self.logs_page)
            elif spec.page_id == "settings" and self.data_page is not None:
                self.settings_tabs = QTabWidget()
                self.settings_tabs.setObjectName("SettingsTabs")
                first = self.connection_page or PlaceholderPage(spec)
                self.settings_tabs.addTab(first, "Account & connection")
                self.settings_tabs.addTab(self.data_page, "Data & cloud sync")
                if self.notifications_page is not None:
                    self.settings_tabs.addTab(self.notifications_page, "Notifications")
                self._add_page(spec.page_id, self.settings_tabs)
            elif spec.page_id == "settings" and self.connection_page is not None:
                self._add_page(spec.page_id, self.connection_page)
            else:
                self._add_page(spec.page_id, PlaceholderPage(spec))
        self._build_status_bar()
        self.home.stop = self.ask_kill
        self.home.on_onboarded = self.finish_onboarding
        self.home.on_status_bar = self._show_status_bar
        if market is not None:
            watch = market.watch
            self.home.quote = lambda symbol: watch.snapshot.quotes.get(symbol)
        self.home.show_welcome(not prefs.onboarded)
        if connection is not None and self.connection_page is not None:
            self.connection_page.bridge.status.connect(self.set_connection_status)
            self.set_connection_status(connection.service.status)
        if self.data_page is not None:
            self.data_page.bridge.sync.connect(self.set_sync_status)
            self.set_sync_status(self.data_page.last_status)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self.update_session_clock)
        self._clock_timer.start(1000)
        self.update_session_clock()
        self._shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut.activated.connect(self.open_command_palette)
        self._kill_shortcut = QShortcut(QKeySequence(KILL_SHORTCUT), self)
        self._kill_shortcut.activated.connect(self.ask_kill)
        self._kill_shortcut.setEnabled(trading is not None)
        self.positions_page.bridge.snapshot.connect(self.set_execution_status)
        if trading is not None:
            self.set_execution_status(trading.engine.snapshot)
        self.apply_theme(prefs.theme, persist=False)
        self.set_view_mode(prefs.view_mode, persist=False)

    @property
    def nav_buttons(self) -> dict[str, QPushButton]:
        return dict(self._nav_buttons)

    def current_page_id(self) -> str:
        current = self.pages.currentIndex()
        return next((key for key, index in self._page_index.items() if index == current), "")

    @property
    def crash_dialog(self) -> CrashDialog | None:
        return self._crash_dialog

    def crash_state(self) -> dict[str, str]:
        return dict(self._crash_state)

    def show_page(self, page_id: str) -> None:
        self.pages.setCurrentIndex(self._page_index[page_id])
        self._crash_state["page"] = page_id
        button = self._nav_buttons.get(page_id)
        if button is not None:
            button.setChecked(True)

    def set_view_mode(self, mode: ViewMode, persist: bool = True) -> None:
        self.prefs = self.prefs.model_copy(update={"view_mode": mode})
        self._crash_state["view_mode"] = mode.value
        advanced = mode is ViewMode.ADVANCED
        self.sidebar.setVisible(advanced)
        self._shortcut.setEnabled(advanced)
        self.view_button.setText("Switch to Simple" if advanced else "Switch to Advanced")
        self.settings_button.setVisible(not advanced)
        self.settings_button.setText("Settings")
        # Simple view: the plain status line and Stop button on Home, the full bar on request.
        self.statusBar().setVisible(advanced or self.home.status_bar_button.isChecked())
        self.show_page("dashboard" if advanced else SIMPLE_HOME.page_id)
        if persist:
            self._persist()

    def finish_onboarding(self, advanced: bool) -> None:
        """The first-run screen was answered: remember it and open the chosen view."""
        self.prefs = self.prefs.model_copy(update={"onboarded": True})
        self.set_view_mode(ViewMode.ADVANCED if advanced else ViewMode.SIMPLE)

    def toggle_simple_settings(self) -> None:
        """Simple view: Settings (account, connection, data) and back to Home."""
        if self.current_page_id() == SIMPLE_HOME.page_id:
            self.show_page("settings")
            self.settings_button.setText("Back to Home")
        else:
            self.show_page(SIMPLE_HOME.page_id)
            self.settings_button.setText("Settings")

    def toggle_view_mode(self) -> None:
        simple = self.prefs.view_mode is ViewMode.SIMPLE
        self.set_view_mode(ViewMode.ADVANCED if simple else ViewMode.SIMPLE)

    def apply_theme(self, theme: ThemeName, persist: bool = True) -> None:
        self.prefs = self.prefs.model_copy(update={"theme": theme})
        self._crash_state["theme"] = theme.value
        tokens = tokens_for(theme)
        self.setStyleSheet(build_qss(tokens))
        if self.logs_page is not None:
            self.logs_page.apply_tokens(tokens)
        self.market_page.apply_tokens(tokens)
        self.home.apply_tokens(tokens)
        next_theme = "light" if theme is ThemeName.DARK else "dark"
        self.theme_button.setText(f"Switch to {next_theme} theme")
        if persist:
            self._persist()

    def toggle_theme(self) -> None:
        dark = self.prefs.theme is ThemeName.DARK
        self.apply_theme(ThemeName.LIGHT if dark else ThemeName.DARK)

    def commands(self) -> list[Command]:
        items: list[Command] = []
        for spec in ADVANCED_PAGES:
            slot = self._nav_slot(spec.page_id)
            items.append(Command(f"go:{spec.page_id}", f"Go to {spec.title}", spec.group, slot))
        items.append(Command("theme", "Toggle theme", "Appearance", self.toggle_theme))
        items.append(Command("view", "Switch to Simple view", "Appearance", self._to_simple))
        if self.logs_page is not None:
            page = self.logs_page
            items.append(Command("debug", "Toggle debug mode", "Logs", page.toggle_debug))
            items.append(Command("log_folder", "Open log folder", "Logs", page.open_log_folder))
        items.append(Command("crash", "Test the crash reporter", "Logs", self.trigger_crash_test))
        if self.connection_page is not None:
            items.append(Command("connect", "Connect to MT5", "Connection", self._connect))
            items.append(
                Command("diagnose", "Run connection diagnostics", "Connection", self._diagnose),
            )
        if self.data_page is not None:
            items.append(Command("upload", "Upload to the cloud now", "Data", self._upload))
            items.append(Command("history", "Import trade history", "Data", self._import_history))
        return items

    def set_connection_status(self, status: object) -> None:
        """Slot: show the MT5 connection in the status bar and on the Simple home screen."""
        if not isinstance(status, ConnectionStatus):
            return
        self._crash_state["mt5"] = status.state.value
        self.connection_label.setText(f"\u25cf {status.status_bar_text()}")
        self._connected = status.connected
        badge = "ANALYSIS-ONLY" if status.analysis_only else self.operating_mode_label().upper()
        self.mode_badge.setText(badge)
        if self.trading is not None:
            self.set_execution_status(self.trading.engine.snapshot)
        self.home.set_connection(status.connected, plain_status(status))

    def operating_mode_label(self) -> str:
        if self.trading is not None:
            return self.trading.settings.mode.label
        return self.defaults.mode.label

    def set_execution_status(self, snapshot: object) -> None:
        """Slot: the bot state and mode in the status bar (spec F2)."""
        if not isinstance(snapshot, ExecutionSnapshot):
            return
        self._crash_state["operating_mode"] = snapshot.mode.value
        if not self.mode_badge.text().startswith("ANALYSIS-ONLY"):
            self.mode_badge.setText(snapshot.mode.label.upper())
        self.bot_state_label.setText(bot_state_text(snapshot, self._connected))

    def ask_kill(self) -> bool:
        """The kill switch from the status bar or Ctrl+Shift+K, always with a confirmation."""
        return self.positions_page.ask_kill()

    def update_session_clock(self, now: float | None = None) -> None:
        """Status bar (spec F2): the current session, the next open or close, the next news."""
        moment = time.time() if now is None else now
        change, seconds = next_change(moment)
        upcoming = f" \u00b7 {change} in {duration_text(seconds)}" if change else ""
        self.session_clock_label.setText(f"{session_label(moment)}{upcoming}")
        self.news_label.setText(self.market_page.next_news_text(moment))
        self.home.tick()

    def set_sync_status(self, status: object) -> None:
        """Slot: show the cloud sync state in the status bar."""
        if isinstance(status, SyncStatus):
            self.sync_label.setText(status.status_bar_text())
            self.sync_label.setToolTip(status.message)

    def trigger_crash_test(self) -> None:
        """Raise on purpose: the crash hook must write a report and show the dialog."""
        raise CrashTestError("Deliberate crash test from the command palette")

    def show_crash_dialog(self, report_path: str, summary: str) -> None:
        """Slot for `CrashNotifier.crashed`. Shows one dialog at a time."""
        if self._crash_dialog is not None and self._crash_dialog.isVisible():
            return
        crash_dir = self.log_controls.crash_dir if self.log_controls else Path(report_path).parent
        path = Path(report_path) if report_path else None
        self._crash_dialog = CrashDialog(path, summary, crash_dir, self)
        self._crash_dialog.open()

    def open_command_palette(self) -> CommandPalette | None:
        if self.prefs.view_mode is not ViewMode.ADVANCED:
            return None
        self._palette = CommandPalette(self.commands(), self)
        self._palette.open()
        return self._palette

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)
        layout.addWidget(styled_label("MT5 Trading Workstation", "brand"))
        layout.addStretch(1)
        self.view_button = QPushButton()
        self.view_button.setObjectName("ViewModeButton")
        self.view_button.clicked.connect(self.toggle_view_mode)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("ThemeButton")
        self.theme_button.clicked.connect(self.toggle_theme)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("SimpleSettingsButton")
        self.settings_button.clicked.connect(self.toggle_simple_settings)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.view_button)
        layout.addWidget(self.theme_button)
        return bar

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(2)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for group in ADVANCED_GROUPS:
            layout.addWidget(styled_label(group.upper(), "section"))
            for spec in pages_in_group(group):
                button = QPushButton(spec.title)
                button.setObjectName(f"nav_{spec.page_id}")
                button.setProperty("nav", True)
                button.setCheckable(True)
                button.clicked.connect(self._nav_slot(spec.page_id))
                self._nav_group.addButton(button)
                self._nav_buttons[spec.page_id] = button
                layout.addWidget(button)
        layout.addStretch(1)
        return sidebar

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.connection_label = styled_label("● MT5: not connected", "status")
        self.mode_badge = styled_label(self.defaults.mode.label.upper(), "badge")
        self.bot_state_label = styled_label("Bot: stopped", "status")
        self.kill_switch = QPushButton("Stop trading")
        self.kill_switch.setObjectName("KillSwitch")
        self.kill_switch.setProperty("variant", "danger")
        self.kill_switch.setEnabled(self.trading is not None)
        tip = f"Kill switch ({KILL_SHORTCUT}): {KILL_TEXT.splitlines()[0]}"
        self.kill_switch.setToolTip(tip if self.trading is not None else "Nothing is running.")
        self.kill_switch.clicked.connect(self.ask_kill)
        self.sync_label = styled_label("Cloud: off", "status")
        self.sync_label.setObjectName("SyncLabel")
        self.session_clock_label = styled_label("", "status")
        self.session_clock_label.setObjectName("SessionClock")
        self.news_label = styled_label("", "status")
        self.news_label.setObjectName("NextNews")
        bar.addWidget(self.connection_label)
        bar.addWidget(self.mode_badge)
        bar.addWidget(self.bot_state_label)
        bar.addWidget(self.sync_label)
        bar.addWidget(self.session_clock_label)
        bar.addWidget(self.news_label)
        bar.addPermanentWidget(styled_label(f"v{__version__}", "status"))
        bar.addPermanentWidget(self.kill_switch)
        self.setStatusBar(bar)

    def _nav_slot(self, page_id: str) -> Callable[[], None]:
        def slot() -> None:
            self.show_page(page_id)

        return slot

    def _add_page(self, page_id: str, widget: QWidget) -> None:
        self._page_index[page_id] = self.pages.addWidget(widget)

    def _show_status_bar(self, shown: bool) -> None:
        self.statusBar().setVisible(shown or self.prefs.view_mode is ViewMode.ADVANCED)

    def _to_simple(self) -> None:
        self.set_view_mode(ViewMode.SIMPLE)

    def _show_connection_tab(self) -> None:
        self.show_page("settings")
        if self.settings_tabs is not None and self.connection_page is not None:
            self.settings_tabs.setCurrentWidget(self.connection_page)

    def _connect(self) -> None:
        if self.connection_page is not None:
            self._show_connection_tab()
            self.connection_page.connect_to_mt5()

    def _diagnose(self) -> None:
        if self.connection_page is not None:
            self._show_connection_tab()
            self.connection_page.run_diagnostics()

    def _show_data_tab(self) -> None:
        self.show_page("settings")
        if self.settings_tabs is not None and self.data_page is not None:
            self.settings_tabs.setCurrentWidget(self.data_page)

    def _upload(self) -> None:
        if self.data_page is not None:
            self._show_data_tab()
            self.data_page.upload_now()

    def _import_history(self) -> None:
        if self.data_page is not None:
            self._show_data_tab()
            self.data_page.import_history()

    def _persist(self) -> None:
        if self._prefs_dir is not None:
            save_prefs(self._prefs_dir, self.prefs)


def bot_state_text(snapshot: ExecutionSnapshot, connected: bool) -> str:
    if snapshot.stopped:
        return "Bot: stopped (kill switch)"
    if not connected:
        return "Bot: disconnected"
    open_trades = sum(1 for view in snapshot.positions if not view.pending)
    return f"Bot: running \u00b7 {open_trades} open"


def plain_status(status: ConnectionStatus) -> str:
    """The status line in plain language for the Simple view (spec B3b)."""
    account = status.account
    if status.connected and account is not None:
        kinds = {
            AccountKind.DEMO: "practice (demo)",
            AccountKind.CONTEST: "contest",
            AccountKind.REAL: "REAL money",
        }
        kind = kinds.get(account.kind, "trading")
        text = f"Status: connected to {account.company}, {kind} account {account.login}."
        if status.analysis_only:
            text += " Read-only login, so the app only watches and never trades."
        return text
    if status.state is ConnectionState.RECONNECTING:
        return "Status: the connection to MetaTrader 5 was lost. Reconnecting..."
    if status.state is ConnectionState.CONNECTING:
        return "Status: connecting to MetaTrader 5..."
    if status.state is ConnectionState.FAILED:
        return "Status: could not connect to MetaTrader 5. Advanced view > Settings shows why."
    return "Status: not connected"
