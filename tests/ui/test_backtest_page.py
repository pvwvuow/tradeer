"""The Backtest page (spec F3 page 7): the form, a run in the background thread, the result
tabs, the heatmap and the saved runs."""

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from pytestqt.qtbot import QtBot

from app.backtest.service import BacktestRequest, SensitivityOptions, run_report
from app.core.execution_settings import ExecutionConfig, ExecutionSettingsSource
from app.core.strategy_settings import StrategySettings, StrategySettingsSource
from app.risk.settings import RiskConfig, RiskSettingsSource
from app.storage.backtest_store import BacktestRepository
from app.ui.backtest_page import NOT_CONNECTED, BacktestContext, BacktestPage
from tests.unit.backtest_helpers import noisy_history
from tests.unit.storage_helpers import temporary_store

HISTORY = noisy_history(26, 1)


def context(
    tmp_path: Path,
    runs: BacktestRepository | None,
    connected: bool = True,
) -> BacktestContext:
    return BacktestContext(
        load=lambda request, note: (HISTORY, ("history note",)),
        strategies=StrategySettingsSource(tmp_path),
        risk=RiskSettingsSource(tmp_path),
        execution=ExecutionSettingsSource(tmp_path),
        symbols=lambda: ["EURUSD", "GBPUSD"],
        connected=lambda: connected,
        runs=runs,
    )


def short_period(page: BacktestPage) -> None:
    page.start.setDate(QDate(2026, 9, 27))
    page.end.setDate(QDate(2026, 9, 29))
    page.carlo_runs.setValue(100)


def test_without_a_context_the_page_cannot_run(qtbot: QtBot) -> None:
    page = BacktestPage(None)
    qtbot.addWidget(page)
    assert not page.run_button.isEnabled()
    assert page.saved.rowCount() == 0 and page.summary.text() == "No backtest yet."


def test_not_connected_says_so(qtbot: QtBot, tmp_path: Path) -> None:
    page = BacktestPage(context(tmp_path, None, connected=False))
    qtbot.addWidget(page)
    page.start_run()
    assert page.status.text() == NOT_CONNECTED and not page.running


def test_a_run_fills_every_tab_and_is_saved(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        runs = BacktestRepository(store)
        page = BacktestPage(context(tmp_path, runs))
        qtbot.addWidget(page)
        assert page.symbol.count() == 2
        short_period(page)
        page.start_run()
        qtbot.waitUntil(lambda: page.report is not None, timeout=240_000)
        report = page.report
        assert report is not None
        assert page.trades.rowCount() == len(report.result.trades)
        assert page.summary.text().startswith("Trades ")
        assert "history note" in page.summary.text()
        assert page.status.text().startswith("Backtest done")
        assert page.groups.rowCount() == len(report.metrics.by_month)
        assert page.saved.rowCount() == 1 and len(runs.recent()) == 1
        assert page.run_button.isEnabled() and not page.cancel_button.isEnabled()


def test_the_form_becomes_a_request_and_bad_values_are_shown(qtbot: QtBot, tmp_path: Path) -> None:
    page = BacktestPage(context(tmp_path, None))
    qtbot.addWidget(page)
    short_period(page)
    page.walk_enabled.setChecked(True)
    page.walk_param.values.setText("1.5, 2")
    page.sens_enabled.setChecked(True)
    request = page.build_request()
    assert request.start == date(2026, 9, 27) and request.end == date(2026, 9, 29)
    assert request.walk_forward is not None
    assert list(request.walk_forward.grid.values()) == [[1.5, 2.0]]
    assert request.sensitivity is not None and request.sensitivity.y_values == [1.0, 1.5, 2.0]
    page.sens_x.values.setText("1, two")
    page.start_run()
    assert page.status.text().startswith("Check the form") and not page.running


def test_the_heatmap_shows_the_grid_and_the_verdict(qtbot: QtBot, tmp_path: Path) -> None:
    request = BacktestRequest(
        start=date(2026, 9, 27),
        end=date(2026, 9, 29),
        monte_carlo_runs=50,
        sensitivity=SensitivityOptions(
            strategy="london_breakout",
            x_name="buffer_atr",
            x_values=[0.05, 0.1],
            y_name="reward_r",
            y_values=[1.5, 2.0],
            minimum_trades=1,
        ),
    )
    report = run_report(
        HISTORY,
        request,
        settings=StrategySettings(),
        risk=RiskConfig(),
        execution=ExecutionConfig(),
    )
    page = BacktestPage(context(tmp_path, None))
    qtbot.addWidget(page)
    page.show_report(report)
    assert page.heatmap.rowCount() == 2 and page.heatmap.columnCount() == 2
    assert page.sens_verdict.text() == report.sensitivity.verdict()  # type: ignore[union-attr]
    assert "trades" in page.heatmap.item(0, 0).text()
