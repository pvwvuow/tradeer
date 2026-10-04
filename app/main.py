"""Composition root: parse arguments, run a self-test, or start the desktop app."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from app.cli import CliOptions, emit_report, parse_args, run_self_check, self_check_main
from app.core.paths import app_data_dir
from app.observability.categories import LogCategory
from app.observability.levels import LogLevel

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from app.backtest.engine import History
    from app.backtest.service import BacktestRequest
    from app.calendar.exporter import CalendarFileWatcher
    from app.calendar.store import CalendarStore
    from app.core.clock import BrokerClock
    from app.core.execution_settings import ExecutionSettingsSource
    from app.core.strategy_settings import StrategySettingsSource
    from app.engine.execution import ExecutionEngine
    from app.engine.go_live_desk import GoLiveDesk
    from app.engine.market_watch import MarketWatch
    from app.engine.signal_pipeline import SignalPipeline
    from app.ml.service import ModelService
    from app.mt5.api import MT5Api
    from app.mt5.checklist import ConnectRequest
    from app.mt5.connection import ConnectionService
    from app.mt5.gateway import MT5Gateway
    from app.observability.runtime import Observability
    from app.risk.risk_manager import RiskManager
    from app.risk.settings import RiskSettingsSource
    from app.storage.runtime import StorageRuntime
    from app.ui.backtest_page import BacktestContext
    from app.ui.health_page import HealthContext
    from app.ui.model_page import ModelContext
    from app.ui.updates_page import UpdatesContext
    from app.updates.state import LaunchTracker, StartInfo

APP_NAME = "MT5 Trading Workstation"
UI_HEARTBEAT_MS = 1000
UI_FREEZE_SECONDS = 10.0
GATEWAY_FREEZE_SECONDS = 120.0
SYNC_FREEZE_SECONDS = 600.0
MARKET_FREEZE_SECONDS = 180.0
HEALTH_FREEZE_SECONDS = 240.0
SMOKE_TEST_TIMEOUT_SECONDS = 180.0
BACKTEST_TIMEOUT_SECONDS = 6 * 3600.0
Emit = Callable[[str], None]


@dataclass(frozen=True)
class MarketParts:
    watch: MarketWatch
    calendar: CalendarStore
    calendar_file: CalendarFileWatcher
    pipeline: SignalPipeline
    settings: StrategySettingsSource
    risk: RiskManager
    risk_settings: RiskSettingsSource
    execution: ExecutionEngine
    execution_settings: ExecutionSettingsSource
    known_clock: Callable[[], BrokerClock | None]
    models: ModelService
    go_live: GoLiveDesk


@dataclass(frozen=True)
class Launch:
    """This run in the update history (spec J4): first run after an update, crash loop."""

    tracker: LaunchTracker
    start: StartInfo


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = parse_args(args)
    if options.self_check:
        return self_check_main(options)
    if options.crash_test:
        return crash_test_main(options)
    if options.mt5_smoke_test:
        return mt5_smoke_test_main(options)
    if options.mt5_trade_test:
        return mt5_trade_test_main(options)
    if options.backtest:
        return backtest_main(options)
    return run_gui(options, args)


def _has_console() -> bool:
    stream = sys.stderr
    return not getattr(sys, "frozen", False) and stream is not None and stream.isatty()


def crash_test_main(options: CliOptions) -> int:
    from app.observability.crash_test import run_crash_test
    from app.observability.logger import get_logger
    from app.observability.runtime import start_observability

    observability = start_observability(options.profile)
    try:
        log = get_logger(LogCategory.APP)
        result = run_crash_test(
            observability.crash_reporter,
            observability.masker,
            emit=partial(log.info, "{}"),
            flush=observability.pipeline.flush,
            log_dir=observability.log_dir,
        )
    finally:
        observability.shutdown()
    emit_report("\n".join(result.lines()), options.report_file)
    return 0 if result.ok else 1


def mt5_smoke_test_main(options: CliOptions) -> int:
    from app.mt5.smoke_test import run_smoke_test

    def test(mt5: MT5Api, request: ConnectRequest, emit: Emit, elevated: bool | None) -> bool:
        return run_smoke_test(mt5, request, emit, elevated=elevated)

    return _mt5_test_main(options, "Smoke test", "smoke_test", LogCategory.MT5, test)


def mt5_trade_test_main(options: CliOptions) -> int:
    from app.brokers.trade_test import run_trade_test

    def test(mt5: MT5Api, request: ConnectRequest, emit: Emit, elevated: bool | None) -> bool:
        return run_trade_test(mt5, request, options.symbol, emit, elevated=elevated)

    return _mt5_test_main(options, "Trade test", "trade_test", LogCategory.EXECUTION, test)


def backtest_main(options: CliOptions) -> int:
    from app.backtest.command import run_backtest_command

    def test(mt5: MT5Api, request: ConnectRequest, emit: Emit, elevated: bool | None) -> bool:
        return run_backtest_command(
            mt5,
            request,
            symbol=options.symbol,
            start=options.start,
            end=options.end,
            strategies=options.strategies,
            directory=app_data_dir(options.profile),
            emit=emit,
            elevated=elevated,
        )

    return _mt5_test_main(
        options,
        "Backtest",
        "backtest",
        LogCategory.BACKTEST,
        test,
        timeout=BACKTEST_TIMEOUT_SECONDS,
    )


def _mt5_test_main(
    options: CliOptions,
    title: str,
    name: str,
    category: LogCategory,
    test: Callable[[MT5Api, ConnectRequest, Emit, bool | None], bool],
    timeout: float = SMOKE_TEST_TIMEOUT_SECONDS,
) -> int:
    """Connect with the saved profile in the gateway thread and run one test command."""
    from app.core.credentials import CredentialError, KeyringStore, credential_name, read_password
    from app.core.profiles import load_account
    from app.mt5.errors import MT5Error
    from app.mt5.gateway import MT5Gateway, load_mt5
    from app.mt5.privileges import is_elevated
    from app.mt5.request_log import log_request
    from app.observability.logger import get_logger
    from app.observability.runtime import start_observability

    lines: list[str] = []
    observability = start_observability(options.profile)
    log = get_logger(category)
    mt5_log = get_logger(LogCategory.MT5)

    def emit(line: str) -> None:
        lines.append(line)
        log.info(f"{title}: {{}}", line)
        if category is not LogCategory.MT5:
            mt5_log.info(f"{title}: {{}}", line)

    ok = False
    try:
        account = load_account(app_data_dir(options.profile))
        password = ""
        if account.configured and account.login is not None:
            key = credential_name(options.profile, account.login, account.server)
            try:
                password = read_password(KeyringStore(), key) or ""
            except CredentialError as error:
                emit(f"Saved password not available ({error}); trying the terminal's login.")
        else:
            emit(
                f"No saved account in profile {options.profile!r}: using the account that is "
                "logged in to MT5 right now.",
            )
        request = account.request(password)
        elevated = is_elevated()
        gateway = MT5Gateway(load_mt5, on_request=log_request)
        gateway.start()
        try:
            ok = gateway.run(
                name,
                lambda mt5: test(mt5, request, emit, elevated),
                timeout=timeout,
            )
        except MT5Error as error:
            emit(f"[\u2717] {error.title}")
            if error.fix:
                emit(f"      Fix: {error.fix}")
            emit("Result: FAIL")
        finally:
            gateway.stop()
    finally:
        observability.shutdown()
    emit_report("\n".join(lines), options.report_file)
    return 0 if ok else 1


def run_gui(options: CliOptions, qt_args: list[str]) -> int:
    from app.core.single_instance import LOCK_FILE_NAME, InstanceLock
    from app.domain.config import TradingDefaults
    from app.observability.runtime import start_observability

    lock = InstanceLock(app_data_dir(options.profile) / LOCK_FILE_NAME)
    if not lock.acquire():
        return _already_running(options, qt_args)
    try:
        defaults = TradingDefaults().model_dump(mode="json")
        observability = start_observability(
            options.profile,
            console=_has_console(),
            startup_details={"defaults": defaults},
        )
        try:
            return _run_window(observability, options, qt_args)
        finally:
            observability.shutdown()
    finally:
        lock.release()


def _already_running(options: CliOptions, qt_args: list[str]) -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    message = (
        f"The app is already running for profile {options.profile!r}. Use that window, or start "
        "another profile with --profile NAME."
    )
    print(message, file=sys.stderr)
    application = QApplication([sys.argv[0], *qt_args])
    QMessageBox.information(None, APP_NAME, message)
    application.quit()
    return 1


def _start_connection(
    observability: Observability,
    profile: str,
) -> tuple[MT5Gateway, ConnectionService]:
    from app.core.credentials import CredentialError, KeyringStore, credential_name, read_password
    from app.core.profiles import load_account
    from app.mt5.connection import ConnectionService
    from app.mt5.gateway import MT5Gateway, load_mt5
    from app.mt5.privileges import is_elevated
    from app.mt5.request_log import log_event, log_request

    watchdog = observability.watchdog
    watchdog.register("mt5-gateway", GATEWAY_FREEZE_SECONDS)

    def helper_started(start: int, pid: int | None, version: str) -> None:
        how = "started" if start == 1 else f"restarted ({start} starts)"
        log_event("INFO", f"MT5 helper process {how}: pid {pid}, MetaTrader5 {version}")

    gateway = MT5Gateway(
        partial(load_mt5, on_start=helper_started),
        on_request=log_request,
        heartbeat=partial(watchdog.beat, "mt5-gateway"),
    )
    gateway.start()
    store = KeyringStore()

    def request() -> ConnectRequest:
        account = load_account(app_data_dir(profile))
        password = ""
        if account.login is not None:
            try:
                name = credential_name(profile, account.login, account.server)
                password = read_password(store, name) or ""
            except CredentialError as error:
                log_event("WARNING", f"Saved password not available: {error}")
        return account.request(password)

    service = ConnectionService(gateway, request, log=log_event, elevated=is_elevated())
    service.start_monitor()
    return gateway, service


def _run_window(observability: Observability, options: CliOptions, qt_args: list[str]) -> int:
    # Qt is imported lazily so that `--self-check` can report a broken Qt install.
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.observability.logger import get_logger
    from app.ui.qt_logging import install_qt_message_handler

    ui_log = get_logger(LogCategory.UI)
    reporter = observability.crash_reporter

    def log_qt(level: LogLevel, text: str, details: dict[str, object]) -> None:
        ui_log.bind(**details).log(level.name, "Qt: {}", text)

    def qt_fatal(text: str) -> None:
        reporter.report_message(f"Qt fatal error: {text}", source="qt", notify_user=False)
        observability.pipeline.flush()

    install_qt_message_handler(log_qt, on_fatal=qt_fatal)
    application = QApplication([sys.argv[0], *qt_args])
    application.setApplicationName(APP_NAME)
    font = QFont()
    font.setFamilies(["Inter", "Segoe UI Variable", "Segoe UI"])
    application.setFont(font)
    from app.__version__ import __version__
    from app.updates.state import LaunchTracker

    tracker = LaunchTracker(__version__)
    launch = Launch(tracker, tracker.start())
    if getattr(sys, "frozen", False):
        ok, report = run_self_check()
        if not ok:
            # A production build must fail loudly, never fall back to fake data (spec I2).
            ui_log.critical("Startup self-check failed:\n{}", report)
            if launch.start.previous:
                _offer_rollback(options.profile, launch.start, report)
            else:
                QMessageBox.critical(None, APP_NAME, report)
            return 1
    from app.storage.runtime import StorageError

    gateway, service = _start_connection(observability, options.profile)
    storage: StorageRuntime | None = None
    market: MarketParts | None = None
    try:
        storage = _open_storage(observability, options.profile, gateway, service)
        market = _start_market(observability, options.profile, gateway, service, storage)
        return _show_window(
            observability,
            options,
            application,
            gateway,
            service,
            storage,
            market,
            launch,
        )
    except StorageError as error:
        ui_log.critical("Local storage failed: {}", error)
        QMessageBox.critical(None, APP_NAME, str(error))
        return 1
    finally:
        if market is not None:
            market.watch.stop()
            observability.watchdog.unregister("market-analysis")
        service.stop_monitor()
        if storage is not None:
            storage.close()
            observability.watchdog.unregister("cloud-sync")
        gateway.stop()
        observability.watchdog.unregister("mt5-gateway")


def _updates_context(profile: str, start: StartInfo) -> UpdatesContext:
    from app.observability.logger import audit, get_logger
    from app.ui.updates_page import UpdatesContext
    from app.updates.service import UpdateService
    from app.updates.settings import UpdateSettingsSource
    from app.updates.velopack_backend import VelopackBackend

    log = get_logger(LogCategory.UPDATE)

    def write(level: str, message: str) -> None:
        log.log(level, "{}", message)

    def record(action: str, before: str, after: str) -> None:
        audit(action, before=before, after=after)

    settings = UpdateSettingsSource(app_data_dir(profile))
    if settings.note:
        write("WARNING", settings.note)
    backend = VelopackBackend()
    if backend.unavailable_reason():
        write("INFO", f"In-app updates off: {backend.unavailable_reason()}")
    service = UpdateService(backend, settings, start, log=write, record=record)
    return UpdatesContext(service, settings)


def _offer_rollback(profile: str, start: StartInfo, report: str) -> None:
    """The new version fails its self-check: offer the previous one (spec J2.6, J4)."""
    from PySide6.QtWidgets import QMessageBox

    from app.updates.service import Command

    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    text = f"{report}\n\nThis version does not work on this PC. Go back to {start.previous}?"
    if QMessageBox.critical(None, APP_NAME, text, buttons) != QMessageBox.StandardButton.Yes:
        return
    service = _updates_context(profile, start).service
    service.handle(Command.ROLLBACK)
    error = service.apply() if service.snapshot.ready else service.snapshot.message
    if error:
        QMessageBox.warning(None, APP_NAME, f"The rollback did not work: {error}")


def _open_storage(
    observability: Observability,
    profile: str,
    gateway: MT5Gateway,
    service: ConnectionService,
) -> StorageRuntime:
    """The local database, the cloud sync and the account tracker (spec E1)."""
    from app.__version__ import __version__
    from app.core.credentials import KeyringStore
    from app.core.execution_settings import load_execution_config
    from app.domain.config import TradingDefaults
    from app.mt5.history_sync import HistoryImporter
    from app.observability.context import SESSION_ID
    from app.observability.logger import get_logger
    from app.storage.runtime import open_storage
    from app.storage.supabase_client import SupabaseClient
    from app.storage.tracker import AccountTracker
    from app.strategies.registry import MAGIC_NUMBERS

    sync_log = get_logger(LogCategory.SYNC)

    def log(level: str, message: str) -> None:
        sync_log.log(level, "{}", message)

    watchdog = observability.watchdog
    storage = open_storage(
        profile,
        app_data_dir(profile),
        SESSION_ID,
        credentials=KeyringStore(),
        client_factory=SupabaseClient,
        log=log,
        heartbeat=partial(watchdog.beat, "cloud-sync"),
    )
    try:
        defaults = TradingDefaults()
        mode, _note = load_execution_config(app_data_dir(profile))
        storage.start_session(
            app_version=__version__,
            mode=mode.mode.value,
            settings=defaults.model_dump(mode="json"),
        )
        storage.attach_logs(observability.pipeline)
        storage.tracker = AccountTracker(
            storage.store,
            storage.session_id,
            status=lambda: service.status,
            history=HistoryImporter(
                gateway,
                storage.store,
                bot_magics=frozenset(MAGIC_NUMBERS.values()),
            ),
            log=log,
        )
        service.add_listener(storage.tracker.on_status)
        watchdog.register("cloud-sync", SYNC_FREEZE_SECONDS)
        storage.start()
    except BaseException:
        storage.close()
        raise
    return storage


def _start_market(
    observability: Observability,
    profile: str,
    gateway: MT5Gateway,
    service: ConnectionService,
    storage: StorageRuntime,
) -> MarketParts:
    """The closed-bar analysis thread, the calendar and the signals (spec C2, C3, C5)."""
    import json

    from app.brokers.live_broker import LiveBroker
    from app.brokers.market import GatewayMarket
    from app.brokers.paper_broker import ModeRiskBroker, PaperBroker, risk_account
    from app.calendar.exporter import CalendarFileWatcher
    from app.calendar.store import CalendarStore, import_exporter_file
    from app.core.clock import BrokerClock
    from app.core.execution_settings import ExecutionSettingsSource
    from app.core.strategy_settings import StrategySettingsSource
    from app.core.watchlist import WatchlistSource
    from app.domain.modes import OperatingMode
    from app.engine.execution import ExecutionEngine
    from app.engine.go_live_desk import GoLiveDesk
    from app.engine.go_live_gate import GoLiveSource, error_counts
    from app.engine.market_watch import MarketWatch
    from app.engine.signal_pipeline import SignalPipeline
    from app.mt5.market_data import MarketData
    from app.mt5.models import AccountKind, MarginMode
    from app.mt5.risk_reads import GatewayRiskBroker
    from app.observability.logger import audit, get_logger
    from app.risk.risk_manager import RiskManager
    from app.risk.settings import RiskSettingsSource
    from app.storage.backtest_store import BacktestRepository
    from app.storage.risk_store import RiskRepository
    from app.storage.signal_store import SignalRepository
    from app.storage.trade_store import TradeRepository
    from app.strategies.registry import MAGIC_NUMBERS, strategy_for_magic

    analysis_log = get_logger(LogCategory.ANALYSIS)
    strategy_log = get_logger(LogCategory.STRATEGY)
    risk_log = get_logger(LogCategory.RISK)
    store = storage.store
    calendar = CalendarStore(store)
    calendar_file = CalendarFileWatcher()

    def clock_key() -> str | None:
        account = service.status.account
        return f"broker_clock:{account.server}" if account is not None else None

    def load_clock() -> BrokerClock | None:
        key = clock_key()
        raw = store.get_state(key) if key is not None else None
        if raw is None:
            return None
        try:
            return BrokerClock.from_dict(json.loads(raw))
        except (ValueError, AttributeError):
            return None

    def save_clock(clock: BrokerClock) -> None:
        key = clock_key()
        if key is not None:
            store.set_state(key, json.dumps(clock.to_dict()))
        if storage.tracker is not None:
            storage.tracker.on_broker_clock()

    def refresh_calendar() -> None:
        message = import_exporter_file(calendar_file, calendar)
        if "did not change" not in message:
            analysis_log.info("{}", message)

    def log(level: str, message: str) -> None:
        analysis_log.log(level, "{}", message)

    def signal_log(level: str, message: str) -> None:
        strategy_log.log(level, "{}", message)

    settings = StrategySettingsSource(
        app_data_dir(profile),
        note=lambda text: signal_log("WARNING", text),
    )

    def log_risk(level: str, message: str) -> None:
        risk_log.log(level, "{}", message)

    risk_settings = RiskSettingsSource(
        app_data_dir(profile),
        note=lambda text: log_risk("WARNING", text),
    )
    execution_log = get_logger(LogCategory.EXECUTION)

    def log_execution(level: str, message: str) -> None:
        execution_log.log(level, "{}", message)

    execution_settings = ExecutionSettingsSource(
        app_data_dir(profile),
        note=lambda text: log_execution("WARNING", text),
    )

    def paper_mode() -> bool:
        return execution_settings.mode is OperatingMode.PAPER

    market_reads = GatewayMarket(gateway)
    trades = TradeRepository(store)
    paper = PaperBroker(
        market_reads,
        lambda: execution_settings.config.settings,
        store=trades,
        account=storage.current_account,
    )
    risk = RiskManager(
        lambda: risk_settings.config,
        ModeRiskBroker(
            GatewayRiskBroker(gateway, strategy_for_magic),
            paper,
            paper_mode,
            strategy_for_magic,
        ),
        store=RiskRepository(store),
        account=lambda: risk_account(storage.current_account(), paper_mode()),
        connected=lambda: service.status.connected,
        log=log_risk,
    )
    log_risk("INFO", f"Risk profile: {risk_settings.config.profile_title()}")

    def netting() -> bool:
        account = service.status.account
        return account is not None and account.margin_mode is MarginMode.NETTING

    def broker_symbol(name: str) -> str:
        return watch.broker_symbol(name)

    def real_account() -> bool:
        account = service.status.account
        return account is None or account.kind is not AccountKind.DEMO

    def record(action: str, before: Any, after: Any) -> None:
        audit(action, before=before, after=after)

    def errors(now: float, days: int) -> tuple[int, int]:
        return error_counts(store.db, now, days)

    go_live = GoLiveDesk(
        gate=GoLiveSource(app_data_dir(profile)),
        strategies=settings,
        risk=lambda: risk_settings.config,
        account=storage.current_account,
        real=real_account,
        runs=BacktestRepository(store).recent,
        errors=errors,
        record=record,
    )
    execution = ExecutionEngine(
        lambda: execution_settings.config,
        market=market_reads,
        paper=paper,
        live=LiveBroker(
            gateway,
            retries=lambda: execution_settings.config.settings.max_retries,
            deviation=lambda: execution_settings.config.settings.deviation_points,
        ),
        magics=MAGIC_NUMBERS,
        risk=risk,
        store=trades,
        account=storage.current_account,
        clock=lambda: watch.clock,
        connected=lambda: service.status.connected,
        netting=netting,
        broker_symbol=broker_symbol,
        stop_trading=risk.request_stop,
        paper_step=paper.step,
        auto_gate=go_live.guard,
        log=log_execution,
    )
    log_execution("INFO", f"Trading mode: {execution_settings.mode.label}")
    if execution_settings.mode is OperatingMode.AUTO:
        log_execution("WARNING", "Auto mode: signals are sent without a click (Go-Live gate)")
    models = _model_service(profile, storage)
    pipeline = SignalPipeline(
        settings.strategies,
        settings.filters,
        store=SignalRepository(store),
        account=storage.current_account,
        risk=risk,
        executor=execution,
        model=models.current,
        log=signal_log,
    )
    loaded = pipeline.load()
    enabled = ", ".join(settings.settings.enabled()) or "none"
    signal_log("INFO", f"Strategies on: {enabled}; {loaded} saved signals loaded")

    watchdog = observability.watchdog
    watchdog.register("market-analysis", MARKET_FREEZE_SECONDS)
    watch = MarketWatch(
        MarketData(gateway, BrokerClock.assumed()),
        symbols=WatchlistSource(app_data_dir(profile)),
        connected=lambda: service.status.connected,
        events=calendar.recent_and_upcoming,
        refresh_calendar=refresh_calendar,
        load_clock=load_clock,
        save_clock=save_clock,
        log=log,
        heartbeat=partial(watchdog.beat, "market-analysis"),
        signals=pipeline,
    )

    def known_clock() -> BrokerClock | None:
        clock = watch.clock
        return clock if clock.measured else load_clock()

    if storage.tracker is not None:
        storage.tracker.clock_source = known_clock
    risk.clock_source = lambda: watch.clock
    watch.start()
    return MarketParts(
        watch,
        calendar,
        calendar_file,
        pipeline,
        settings,
        risk,
        risk_settings,
        execution,
        execution_settings,
        known_clock,
        models,
        go_live,
    )


def _model_service(profile: str, storage: StorageRuntime) -> ModelService:
    """The model registry in `<profile>/models` and the active model (spec C10)."""
    from app.ml.registry import FOLDER, ModelRegistry
    from app.ml.service import ModelService
    from app.observability.logger import audit, get_logger
    from app.storage.signal_store import SignalRepository

    ml_log = get_logger(LogCategory.ML)

    def write(level: str, message: str) -> None:
        ml_log.log(level, "{}", message)

    def record(action: str, before: str, after: str) -> None:
        audit(action, before=before, after=after)

    store = storage.store
    registry = ModelRegistry(store, app_data_dir(profile) / FOLDER, account=storage.current_account)
    return ModelService(registry, SignalRepository(store), log=write, audit=record)


def _model_context(
    profile: str,
    service: ConnectionService,
    market: MarketParts,
    backtest: BacktestContext,
) -> ModelContext:
    """The Model page: training reads history like the Backtest page (spec C10)."""
    from datetime import UTC, datetime, timedelta

    from app.backtest.service import BacktestRequest
    from app.core.watchlist import WatchlistSource
    from app.observability.logger import get_logger
    from app.ui.model_page import ModelContext, Note

    ml_log = get_logger(LogCategory.ML)

    def write(level: str, message: str) -> None:
        ml_log.log(level, "{}", message)

    def load(symbol: str, start: float, end: float, note: Note) -> History:
        first = datetime.fromtimestamp(start, UTC).date()
        last = datetime.fromtimestamp(end, UTC).date() - timedelta(days=1)
        request = BacktestRequest(symbol=symbol, start=first, end=last)
        history, notes = backtest.load(request, note)
        for text in notes:
            write("WARNING", f"Training history {symbol}: {text}")
        return history

    return ModelContext(
        service=market.models,
        load=load,
        strategies=market.settings,
        risk=market.risk_settings,
        symbols=WatchlistSource(app_data_dir(profile)),
        connected=lambda: service.status.connected,
        events=lambda start, end: market.calendar.between(int(start), int(end)),
        log=write,
    )


def _backtest_context(
    profile: str,
    gateway: MT5Gateway,
    service: ConnectionService,
    storage: StorageRuntime,
    market: MarketParts,
) -> BacktestContext:
    """The Backtest page's history loader and settings (spec C8, F3 page 7)."""
    from app.backtest.history import CACHE_FOLDER, load_history, resolve_broker_symbol
    from app.core.clock import BrokerClock
    from app.core.watchlist import WatchlistSource
    from app.observability.logger import get_logger
    from app.storage.backtest_store import BacktestRepository
    from app.ui.backtest_page import BacktestContext, Note

    backtest_log = get_logger(LogCategory.BACKTEST)
    cache = app_data_dir(profile) / CACHE_FOLDER

    def write(level: str, message: str) -> None:
        backtest_log.log(level, "{}", message)

    def load(request: BacktestRequest, note: Note) -> tuple[History, tuple[str, ...]]:
        clock = market.known_clock() or BrokerClock.assumed()
        broker = resolve_broker_symbol(gateway, request.symbol)
        write("INFO", f"Backtest history {request.symbol} ({broker}) {request.period}")
        history, report = load_history(
            gateway,
            request.symbol,
            broker,
            request.utc_start,
            request.utc_end,
            clock,
            cache=cache,
            note=note,
        )
        return history, report.notes

    return BacktestContext(
        load=load,
        strategies=market.settings,
        risk=market.risk_settings,
        execution=market.execution_settings,
        symbols=WatchlistSource(app_data_dir(profile)),
        connected=lambda: service.status.connected,
        events=lambda start, end: market.calendar.between(int(start), int(end)),
        runs=BacktestRepository(storage.store),
        account=storage.current_account,
        log=write,
    )


def _health_context(
    observability: Observability,
    profile: str,
    service: ConnectionService,
    storage: StorageRuntime,
    market: MarketParts,
) -> HealthContext:
    """The health monitor (spec E3): reads snapshots only, never calls MT5 itself."""
    import shutil
    import time

    from app.core.clock import fx_weekend
    from app.engine.health_monitor import HealthMonitor, folder_size
    from app.observability.health import HealthInputs
    from app.observability.logger import get_logger
    from app.storage.health_store import HealthRepository
    from app.ui.health_page import HealthContext

    health_log = get_logger(LogCategory.APP)
    watchdog = observability.watchdog
    repository = HealthRepository(storage.store, storage.current_account)
    directory = app_data_dir(profile)

    def write(level: str, message: str) -> None:
        health_log.log(level, "{}", message)

    def disk_free() -> float | None:
        try:
            return float(shutil.disk_usage(directory).free)
        except OSError:
            return None

    def collect() -> HealthInputs:
        now = time.time()
        status = service.status
        terminal = status.terminal
        clock = market.watch.clock
        quotes = list(market.watch.snapshot.quotes.values())
        times = [
            float(clock.to_utc(quote.server_time))
            for quote in quotes
            if quote.valid and quote.server_time > 0
        ]
        sync = storage.worker.engine.status
        counts = storage.store.outbox_counts()
        return HealthInputs(
            now=now,
            mt5_state=status.state.value,
            algo_trading=terminal.trade_allowed if terminal is not None else None,
            trade_api_disabled=terminal.tradeapi_disabled if terminal is not None else False,
            ping_ms=terminal.ping_ms if terminal is not None else None,
            quote_times=times,
            market_closed=fx_weekend(now),
            clock_measured=clock.measured,
            clock_text=clock.text(),
            clock_changes=[float(change.utc_time) for change in clock.changes],
            sync_state=sync.state.value,
            sync_message=sync.message,
            pending=counts.pending,
            failed=counts.failed,
            disk_free_bytes=disk_free(),
            log_bytes=folder_size(observability.log_dir),
            workers=watchdog.statuses(),
        )

    def history(limit: int) -> Sequence[Any]:
        return repository.recent(limit, problems_only=True)

    watchdog.register("health", HEALTH_FREEZE_SECONDS)
    monitor = HealthMonitor(
        collect,
        save=repository.record,
        log=write,
        heartbeat=partial(watchdog.beat, "health"),
    )
    return HealthContext(monitor, watchdog.statuses, history)


def _show_window(
    observability: Observability,
    options: CliOptions,
    application: QApplication,
    gateway: MT5Gateway,
    service: ConnectionService,
    storage: StorageRuntime,
    market: MarketParts,
    launch: Launch,
) -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QMessageBox

    from app.core.credentials import KeyringStore
    from app.core.profiles import load_account
    from app.core.ui_prefs import load_prefs
    from app.mt5.models import AccountKind
    from app.mt5.privileges import is_elevated
    from app.observability.logger import get_logger
    from app.ui.connection_page import ConnectionContext, launch_profile_instance
    from app.ui.crash_dialog import CrashNotifier
    from app.ui.insights import build_insights
    from app.ui.main_window import MainWindow
    from app.ui.market_page import MarketContext
    from app.ui.positions_page import TradingContext
    from app.ui.risk_page import RiskContext
    from app.ui.signals_page import SignalsContext
    from app.updates.state import HEALTHY_SECONDS

    ui_log = get_logger(LogCategory.UI)
    reporter = observability.crash_reporter
    prefs_dir = app_data_dir(options.profile)

    def data_path() -> str:
        terminal = service.status.terminal
        return terminal.data_path if terminal is not None else ""

    def real_account() -> bool:
        account = service.status.account
        return account is None or account.kind is not AccountKind.DEMO

    watch, calendar, calendar_file = market.watch, market.calendar, market.calendar_file
    try:
        backtest = _backtest_context(options.profile, gateway, service, storage, market)
        context = ConnectionContext(
            profile=options.profile,
            profile_dir=prefs_dir,
            gateway=gateway,
            service=service,
            credentials=KeyringStore(),
            elevated=is_elevated(),
            launch_profile=launch_profile_instance,
        )
        insights = build_insights(
            profile=options.profile,
            profile_dir=prefs_dir,
            storage=storage,
            service=service,
            execution=market.execution,
            pipeline=market.pipeline,
            risk=market.risk,
            watch=watch,
            calendar=calendar,
            known_clock=market.known_clock,
            credentials=context.credentials,
            logs=observability.pipeline,
        )
        updates = _updates_context(options.profile, launch.start)
        health = _health_context(observability, options.profile, service, storage, market)
        if insights.dashboard.trades is not None:
            market.go_live.trades = insights.dashboard.trades
        insights.dashboard.go_live = market.go_live.readiness
        trading = TradingContext(
            market.execution,
            market.execution_settings,
            real_account,
            insights.history,
            auto_check=market.go_live.auto_check,
        )
        window = MainWindow(
            load_prefs(prefs_dir),
            prefs_dir,
            observability.controls,
            context,
            storage,
            MarketContext(watch, prefs_dir, calendar, calendar_file, data_path),
            SignalsContext(market.pipeline, market.settings),
            RiskContext(market.risk, market.risk_settings),
            trading,
            backtest,
            _model_context(options.profile, service, market, backtest),
            insights.dashboard,
            insights.analytics,
            insights.journal,
            insights.notifications,
            updates,
            health,
        )
        insights.attach(window)
        window.strategies_page.attach_go_live(market.go_live)
    except Exception as error:
        reporter.report_exception(type(error), error, error.__traceback__, source="startup")
        QMessageBox.critical(None, APP_NAME, "The app could not start. A crash report was saved.")
        return 1
    notifier = CrashNotifier()
    notifier.crashed.connect(
        window.show_crash_dialog,
        type=Qt.ConnectionType.QueuedConnection,
    )
    reporter.add_listener(notifier.notify)
    reporter.set_state_provider(window.crash_state)
    observability.watchdog.register("ui", UI_FREEZE_SECONDS)
    heartbeat = QTimer(window)
    heartbeat.timeout.connect(partial(observability.watchdog.beat, "ui"))
    heartbeat.start(UI_HEARTBEAT_MS)
    window.show()
    ui_log.info("Main window shown")
    updates.service.start()
    health.monitor.start()
    QTimer.singleShot(int(HEALTHY_SECONDS * 1000), launch.tracker.mark_healthy)
    account = load_account(prefs_dir)
    if account.configured and account.auto_connect and window.connection_page is not None:
        ui_log.info("Connecting automatically to the saved account")
        window.connection_page.connect_to_mt5()
    try:
        code = int(application.exec())
        launch.tracker.mark_healthy()
        return code
    finally:
        health.monitor.stop()
        observability.watchdog.unregister("health")
        updates.service.stop()
        insights.stop()
        heartbeat.stop()
        observability.watchdog.unregister("ui")
        reporter.remove_listener(notifier.notify)
        reporter.set_state_provider(None)


if __name__ == "__main__":
    raise SystemExit(main())
