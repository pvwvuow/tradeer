"""Main window: Simple/Advanced views, grouped sidebar, status bar and command palette.

0.32 (UI v2, docs/UI_V2.md): the frame is drawn like the owner's design of 9 October 2026.
The top bar carries the logo, "MT5 Workstation", the crumb ("TRADE / 01"), the MT5 tag
("MT5 · DEMO · CONNECTED"), the mode tag, Search with its Ctrl K hint, the Simple /
Advanced segment, FA · EN and the theme switch; a LIVE ticker strip runs under it in the
Advanced view; the sidebar numbers the pages 01 to 14 under TRADE, ANALYZE and SYSTEM; and
the status bar ends with the coral Stop trading button and its Ctrl Shift K hint.

With Persian chosen (spec A, F1) the whole frame and the page headers are Persian and run
right to left, in the Vazirmatn font, like the design. The bodies of the Advanced pages
stay English (left to right) until each page gets its own UI v2 layout.

Keyboard (spec F1): Tab reaches every control and shows a focus ring, Ctrl+, opens Settings,
Ctrl+K the command palette (Advanced view) and Ctrl+Shift+K the kill switch. Buttons that only
show an icon or an arrow get a name for screen readers.

7 October 2026 polish: the Windows title bar takes the theme's colors, a page fades in when
it opens, and everything clickable shows a hand cursor.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.analysis.sessions import duration_text, next_change, session_label
from app.core.ui_prefs import Language, ThemeName, UiPrefs, ViewMode, save_prefs
from app.domain.config import TradingDefaults
from app.engine.execution import ExecutionSnapshot
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.mt5.models import AccountKind
from app.observability.controls import LogControls
from app.observability.crash_handler import CrashTestError
from app.storage.runtime import StorageRuntime
from app.storage.sync import SyncStatus
from app.ui.ai_lab_page import AiLabPage, ai_lab_context
from app.ui.analytics_page import AnalyticsContext, AnalyticsPage
from app.ui.backtest_page import BacktestContext, BacktestPage
from app.ui.command_palette import CommandPalette
from app.ui.commands import Command
from app.ui.connection_page import ConnectionContext, ConnectionPage
from app.ui.crash_dialog import CrashDialog
from app.ui.dashboard_page import DashboardContext, DashboardPage
from app.ui.data_page import DataPage
from app.ui.health_page import HealthContext, HealthPage
from app.ui.home_page import HomePage
from app.ui.i18n import RESTART_TEXT, Translator, persian_font, translate_widgets
from app.ui.journal_page import JournalContext, JournalPage
from app.ui.logs_page import LogsPage
from app.ui.market_page import MarketContext, MarketPage
from app.ui.model_page import ModelContext, ModelPage
from app.ui.motion import fade_in
from app.ui.navigation import (
    ADVANCED_GROUPS,
    ADVANCED_PAGES,
    SIMPLE_HOME,
    page_by_id,
    pages_in_group,
)
from app.ui.notifications_page import NotificationsContext, NotificationsPage
from app.ui.pages import PageHeader, PlaceholderPage, decorate_page, styled_label
from app.ui.positions_page import KILL_TEXT, PositionsPage, TradingContext
from app.ui.risk_page import RiskContext, RiskPage
from app.ui.shell import (
    FOOTER_HEIGHT,
    FRAME_FA,
    PAGE_HEADS_FA,
    SIDEBAR_WIDTH,
    TEXT_FAMILY,
    TOP_BAR_HEIGHT,
    DayOpens,
    GroupRule,
    KbdButton,
    Led,
    LogoMark,
    NavButton,
    Segmented,
    Themed,
    TickerStrip,
    frame_qss,
    load_frame_fonts,
    persian_digits,
    shell_text,
)
from app.ui.signals_page import SignalsContext, SignalsPage
from app.ui.strategies_page import StrategiesPage
from app.ui.style import (
    ICON_SIZE,
    Glyph,
    chip,
    glyph_icon,
    hand_cursors,
    icons_available,
    name_controls,
    set_chip,
    style_plots,
    style_tables,
    ui_font,
)
from app.ui.theme import ThemeTokens, build_qss, tokens_for
from app.ui.updates_page import UpdateBanner, UpdatesContext, UpdatesPage
from app.ui.v2 import v2_qss
from app.ui.window_chrome import style_title_bar

KILL_SHORTCUT = "Ctrl+Shift+K"
SETTINGS_SHORTCUT = "Ctrl+,"
LANGUAGE_TITLE = "Language / \u0632\u0628\u0627\u0646"
LANGUAGE_TEXT = "FA \u00b7 EN"
BRAND = "MT5 Workstation"
POWER_GLYPH = "\ue7e8"
PAGE_NUMBERS: dict[str, int] = {
    spec.page_id: number for number, spec in enumerate(ADVANCED_PAGES, start=1)
}


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
        updates: UpdatesContext | None = None,
        health: HealthContext | None = None,
    ) -> None:
        super().__init__()
        self.prefs = prefs
        self.defaults = TradingDefaults()
        self.log_controls = log_controls
        self._prefs_dir = prefs_dir
        self._page_index: dict[str, int] = {}
        self._nav_buttons: dict[str, NavButton] = {}
        self._group_rules: list[GroupRule] = []
        self._palette: CommandPalette | None = None
        self._crash_dialog: CrashDialog | None = None
        self.page_motion = True  # pages fade in when they open
        # Plain data only: crash reports read this from other threads (never touch widgets).
        self._crash_state: dict[str, str] = {
            "page": "",
            "view_mode": prefs.view_mode.value,
            "theme": prefs.theme.value,
            "operating_mode": self.defaults.mode.value,
            "mt5": ConnectionState.DISCONNECTED.value,
        }
        self.setWindowTitle(f"MT5 Trading Workstation {__version__}")
        load_frame_fonts()
        font = ui_font(self.font())
        font.setFamilies([TEXT_FAMILY, *font.families()])
        self.setFont(font)
        self.translator = Translator(prefs.language)
        self.frame_translator = Translator(prefs.language, FRAME_FA)
        self.persian = self.translator.right_to_left
        self.notify_language: Callable[[str], None] = self._show_language_note
        if self.persian:
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.setFont(persian_font(self.font()))
        self.resize(1360, 860)
        self.setMinimumSize(1024, 640)
        self._connected = False
        self._day_opens = DayOpens()
        self._prices: Callable[[], dict[str, float]] | None = None
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.top_bar = self._build_top_bar()
        root_layout.addWidget(self.top_bar)
        self.updates = updates
        if updates is not None:
            updates.restart = self.restart_to_update
        self.update_banner = UpdateBanner(updates, self.show_updates)
        root_layout.addWidget(self.update_banner)
        self.ticker = TickerStrip()
        self.ticker.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        root_layout.addWidget(self.ticker)
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
        self.home.translator = self.translator
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
        self.dashboard_page = DashboardPage(dashboard, persian=self.persian)
        self.dashboard_page.go = self.show_page
        self.analytics_page = AnalyticsPage(analytics)
        self.journal_page = JournalPage(journal)
        self.ai_lab_page = AiLabPage(ai_lab_context(analytics, backtest))
        self.health_page = HealthPage(health)
        self.notifications_page = (
            NotificationsPage(notifications) if notifications is not None else None
        )
        self.updates_page = UpdatesPage(updates) if updates is not None else None
        self.confirm_update: Callable[[str], bool] = self._confirm_update
        self.trading = trading
        if trading is not None:
            self.signals_page.mode_text = lambda: trading.settings.mode.label
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
            elif spec.page_id == "ai_lab":
                self._add_page(spec.page_id, self.ai_lab_page)
            elif spec.page_id == "health":
                self._add_page(spec.page_id, self.health_page)
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
                if self.updates_page is not None:
                    self.settings_tabs.addTab(self.updates_page, "Updates")
                self._add_page(spec.page_id, self.settings_tabs)
            elif spec.page_id == "settings" and self.connection_page is not None:
                self._add_page(spec.page_id, self.connection_page)
            else:
                self._add_page(spec.page_id, PlaceholderPage(spec))
        self._build_status_bar()
        self._dress_page_headers()
        if self.persian:
            self._keep_english_left_to_right()
        style_tables(self)
        name_controls(self)
        hand_cursors(self)
        self.home.stop = self.ask_kill
        self.home.on_onboarded = self.finish_onboarding
        self.home.on_status_bar = self._show_status_bar
        if market is not None:
            watch = market.watch
            self.home.quote = lambda symbol: watch.snapshot.quotes.get(symbol)

            def prices() -> dict[str, float]:
                found: dict[str, float] = {}
                for symbol, quote in watch.snapshot.quotes.items():
                    bid = getattr(quote, "bid", None)
                    if isinstance(bid, int | float):
                        found[symbol] = float(bid)
                return found

            self._prices = prices
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
        self._settings_shortcut = QShortcut(QKeySequence(SETTINGS_SHORTCUT), self)
        self._settings_shortcut.activated.connect(self.open_settings)
        self.positions_page.bridge.snapshot.connect(self.set_execution_status)
        if trading is not None:
            self.set_execution_status(trading.engine.snapshot)
        self.apply_theme(prefs.theme, persist=False)
        self.set_view_mode(prefs.view_mode, persist=False)

    @property
    def nav_buttons(self) -> dict[str, QPushButton]:
        found: dict[str, QPushButton] = {}
        found.update(self._nav_buttons)
        return found

    @property
    def view_button(self) -> QPushButton:
        """The segment that switches to the other view (Advanced while Simple is shown)."""
        simple = self.prefs.view_mode is ViewMode.SIMPLE
        return self.view_segment.buttons[1 if simple else 0]

    def current_page_id(self) -> str:
        current = self.pages.currentIndex()
        return next((key for key, index in self._page_index.items() if index == current), "")

    @property
    def crash_dialog(self) -> CrashDialog | None:
        return self._crash_dialog

    def crash_state(self) -> dict[str, str]:
        return dict(self._crash_state)

    def show_page(self, page_id: str) -> None:
        index = self._page_index[page_id]
        changed = index != self.pages.currentIndex()
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if changed and self.page_motion and page is not None:
            fade_in(page)
        self._crash_state["page"] = page_id
        simple = self.prefs.view_mode is ViewMode.SIMPLE
        self.page_crumb.setText("SIMPLE VIEW" if simple else page_crumb(page_id))
        button = self._nav_buttons.get(page_id)
        if button is not None:
            button.setChecked(True)

    def set_view_mode(self, mode: ViewMode, persist: bool = True) -> None:
        self.prefs = self.prefs.model_copy(update={"view_mode": mode})
        self._crash_state["view_mode"] = mode.value
        advanced = mode is ViewMode.ADVANCED
        self.sidebar.setVisible(advanced)
        self.ticker.setVisible(advanced)
        self._shortcut.setEnabled(advanced)
        self.search_button.setVisible(advanced)
        self.view_segment.choose(1 if advanced else 0)
        self.settings_button.setVisible(not advanced)
        self.settings_button.setText("Settings")
        # Simple view: the plain status line and Stop button on Home, the full bar on request.
        self.statusBar().setVisible(advanced or self.home.status_bar_button.isChecked())
        self.show_page("dashboard" if advanced else SIMPLE_HOME.page_id)
        self._retranslate()
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
        self._retranslate()

    def open_settings(self) -> None:
        """Ctrl+, opens Settings in either view (spec F1: all of it works from the keyboard)."""
        if self.prefs.view_mode is ViewMode.ADVANCED:
            self.show_page("settings")
        elif self.current_page_id() == SIMPLE_HOME.page_id:
            self.toggle_simple_settings()

    def show_updates(self) -> None:
        """Open Settings > Updates (from the banner or the command palette)."""
        self.show_page("settings")
        if self.prefs.view_mode is ViewMode.SIMPLE:
            self.settings_button.setText("Back to Home")
        if self.settings_tabs is not None and self.updates_page is not None:
            self.settings_tabs.setCurrentWidget(self.updates_page)
        self._retranslate()

    def toggle_language(self) -> Language:
        """Save the other language; the app uses it from its next start."""
        chosen = Language.EN if self.prefs.language is Language.FA else Language.FA
        self.prefs = self.prefs.model_copy(update={"language": chosen})
        self._persist()
        self.language_button.setToolTip(f"{LANGUAGE_TITLE}: {chosen.value.upper()}")
        self.notify_language(RESTART_TEXT)
        return chosen

    def _show_language_note(self, text: str) -> None:
        QMessageBox.information(self, LANGUAGE_TITLE, text)

    def restart_to_update(self) -> bool:
        """Confirm, hand the downloaded version to the installer and close the app (J2.4)."""
        updates = self.updates
        if updates is None or not updates.service.snapshot.ready:
            self.show_updates()
            return False
        snapshot = updates.service.snapshot
        open_trades = 0
        if self.trading is not None:
            positions = self.trading.engine.snapshot.positions
            open_trades = sum(1 for view in positions if not view.pending)
        verb = "go back to" if snapshot.downgrade else "install"
        text = f"The app closes, will {verb} {snapshot.version} and starts again in about a minute."
        if open_trades:
            text += (
                f"\n\n{open_trades} trade(s) are open. Their stop loss and take profit stay at "
                "the broker while the app restarts, but nothing is managed for that minute."
            )
        if not self.confirm_update(text):
            return False
        error = updates.service.apply()
        if error:
            QMessageBox.warning(self, "Update", error)
            return False
        self.close()
        return True

    def _confirm_update(self, text: str) -> bool:
        answer = QMessageBox.question(self, "Restart to update", text)
        return answer == QMessageBox.StandardButton.Yes

    def toggle_view_mode(self) -> None:
        simple = self.prefs.view_mode is ViewMode.SIMPLE
        self.set_view_mode(ViewMode.ADVANCED if simple else ViewMode.SIMPLE)

    def apply_theme(self, theme: ThemeName, persist: bool = True) -> None:
        self.prefs = self.prefs.model_copy(update={"theme": theme})
        self._crash_state["theme"] = theme.value
        tokens = tokens_for(theme)
        qss = build_qss(tokens) + frame_qss(tokens, self.persian) + v2_qss(tokens)
        self.setStyleSheet(qss)
        style_title_bar(self, tokens)
        if self.logs_page is not None:
            self.logs_page.apply_tokens(tokens)
        self.market_page.apply_tokens(tokens)
        self.dashboard_page.apply_tokens(tokens)
        self.home.apply_tokens(tokens)
        style_plots(self, tokens)
        self._apply_frame_tokens(tokens)
        self._apply_icons(tokens)
        next_theme = "light" if theme is ThemeName.DARK else "dark"
        self.theme_button.setToolTip(f"Switch to the {next_theme} theme")
        self.theme_button.setAccessibleName(f"Switch to the {next_theme} theme")
        self.theme_button.setText("" if icons_available() else f"{next_theme.title()} theme")
        self._retranslate()
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
        items.append(Command("language", "Change the language", "Appearance", self._language))
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
        if self.updates_page is not None:
            items.append(Command("updates", "Check for updates", "System", self._check_updates))
        if self.data_page is not None:
            items.append(Command("upload", "Upload to the cloud now", "Data", self._upload))
            items.append(Command("history", "Import trade history", "Data", self._import_history))
        return items

    def set_connection_status(self, status: object) -> None:
        """Slot: show the MT5 connection in the status bar and on the Simple home screen."""
        if not isinstance(status, ConnectionStatus):
            return
        self._crash_state["mt5"] = status.state.value
        text, tone = connection_chip(status)
        set_chip(self.connection_chip, text, tone)
        self.connection_chip.setToolTip(status.status_bar_text())
        self.connection_label.setText(footer_connection(status, self.persian))
        self.connection_led.set_tone(tone)
        self._connected = status.connected
        self._dashboard_connection(status, tone)
        badge = "ANALYSIS-ONLY" if status.analysis_only else self.operating_mode_label().upper()
        self._set_mode(badge)
        if self.trading is not None:
            self.set_execution_status(self.trading.engine.snapshot)
        self.home.set_connection(status.connected, plain_status(status))
        self._retranslate()

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
            self._set_mode(snapshot.mode.label.upper())
        self.bot_state_label.setText(self._foot(bot_state_text(snapshot, self._connected)))
        self._retranslate()

    def set_nav_badge(self, page_id: str, text: str, tone: str = "warning") -> None:
        """A small count next to a sidebar page, e.g. the signals waiting for approval."""
        button = self._nav_buttons.get(page_id)
        if button is not None:
            button.set_badge(text, tone)

    def set_nav_dot(self, page_id: str, tone: str) -> None:
        """A status light next to a sidebar page ("" hides it), e.g. Health in amber."""
        button = self._nav_buttons.get(page_id)
        if button is not None:
            button.set_dot(tone)

    def ask_kill(self) -> bool:
        """The kill switch from the status bar or Ctrl+Shift+K, always with a confirmation."""
        return self.positions_page.ask_kill()

    def update_session_clock(self, now: float | None = None) -> None:
        """Status bar (spec F2): the current session, the next open or close, the next news."""
        moment = time.time() if now is None else now
        change, seconds = next_change(moment)
        upcoming = f" \u00b7 {change} in {duration_text(seconds)}" if change else ""
        self.session_clock_label.setText(self._foot(f"{session_label(moment)}{upcoming}"))
        self.news_label.setText(self.market_page.next_news_text(moment))
        self._update_ticker(moment)
        self._update_badges()
        self.home.tick()
        self._retranslate()

    def set_sync_status(self, status: object) -> None:
        """Slot: show the cloud sync state in the status bar."""
        if isinstance(status, SyncStatus):
            self.sync_label.setText(self._foot(status.status_bar_text()))
            self.sync_label.setToolTip(status.message)
            self.dashboard_page.set_health("Cloud sync", *sync_health(status))

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

    def _word(self, english: str) -> str:
        return shell_text(english, self.persian)

    def _foot(self, text: str) -> str:
        """A status bar text in the chosen language (Persian counts in Persian digits)."""
        translated = self.frame_translator.text(text)
        return persian_digits(translated) if self.persian else translated

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(TOP_BAR_HEIGHT)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(0)
        self.logo = LogoMark()
        layout.addWidget(self.logo)
        layout.addSpacing(10)
        layout.addWidget(styled_label(BRAND, "brand"))
        layout.addSpacing(16)
        self.page_crumb = styled_label("", "crumb")
        self.page_crumb.setObjectName("PageCrumb")
        layout.addWidget(self.page_crumb)
        layout.addStretch(1)
        self.connection_chip = chip("\u25cf MT5 \u00b7 NOT CONNECTED", "neutral")
        self.connection_chip.setObjectName("ConnectionChip")
        self.mode_chip = chip(self.defaults.mode.label.upper(), mode_tone(self.defaults.mode.label))
        self.mode_chip.setObjectName("ModeChip")
        self.mode_chip.setToolTip("Operating mode")
        self.search_button = KbdButton(self._word("Search"), "Ctrl K")
        self.search_button.setObjectName("SearchButton")
        self.search_button.setToolTip("Command palette: every page and action")
        self.search_button.clicked.connect(self.open_command_palette)
        self.view_segment = Segmented([self._word("Simple"), self._word("Advanced")])
        self.view_segment.setObjectName("Segmented")
        self.view_segment.setToolTip("Simple or Advanced view")
        self.view_segment.group.idClicked.connect(self._segment_clicked)
        self.language_button = _ghost_button(LANGUAGE_TEXT, "Language")
        self.language_button.setToolTip(LANGUAGE_TITLE)
        self.language_button.clicked.connect(self.toggle_language)
        self.theme_button = _ghost_button("", "ThemeButton")
        self.theme_button.clicked.connect(self.toggle_theme)
        self.settings_button = _ghost_button("Settings", "SimpleSettingsButton")
        self.settings_button.setToolTip(f"Settings ({SETTINGS_SHORTCUT})")
        self.settings_button.clicked.connect(self.toggle_simple_settings)
        cluster: list[QWidget] = [
            self.connection_chip,
            self.mode_chip,
            self.search_button,
            self.view_segment,
            self.language_button,
            self.theme_button,
            self.settings_button,
        ]
        for index, widget in enumerate(cluster):
            if index:
                layout.addSpacing(10)
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar.setAccessibleName(self._word("Pages"))
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 6, 10, 12)
        layout.setSpacing(0)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for group in ADVANCED_GROUPS:
            rule = GroupRule(group)
            self._group_rules.append(rule)
            layout.addWidget(rule)
            for spec in pages_in_group(group):
                button = NavButton(PAGE_NUMBERS[spec.page_id], self._word(spec.title))
                button.setObjectName(f"nav_{spec.page_id}")
                head = PAGE_HEADS_FA.get(spec.page_id)
                button.setToolTip(head[1] if self.persian and head else spec.summary)
                button.clicked.connect(self._nav_slot(spec.page_id))
                self._nav_group.addButton(button)
                self._nav_buttons[spec.page_id] = button
                layout.addWidget(button)
        layout.addStretch(1)
        return sidebar

    def _apply_frame_tokens(self, tokens: ThemeTokens) -> None:
        """Give the painted frame pieces the theme's colors."""
        painted: list[Themed] = [
            self.logo,
            self.ticker,
            self.search_button,
            self.kill_switch,
            self.connection_led,
            *self._group_rules,
            *self._nav_buttons.values(),
        ]
        for widget in painted:
            widget.apply_tokens(tokens)

    def _apply_icons(self, tokens: ThemeTokens) -> None:
        """Redraw the frame's icon-font icons in the theme's colors (pixmaps, not text)."""
        self.search_button.set_glyph(glyph_icon(Glyph.SEARCH, tokens.text_secondary))
        self.kill_switch.set_glyph(glyph_icon(POWER_GLYPH, tokens.loss))
        for button, glyph in (
            (self.theme_button, Glyph.THEME),
            (self.settings_button, Glyph.SETTINGS),
        ):
            button.setIcon(glyph_icon(glyph, tokens.text_secondary))
            button.setIconSize(QSize(ICON_SIZE - 1, ICON_SIZE - 1))
        if icons_available():
            self.theme_button.setFixedSize(34, 34)

    def _set_mode(self, text: str) -> None:
        self.mode_badge.setText(text)
        set_chip(self.mode_chip, text, mode_tone(text))

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        bar.setFixedHeight(FOOTER_HEIGHT)
        connection = QWidget()
        row = QHBoxLayout(connection)
        row.setContentsMargins(7, 0, 0, 0)
        row.setSpacing(0)
        self.connection_led = Led(6)
        self.connection_label = styled_label(self._connection_text(), "foot")
        row.addWidget(self.connection_led, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.connection_label)
        self.mode_badge = styled_label(self.defaults.mode.label.upper(), "foot_num")
        self.mode_badge.setObjectName("ModeBadge")
        self.bot_state_label = styled_label(self._foot("Bot: stopped"), "foot")
        self.kill_switch = KbdButton(
            self._word("Stop trading"),
            "Ctrl Shift K",
            "danger",
            point_size=9.0,
            height=24,
        )
        self.kill_switch.setObjectName("KillSwitch")
        self.kill_switch.setProperty("variant", "danger")
        self.kill_switch.setEnabled(self.trading is not None)
        tip = f"Kill switch ({KILL_SHORTCUT}): {KILL_TEXT.splitlines()[0]}"
        self.kill_switch.setToolTip(tip if self.trading is not None else "Nothing is running.")
        self.kill_switch.clicked.connect(self.ask_kill)
        self.sync_label = styled_label(self._foot("Cloud: off"), "foot")
        self.sync_label.setObjectName("SyncLabel")
        self.session_clock_label = styled_label("", "foot")
        self.session_clock_label.setObjectName("SessionClock")
        self.news_label = styled_label("", "foot")
        self.news_label.setObjectName("NextNews")
        bar.addWidget(connection)
        bar.addWidget(self.mode_badge)
        bar.addWidget(self.bot_state_label)
        bar.addWidget(self.sync_label)
        bar.addWidget(self.session_clock_label)
        bar.addWidget(self.news_label)
        bar.addPermanentWidget(styled_label(f"v{__version__}", "foot_num"))
        bar.addPermanentWidget(self.kill_switch)
        self.setStatusBar(bar)

    def _connection_text(self) -> str:
        return footer_connection(ConnectionStatus(), self.persian)

    def _dress_page_headers(self) -> None:
        """Every page header shows its place ("SYSTEM / 11"); Persian headers in Persian."""
        for page_id, index in self._page_index.items():
            page = self.pages.widget(index)
            if page_id == SIMPLE_HOME.page_id or page is None:
                continue
            spec = page_by_id(page_id)
            persian = PAGE_HEADS_FA.get(page_id) if self.persian else None
            for header in page.findChildren(PageHeader):
                if header.title.text() != spec.title:
                    continue
                header.crumb.setText(page_crumb(page_id))
                header.crumb.setVisible(True)
                if persian is not None:
                    header.title.setText(persian[0])
                    header.subtitle.setText(persian[1])
                    header.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    def _keep_english_left_to_right(self) -> None:
        """The bodies of the Advanced pages and Settings stay English: left to right."""
        for page_id, index in self._page_index.items():
            page = self.pages.widget(index)
            ready = bool(getattr(page, "right_to_left_ready", False))
            if page_id != SIMPLE_HOME.page_id and page is not None and not ready:
                page.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
                for header in page.findChildren(PageHeader):
                    if header.crumb.text() == page_crumb(page_id):
                        header.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    def _dashboard_connection(self, status: ConnectionStatus, tone: str) -> None:
        """The account kind under the balance and the MT5 line of System health."""
        account = status.account
        kinds = {
            AccountKind.DEMO: "Demo account",
            AccountKind.REAL: "Real account",
            AccountKind.CONTEST: "Contest account",
        }
        page = self.dashboard_page
        page.set_account_kind(kinds.get(account.kind, "") if account is not None else "")
        if status.connected:
            page.set_health("MT5 connection", "OK", "profit")
        elif tone == "warning":
            page.set_health("MT5 connection", "Connecting", "warning")
        elif status.state is ConnectionState.FAILED:
            page.set_health("MT5 connection", "Failed", "loss")
        else:
            page.set_health("MT5 connection", "OFF", "loss")

    def _update_badges(self) -> None:
        """Sidebar: signals waiting for approval, and a light on Health when a line is not OK."""
        page = self.dashboard_page
        if self.data_page is not None:
            page.set_health("Local database", "OK", "profit")
        if self.updates is not None:
            snapshot = self.updates.service.snapshot
            if snapshot.ready:
                page.set_health("Update", f"{snapshot.version} ready", "warning")
            else:
                page.set_health("Update", "Up to date", "profit")
        waiting = str(page.waiting_count) if page.waiting_count else ""
        self.set_nav_badge("signals", persian_digits(waiting) if self.persian else waiting)
        self.set_nav_dot("health", page.health_tone)

    def _update_ticker(self, now: float) -> None:
        if self._prices is None:
            self.ticker.set_items([], False)
            return
        items = self._day_opens.items(self._prices(), now)
        self.ticker.set_items(items, self._connected)

    def _segment_clicked(self, index: int) -> None:
        mode = ViewMode.SIMPLE if index == 0 else ViewMode.ADVANCED
        if mode is not self.prefs.view_mode:
            self.set_view_mode(mode)

    def _retranslate(self) -> None:
        translate_widgets(self.translator, self.top_bar)

    def _nav_slot(self, page_id: str) -> Callable[[], None]:
        def slot() -> None:
            self.show_page(page_id)

        return slot

    def _add_page(self, page_id: str, widget: QWidget) -> None:
        if page_id != SIMPLE_HOME.page_id:
            decorate_page(widget, page_by_id(page_id))
        self._page_index[page_id] = self.pages.addWidget(widget)

    def _show_status_bar(self, shown: bool) -> None:
        self.statusBar().setVisible(shown or self.prefs.view_mode is ViewMode.ADVANCED)

    def _to_simple(self) -> None:
        self.set_view_mode(ViewMode.SIMPLE)

    def _language(self) -> None:
        self.toggle_language()

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

    def _check_updates(self) -> None:
        self.show_updates()
        if self.updates is not None:
            self.updates.service.check_now()

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


def _ghost_button(text: str, name: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    button.setProperty("variant", "ghost")
    return button


def nav_text(number: int, title: str) -> str:
    """A sidebar entry's text, e.g. "01   Dashboard" (UI v2 numbers the pages 01 to 14)."""
    return f"{number:02d}   {title}"


def page_crumb(page_id: str) -> str:
    """Where a page sits, as the design writes it: its group and number, "TRADE / 01"."""
    spec = page_by_id(page_id)
    if spec.group == "Simple":
        return ""
    return f"{spec.group.upper()} / {PAGE_NUMBERS[page_id]:02d}"


def mode_tone(mode: str) -> str:
    """Tag tone of an operating mode: watching in amber, real orders in coral, else ink."""
    key = mode.strip().lower()
    if key.startswith("analysis"):
        return "warning"
    if key in ("semi-auto", "auto"):
        return "loss"
    return "accent"


def connection_chip(status: ConnectionStatus) -> tuple[str, str]:
    """The top bar's MT5 tag as the design writes it, e.g. "MT5 · DEMO · CONNECTED"."""
    account = status.account
    dot = "\u25cf MT5 \u00b7 "
    if status.connected and account is not None:
        if account.kind is AccountKind.REAL:
            return f"{dot}REAL \u00b7 CONNECTED", "loss"
        return f"{dot}{account.kind.value.upper()} \u00b7 CONNECTED", "profit"
    if status.connected:
        return f"{dot}CONNECTED", "profit"
    if status.state is ConnectionState.CONNECTING:
        return f"{dot}CONNECTING\u2026", "warning"
    if status.state is ConnectionState.RECONNECTING:
        return f"{dot}RECONNECTING\u2026", "warning"
    if status.state is ConnectionState.FAILED:
        return f"{dot}FAILED", "loss"
    return f"{dot}NOT CONNECTED", "neutral"


def sync_health(status: SyncStatus) -> tuple[str, str]:
    """The Cloud sync line of System health: its words and tone."""
    if status.failed:
        return f"{status.failed} refused", "loss"
    if status.pending:
        return f"{status.pending} waiting", "warning"
    words = status.status_bar_text().removeprefix("Cloud: ")
    tone = "profit" if words in ("up to date", "uploading") else "neutral"
    return words.capitalize(), tone


def footer_connection(status: ConnectionStatus, persian: bool) -> str:
    """The status bar's MT5 text: "MT5 وصل · دمو" in Persian, the full English otherwise."""
    if not persian:
        return status.status_bar_text()
    account = status.account
    if status.connected:
        real = account is not None and account.kind is AccountKind.REAL
        return "MT5 وصل \u00b7 " + ("حساب واقعی" if real else "دمو")
    if status.state in (ConnectionState.CONNECTING, ConnectionState.RECONNECTING):
        return "در حال اتصال\u2026"
    if status.state is ConnectionState.FAILED:
        return "اتصال به MT5 ناموفق بود"
    return "MT5 وصل نیست"
