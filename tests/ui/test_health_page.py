"""The Health page (spec F3 page 13): checks, workers and recent issues, read-only."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.ui_prefs import UiPrefs
from app.engine.health_monitor import HealthMonitor
from app.observability.health import HealthInputs
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


def context() -> HealthContext:
    monitor = HealthMonitor(inputs, clock=lambda: NOW)
    saved = (SavedCheck(NOW - 60, "algo_trading", "warning", None, "It is off"),)
    return HealthContext(monitor, lambda: WORKERS, lambda limit: saved[:limit])


def test_without_services_it_says_so(qtbot: QtBot) -> None:
    page = HealthPage(None)
    qtbot.addWidget(page)
    assert not page.check_button.isEnabled()
    assert "start with the app's services" in page.summary.text()
    assert page.checks.rowCount() == 0


def test_before_the_first_check(qtbot: QtBot) -> None:
    page = HealthPage(context())
    qtbot.addWidget(page)
    assert page.status_chip.text() == "Not checked"
    assert "first check" in page.summary.text()
    assert page.workers.rowCount() == 2


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


def test_the_main_window_shows_the_health_page(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(), tmp_path)
    qtbot.addWidget(window)
    window.show_page("health")
    assert isinstance(window.pages.currentWidget(), HealthPage)
    assert window.health_page.context is None
