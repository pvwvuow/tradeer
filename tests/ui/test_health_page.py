"""The Health page (spec F3 page 13): checks, performance, workers, recent issues and the
debug bundle."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.ui_prefs import UiPrefs
from app.engine.health_monitor import HealthMonitor
from app.engine.perf_monitor import PerfMonitor
from app.observability.debug_bundle import BundleResult
from app.observability.health import HealthInputs
from app.observability.metrics import MB, PerfInputs
from app.observability.watchdog import WorkerStatus
from app.storage.health_store import SavedCheck
from app.ui.health_page import HealthContext, HealthPage
from app.ui.main_window import MainWindow

NOW = 1_790_000_000.0
WORKERS = (
    WorkerStatus("ui", 0.5, 10.0, False, 0),
    WorkerStatus("cloud-sync", 900.0, 600.0, True, 2),
)


def inputs() -> HealthInputs:
    return HealthInputs(
        now=NOW,
        mt5_state="connected",
        algo_trading=False,
        ping_ms=35.0,
        quote_times=(NOW - 3,),
        clock_measured=True,
        sync_state="up_to_date",
        disk_free_bytes=1e12,
        log_bytes=1e6,
        workers=WORKERS,
    )


def measure() -> PerfInputs:
    return PerfInputs(
        now=NOW,
        cpu_percent=1.2,
        memory_bytes=700 * MB,
        mt5_ms=[10.0, 30.0],
        bar_cycles=[(250.0, 3)],
        mt5_queue=0,
        sync_queue=4,
        startup_seconds=3.0,
    )


def context(bundle: object = None) -> HealthContext:
    perf = PerfMonitor(measure, clock=lambda: NOW)
    monitor = HealthMonitor(inputs, clock=lambda: NOW, after=(perf.run_once,))
    saved = (SavedCheck(NOW - 60, "algo_trading", "warning", None, "It is off"),)
    return HealthContext(
        monitor,
        lambda: WORKERS,
        lambda limit: saved[:limit],
        perf,
        bundle,  # type: ignore[arg-type]
    )


def test_without_services_it_says_so(qtbot: QtBot) -> None:
    page = HealthPage(None)
    qtbot.addWidget(page)
    assert not page.check_button.isEnabled() and not page.bundle_button.isEnabled()
    assert "start with the app's services" in page.summary.text()
    assert page.checks.rowCount() == 0 and page.metrics.rowCount() == 0


def test_before_the_first_check(qtbot: QtBot) -> None:
    page = HealthPage(context())
    qtbot.addWidget(page)
    assert page.status_chip.text() == "Not checked"
    assert "first check" in page.summary.text()
    assert page.workers.rowCount() == 2
    assert "Measured with every health check" in page.perf_summary.text()
    assert not page.bundle_button.isEnabled()  # no bundle builder in this context


def test_check_now_fills_the_tables(qtbot: QtBot) -> None:
    page = HealthPage(context())
    qtbot.addWidget(page)
    page.now = lambda: NOW + 5
    page.check_now()  # the monitor is not started in tests: it runs inline
    page.refresh()
    assert page.checks.rowCount() == 11
    rows = {page.checks.item(row, 0).text(): row for row in range(page.checks.rowCount())}
    algo = rows["Algo Trading on"]
    assert page.checks.item(algo, 1).text() == "Warning"
    assert "Press Algo Trading" in page.checks.item(algo, 3).text()
    workers = rows["Background workers"]
    assert page.checks.item(workers, 1).text() == "Problem"
    assert page.status_chip.text() == "Problem"
    assert "Last check 5 s ago" in page.summary.text()
    assert page.workers.item(0, 0).text() == "cloud-sync"
    assert page.workers.item(0, 1).text() == "Not responding"
    assert page.history.rowCount() == 1 and page.history.item(0, 1).text() == "algo_trading"


def test_the_performance_table_shows_the_budgets(qtbot: QtBot) -> None:
    page = HealthPage(context())
    qtbot.addWidget(page)
    page.check_now()
    page.refresh()
    assert page.metrics.rowCount() == 9
    rows = {page.metrics.item(row, 0).text(): row for row in range(page.metrics.rowCount())}
    memory = rows["Memory"]
    assert page.metrics.item(memory, 1).text() == "Over budget"
    assert page.metrics.item(memory, 2).text() == "700 MB"
    assert page.metrics.item(memory, 3).text() == "< 500 MB"
    assert page.metrics.item(rows["CPU"], 1).text() == "Within budget"
    assert "1 over budget: Memory" in page.perf_summary.text()


def test_the_debug_bundle_is_created_in_the_background(qtbot: QtBot, tmp_path: Path) -> None:
    target = tmp_path / "debug" / "debug-x.zip"

    def build() -> BundleResult:
        target.parent.mkdir()
        target.write_bytes(b"PK")
        return BundleResult(target, ("README_DEBUG.md",))

    page = HealthPage(context(build))
    qtbot.addWidget(page)
    opened: list[Path] = []
    page.open_folder = opened.append
    assert page.bundle_button.isEnabled() and not page.folder_button.isEnabled()
    assert page.create_bundle()
    qtbot.waitUntil(lambda: page.bundle_button.isEnabled(), timeout=5000)
    assert page.last_bundle == target
    assert page.bundle_status.text().startswith(f"Debug bundle saved: {target}")
    assert page.folder_button.isEnabled()
    page.folder_button.click()
    assert opened == [target.parent]


def test_a_failing_bundle_says_why(qtbot: QtBot) -> None:
    def build() -> BundleResult:
        raise OSError("disk full")

    page = HealthPage(context(build))
    qtbot.addWidget(page)
    assert page.create_bundle()
    qtbot.waitUntil(lambda: page.bundle_button.isEnabled(), timeout=5000)
    assert "could not be created: OSError: disk full" in page.bundle_status.text()
    assert page.last_bundle is None and not page.folder_button.isEnabled()


def test_the_main_window_shows_the_health_page(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(), tmp_path)
    qtbot.addWidget(window)
    window.show_page("health")
    assert isinstance(window.pages.currentWidget(), HealthPage)
    assert window.health_page.context is None
