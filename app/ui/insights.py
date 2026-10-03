"""Phase 12 wiring (spec C11, C12, C14): the Dashboard, Analytics and Journal contexts, the
report scheduler, the notification center with its watcher, the tray and the Telegram bot.

`build_insights` makes the parts before the window exists; `Insights.attach` adds what needs
the window (the tray toasts and the report timer). Everything here only reads the engine's
snapshots or uses the same public calls as the buttons.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from app.analytics.behavior import NewsEvent
from app.analytics.trades import TradeRecord, load_trades
from app.calendar.store import CalendarStore
from app.core.clock import BrokerClock
from app.core.credentials import CredentialError, CredentialStore, read_password
from app.engine.execution import ExecutionEngine
from app.engine.market_watch import MarketWatch
from app.engine.signal_pipeline import SignalPipeline
from app.journal.detail import SignalDetails, detail_text
from app.journal.reports import REPORT_FOLDER, Period, ReportRepository
from app.journal.scheduler import ReportService
from app.journal.store import JournalRepository
from app.mt5.connection import ConnectionService
from app.notify.center import NotificationCenter
from app.notify.events import EventKind, Notice
from app.notify.remote import EngineRemote
from app.notify.settings import NotificationSettingsSource, token_name
from app.notify.telegram import TelegramApi, TelegramBot, TelegramError
from app.notify.watcher import NoticeWatcher
from app.observability.categories import LogCategory
from app.observability.logger import LogPipeline, audit, get_logger
from app.risk.risk_manager import RiskManager
from app.storage.backtest_store import BacktestRepository
from app.storage.runtime import StorageRuntime
from app.strategies.registry import strategy_for_magic
from app.ui.analytics_page import AnalyticsContext
from app.ui.dashboard_page import DashboardContext
from app.ui.journal_page import JournalContext
from app.ui.notifications_page import NotificationsContext
from app.ui.trade_history import TradeHistory

REPORT_CHECK_MS = 5 * 60 * 1000
ERROR_LEVEL = 40
TRADES_CACHE_SECONDS = 5.0
DAY = 86_400.0

_log = get_logger(LogCategory.NOTIFY)


def write(level: str, message: str) -> None:
    _log.log(level, "{}", message)


class TradeCache:
    """The closed trades, read again at most every few seconds (several pages share them)."""

    def __init__(self, storage: StorageRuntime) -> None:
        self._storage = storage
        self._lock = threading.Lock()
        self._at = -math.inf
        self._trades: list[TradeRecord] = []

    def __call__(self) -> list[TradeRecord]:
        now = time.monotonic()
        with self._lock:
            if now - self._at >= TRADES_CACHE_SECONDS:
                self._trades = load_trades(self._storage.store.db, strategy_for_magic)
                self._at = now
            return list(self._trades)

    def today(self) -> tuple[float, int]:
        start = math.floor(time.time() / DAY) * DAY
        closed = [t for t in self() if t.close_time >= start]
        return sum(t.net_profit for t in closed), len(closed)


@dataclass
class Insights:
    dashboard: DashboardContext
    analytics: AnalyticsContext
    journal: JournalContext
    notifications: NotificationsContext
    history: TradeHistory
    center: NotificationCenter
    watcher: NoticeWatcher
    reports: ReportService
    bot_holder: list[TelegramBot] = field(default_factory=list)
    timers: list[QTimer] = field(default_factory=list)
    stop_hooks: list[Callable[[], None]] = field(default_factory=list)

    def attach(self, window: QWidget) -> None:
        from app.ui.tray import TrayNotifier

        tray = TrayNotifier(window)
        self.center.set_toast(tray.notify)
        self.stop_hooks.append(tray.hide)
        timer = QTimer(window)
        timer.setInterval(REPORT_CHECK_MS)
        timer.timeout.connect(self.reports.run_due)
        timer.start()
        self.timers.append(timer)
        QTimer.singleShot(30_000, self.reports.run_due)

    def stop(self) -> None:
        for timer in self.timers:
            timer.stop()
        for bot in self.bot_holder:
            bot.stop()
        for hook in self.stop_hooks:
            hook()


def _offset(clock: Callable[[], BrokerClock | None]) -> float:
    found = clock()
    return found.offset_at(time.time()) * 3600.0 if found is not None else 0.0


def build_insights(
    *,
    profile: str,
    profile_dir: Path,
    storage: StorageRuntime,
    service: ConnectionService,
    execution: ExecutionEngine,
    pipeline: SignalPipeline,
    risk: RiskManager,
    watch: MarketWatch,
    calendar: CalendarStore,
    known_clock: Callable[[], BrokerClock | None],
    credentials: CredentialStore,
    logs: LogPipeline,
) -> Insights:
    trades = TradeCache(storage)
    journal = JournalRepository(storage.store, storage.current_account)
    details = SignalDetails(storage.store)
    runs = BacktestRepository(storage.store)

    def currency() -> str:
        usage = risk.snapshot.usage
        return usage.currency if usage is not None else ""

    def balance() -> float:
        usage = risk.snapshot.usage
        return usage.balance if usage is not None else math.nan

    def events(start: float, end: float) -> Sequence[NewsEvent]:
        return [
            NewsEvent(float(e.time), e.currency, e.impact.value)
            for e in calendar.between(int(start), int(end))
        ]

    def backtests() -> Sequence[tuple[str, Mapping[str, Any]]]:
        return [(f"Backtest {run.title}", run.metrics) for run in runs.recent(5)]

    def detail(trade: TradeRecord) -> str:
        return detail_text(
            trade,
            journal.events(trade.id),
            details.parts(trade.signal_id),
            journal.entry(trade.id),
        )

    settings = NotificationSettingsSource(profile_dir, lambda text: write("WARNING", text))
    center = NotificationCenter(lambda: settings.settings, log=write)
    watcher = NoticeWatcher(center)
    execution.add_listener(watcher.on_execution)
    pipeline.add_listener(watcher.on_signals)
    risk.add_listener(watcher.on_risk)
    service.add_listener(watcher.on_connection)
    storage.worker.add_listener(watcher.on_sync)

    def error_entry(entry: Mapping[str, Any]) -> None:
        watcher.on_error(str(entry.get("message", "")))

    def accept(level: int, category: str) -> bool:
        return level >= ERROR_LEVEL and category != LogCategory.NOTIFY.value

    logs.add_entry_sink(error_entry, accept)

    def send_report(notice: Notice) -> object:
        if not settings.settings.send_reports:
            return center.publish(Notice(EventKind.DAILY_REPORT, notice.text.splitlines()[0]))
        return center.publish(notice)

    reports = ReportService(
        ReportRepository(storage.store, profile_dir / REPORT_FOLDER),
        trades,
        account=storage.current_account,
        offset=lambda: _offset(known_clock),
        currency=currency,
        notify=send_report,
        log=write,
    )
    remote = EngineRemote(execution, pipeline, risk, lambda: service.status, trades.today)
    holder: list[TelegramBot] = []
    name = token_name(profile)

    def apply_bot() -> str:
        for bot in holder:
            bot.stop()
        holder.clear()
        center.set_telegram(None)
        current = settings.settings
        if not current.telegram_enabled:
            return "Telegram is off."
        try:
            token = read_password(credentials, name)
        except CredentialError as error:
            return f"Telegram is off: {error}"
        if not token:
            return "Telegram is off: no bot token saved."
        bot = TelegramBot(
            TelegramApi(token),
            lambda: settings.settings,
            remote,
            log=write,
            audit=lambda action, before, after: audit(
                action,
                before=before,
                after=after,
                source="telegram",
            ),
        )

        def send(notice: Notice) -> None:
            threading.Thread(target=bot.notify, args=(notice,), daemon=True).start()

        center.set_telegram(send)
        bot.start()
        holder.append(bot)
        write("INFO", f"Telegram bot started for {len(current.chat_ids)} chat(s)")
        return "Telegram bot started."

    def test() -> str:
        notice = Notice(EventKind.DAILY_REPORT, "Test notification from MT5 Trading Workstation")
        parts = ["A Windows notification was shown."]
        for bot in holder:
            try:
                sent = bot.broadcast(f"{notice.title}\n{notice.text}")
                parts.append(f"Telegram: sent to {sent} chat(s).")
            except TelegramError as error:
                parts.append(f"Telegram: {error}")
        if not holder:
            parts.append("Telegram is off.")
        center.publish(Notice(EventKind.DAILY_REPORT, notice.text, f"test:{time.time()}"))
        return " ".join(parts)

    def make_report(period: Period) -> object:
        return reports.make(period)

    insights = Insights(
        dashboard=DashboardContext(
            execution=lambda: execution.snapshot,
            signals=lambda: pipeline.snapshot,
            risk=lambda: risk.snapshot,
            market=lambda: watch.snapshot,
            trades=trades,
        ),
        analytics=AnalyticsContext(
            trades=trades,
            balance=balance,
            export_dir=profile_dir,
            currency=currency,
            events=events,
            backtests=backtests,
            log=write,
        ),
        journal=JournalContext(
            trades=trades,
            journal=journal,
            reports=ReportRepository(storage.store, profile_dir / REPORT_FOLDER),
            make_report=make_report,
            offset=lambda: _offset(known_clock),
        ),
        notifications=NotificationsContext(
            source=settings,
            credentials=credentials,
            token_name=name,
            center=center,
            apply_bot=apply_bot,
            test=test,
        ),
        history=TradeHistory(trades=trades, detail=detail),
        center=center,
        watcher=watcher,
        reports=reports,
        bot_holder=holder,
    )
    apply_bot()
    return insights
