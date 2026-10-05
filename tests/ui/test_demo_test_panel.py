"""Health > Demo test in the window: the steps show while it runs, the report is saved and
its folder opens; without the app's services the button is off."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from pytestqt.qtbot import QtBot

from app.brokers.demo_test import DemoReport, Outcome, Step
from app.engine.health_monitor import HealthMonitor
from app.observability.health import HealthInputs
from app.ui.demo_test_panel import ALL_SYMBOLS, NO_CONTEXT, DemoTestContext, DemoTestPanel
from app.ui.health_page import HealthContext, HealthPage

NOW = 1_791_200_000.0
STEPS = (
    Step("", "MT5 and the demo account", Outcome.PASS, "DEMO account at Demo Broker Ltd", 0.1),
    Step("XAUUSD", "Market buy with SL and TP", Outcome.PASS, "position 70000001", 0.3),
    Step("XAUUSD", "Breakout pair", Outcome.SKIP, "the price did not reach either side", 90.0),
)


class Runner:
    def __init__(self) -> None:
        self.symbols: list[str] = []
        self.stopped = False

    def run(
        self,
        symbols: Sequence[str],
        on_step: Callable[[Step], None] | None = None,
    ) -> DemoReport:
        self.symbols = list(symbols)
        for step in STEPS:
            if on_step is not None:
                on_step(step)
        return DemoReport(NOW, NOW + 120.0, "DEMO at Demo Broker Ltd", STEPS)

    def stop(self) -> None:
        self.stopped = True


def context(tmp_path: Path, runner: Runner) -> DemoTestContext:
    return DemoTestContext(lambda: runner, lambda: ["EURUSD", "XAUUSD"], tmp_path / "reports")


def choose(panel: DemoTestPanel, text: str) -> None:
    panel.symbol_box.setCurrentIndex(panel.symbol_box.findText(text))


def test_without_services_the_button_is_off(qtbot: QtBot) -> None:
    panel = DemoTestPanel(None)
    qtbot.addWidget(panel)
    assert not panel.run_button.isEnabled() and not panel.start()
    assert panel.status.text() == NO_CONTEXT


def test_a_run_shows_the_steps_and_saves_the_report(qtbot: QtBot, tmp_path: Path) -> None:
    runner = Runner()
    panel = DemoTestPanel(context(tmp_path, runner))
    qtbot.addWidget(panel)
    opened: list[Path] = []
    panel.open_folder = opened.append
    names = [panel.symbol_box.itemText(index) for index in range(panel.symbol_box.count())]
    assert names == ["EURUSD", "XAUUSD", ALL_SYMBOLS]
    choose(panel, "XAUUSD")
    assert panel.start() and not panel.run_button.isEnabled()
    qtbot.waitUntil(lambda: panel.last_report is not None, timeout=5000)
    assert runner.symbols == ["XAUUSD"]
    assert panel.table.rowCount() == 3
    assert panel.table.item(0, 0).text() == "MT5"
    assert panel.table.item(1, 2).text() == "\u2713 Pass"
    assert panel.table.item(2, 2).text() == "\u25cb Skipped"
    text = panel.status.text()
    assert text.startswith("Demo test INCOMPLETE: 2 passed, 0 failed, 1 skipped in 2.0 min")
    assert panel.last_path is not None and panel.last_path.is_file()
    assert f"Report saved: {panel.last_path}" in text
    assert panel.run_button.isEnabled() and not panel.stop_button.isEnabled()
    panel.folder_button.click()
    assert opened == [tmp_path / "reports"]


def test_all_watched_symbols_run_one_after_the_other(qtbot: QtBot, tmp_path: Path) -> None:
    runner = Runner()
    panel = DemoTestPanel(context(tmp_path, runner))
    qtbot.addWidget(panel)
    choose(panel, ALL_SYMBOLS)
    assert panel.start()
    qtbot.waitUntil(lambda: panel.last_report is not None, timeout=5000)
    assert runner.symbols == ["EURUSD", "XAUUSD"]


def test_a_run_that_breaks_says_why(qtbot: QtBot, tmp_path: Path) -> None:
    class Broken(Runner):
        def run(
            self,
            symbols: Sequence[str],
            on_step: Callable[[Step], None] | None = None,
        ) -> DemoReport:
            raise OSError("MT5 helper gone")

    panel = DemoTestPanel(DemoTestContext(Broken, lambda: ["EURUSD"], tmp_path))
    qtbot.addWidget(panel)
    assert panel.start()
    qtbot.waitUntil(lambda: panel.run_button.isEnabled(), timeout=5000)
    assert panel.status.text() == "The demo test could not run: OSError: MT5 helper gone"
    assert panel.last_report is None


def inputs() -> HealthInputs:
    return HealthInputs(
        now=NOW,
        mt5_state="connected",
        algo_trading=True,
        ping_ms=35.0,
        quote_times=(NOW - 3,),
        clock_measured=True,
        sync_state="up_to_date",
        disk_free_bytes=1e12,
        log_bytes=1e6,
        workers=(),
    )


def test_the_health_page_carries_the_demo_test(qtbot: QtBot, tmp_path: Path) -> None:
    monitor = HealthMonitor(inputs, clock=lambda: NOW)
    page = HealthPage(HealthContext(monitor, demo_test=context(tmp_path, Runner())))
    qtbot.addWidget(page)
    assert page.demo_panel.run_button.isEnabled()
    bare = HealthPage(HealthContext(monitor))
    qtbot.addWidget(bare)
    assert not bare.demo_panel.run_button.isEnabled()
