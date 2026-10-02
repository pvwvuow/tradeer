"""Composition root: parse arguments, run a self-test, or start the desktop app."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING

from app.cli import CliOptions, emit_report, parse_args, run_self_check, self_check_main
from app.core.paths import app_data_dir
from app.observability.categories import LogCategory
from app.observability.levels import LogLevel

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from app.calendar.exporter import CalendarFileWatcher
    from app.calendar.store import CalendarStore
    from app.engine.market_watch import MarketWatch
    from app.mt5.checklist import ConnectRequest
    from app.mt5.connection import ConnectionService
    from app.mt5.gateway import MT5Gateway
    from app.observability.runtime import Observability
    from app.storage.runtime import StorageRuntime

APP_NAME = "MT5 Trading Workstation"
UI_HEARTBEAT_MS = 1000
UI_FREEZE_SECONDS = 10.0
GATEWAY_FREEZE_SECONDS = 120.0
SYNC_FREEZE_SECONDS = 600.0
MARKET_FREEZE_SECONDS = 180.0
SMOKE_TEST_TIMEOUT_SECONDS = 180.0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = parse_args(args)
    if options.self_check:
        return self_check_main(options)
    if options.crash_test:
        return crash_test_main(options)
    if options.mt5_smoke_test:
        return mt5_smoke_test_main(options)
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
    from app.core.credentials import CredentialError, KeyringStore, credential_name, read_password
    from app.core.profiles import load_account
    from app.mt5.errors import MT5Error
    from app.mt5.gateway import MT5Gateway, load_mt5
    from app.mt5.privileges import is_elevated
    from app.mt5.request_log import log_request
    from app.mt5.smoke_test import run_smoke_test
    from app.observability.logger import get_logger
    from app.observability.runtime import start_observability

    lines: list[str] = []
    observability = start_observability(options.profile)
    log = get_logger(LogCategory.MT5)

    def emit(line: str) -> None:
        lines.append(line)
        log.info("Smoke test: {}", line)

    ok = False
    try:
        account = load_account(app_data_dir(options.profile))
        password = ""
        if account.configured and account.login is not None:
            name = credential_name(options.profile, account.login, account.server)
            try:
                password = read_password(KeyringStore(), name) or ""
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
                "smoke_test",
                lambda mt5: run_smoke_test(mt5, request, emit, elevated=elevated),
                timeout=SMOKE_TEST_TIMEOUT_SECONDS,
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
    gateway = MT5Gateway(
        load_mt5,
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
    if getattr(sys, "frozen", False):
        ok, report = run_self_check()
        if not ok:
            # A production build must fail loudly, never fall back to fake data (spec I2).
            ui_log.critical("Startup self-check failed:\n{}", report)
            QMessageBox.critical(None, APP_NAME, report)
            return 1
    from app.storage.runtime import StorageError

    gateway, service = _start_connection(observability, options.profile)
    storage: StorageRuntime | None = None
    market: tuple[MarketWatch, CalendarStore, CalendarFileWatcher] | None = None
    try:
        storage = _open_storage(observability, options.profile, gateway, service)
        market = _start_market(observability, options.profile, gateway, service, storage)
        return _show_window(observability, options, application, gateway, service, storage, market)
    except StorageError as error:
        ui_log.critical("Local storage failed: {}", error)
        QMessageBox.critical(None, APP_NAME, str(error))
        return 1
    finally:
        if market is not None:
            market[0].stop()
            observability.watchdog.unregister("market-analysis")
        service.stop_monitor()
        if storage is not None:
            storage.close()
            observability.watchdog.unregister("cloud-sync")
        gateway.stop()
        observability.watchdog.unregister("mt5-gateway")


def _open_storage(
    observability: Observability,
    profile: str,
    gateway: MT5Gateway,
    service: ConnectionService,
) -> StorageRuntime:
    """The local database, the cloud sync and the account tracker (spec E1)."""
    from app.__version__ import __version__
    from app.core.credentials import KeyringStore
    from app.domain.config import TradingDefaults
    from app.mt5.history_sync import HistoryImporter
    from app.observability.context import SESSION_ID
    from app.observability.logger import get_logger
    from app.storage.runtime import open_storage
    from app.storage.supabase_client import SupabaseClient
    from app.storage.tracker import AccountTracker

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
        storage.start_session(
            app_version=__version__,
            mode=defaults.mode.value,
            settings=defaults.model_dump(mode="json"),
        )
        storage.attach_logs(observability.pipeline)
        storage.tracker = AccountTracker(
            storage.store,
            storage.session_id,
            status=lambda: service.status,
            history=HistoryImporter(gateway, storage.store),
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
) -> tuple[MarketWatch, CalendarStore, CalendarFileWatcher]:
    """The closed-bar analysis thread and the calendar (spec C2, C3)."""
    import json

    from app.calendar.exporter import CalendarFileWatcher
    from app.calendar.store import CalendarStore, import_exporter_file
    from app.core.clock import BrokerClock
    from app.core.watchlist import WatchlistSource
    from app.engine.market_watch import MarketWatch
    from app.mt5.market_data import MarketData
    from app.observability.logger import get_logger

    analysis_log = get_logger(LogCategory.ANALYSIS)
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

    def refresh_calendar() -> None:
        message = import_exporter_file(calendar_file, calendar)
        if "did not change" not in message:
            analysis_log.info("{}", message)

    def log(level: str, message: str) -> None:
        analysis_log.log(level, "{}", message)

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
    )
    watch.start()
    return watch, calendar, calendar_file


def _show_window(
    observability: Observability,
    options: CliOptions,
    application: QApplication,
    gateway: MT5Gateway,
    service: ConnectionService,
    storage: StorageRuntime,
    market: tuple[MarketWatch, CalendarStore, CalendarFileWatcher],
) -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QMessageBox

    from app.core.credentials import KeyringStore
    from app.core.profiles import load_account
    from app.core.ui_prefs import load_prefs
    from app.mt5.privileges import is_elevated
    from app.observability.logger import get_logger
    from app.ui.connection_page import ConnectionContext, launch_profile_instance
    from app.ui.crash_dialog import CrashNotifier
    from app.ui.main_window import MainWindow
    from app.ui.market_page import MarketContext

    ui_log = get_logger(LogCategory.UI)
    reporter = observability.crash_reporter
    prefs_dir = app_data_dir(options.profile)

    def data_path() -> str:
        terminal = service.status.terminal
        return terminal.data_path if terminal is not None else ""

    watch, calendar, calendar_file = market
    try:
        context = ConnectionContext(
            profile=options.profile,
            profile_dir=prefs_dir,
            gateway=gateway,
            service=service,
            credentials=KeyringStore(),
            elevated=is_elevated(),
            launch_profile=launch_profile_instance,
        )
        window = MainWindow(
            load_prefs(prefs_dir),
            prefs_dir,
            observability.controls,
            context,
            storage,
            MarketContext(watch, prefs_dir, calendar, calendar_file, data_path),
        )
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
    account = load_account(prefs_dir)
    if account.configured and account.auto_connect and window.connection_page is not None:
        ui_log.info("Connecting automatically to the saved account")
        window.connection_page.connect_to_mt5()
    try:
        return int(application.exec())
    finally:
        heartbeat.stop()
        observability.watchdog.unregister("ui")
        reporter.remove_listener(notifier.notify)
        reporter.set_state_provider(None)


if __name__ == "__main__":
    raise SystemExit(main())
