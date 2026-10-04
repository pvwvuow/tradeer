"""The AI Lab page (spec C13, F3.9): export, paste and check, backtest against the current
settings, and activation only in Paper with a new config version and an audit row."""

from pathlib import Path

from PySide6.QtCore import QDate
from pytestqt.qtbot import QtBot

from app.analytics.ai_import import CREATED_BY, Verdict
from app.core.execution_settings import ExecutionConfig, ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource, load_strategy_settings
from app.core.ui_prefs import UiPrefs
from app.domain.modes import OperatingMode
from app.risk.settings import RiskSettingsSource
from app.storage.backtest_store import BacktestRepository
from app.storage.repositories import Store
from app.ui.ai_lab_page import AiLabContext, AiLabPage, ai_lab_context
from app.ui.analytics_page import AnalyticsContext
from app.ui.backtest_page import BacktestContext
from app.ui.main_window import MainWindow
from tests.unit.backtest_helpers import noisy_history
from tests.unit.storage_helpers import temporary_store
from tests.unit.test_ai_export import TRADES

HISTORY = noisy_history(26, 1)
ANSWER = (
    '{"changes": [{"strategy": "trend_pullback", "params": {"min_adx_h1": 25}, '
    '"reason": "weak trends lose", "expected_impact": "fewer losers"}]}'
)


def backtest_context(tmp_path: Path, store: Store | None) -> BacktestContext:
    return BacktestContext(
        load=lambda request, note: (HISTORY, ("history note",)),
        strategies=StrategySettingsSource(tmp_path),
        risk=RiskSettingsSource(tmp_path),
        execution=ExecutionSettingsSource(tmp_path),
        symbols=lambda: ["EURUSD", "GBPUSD"],
        connected=lambda: True,
        runs=BacktestRepository(store) if store is not None else None,
    )


def lab(
    tmp_path: Path,
    store: Store | None = None,
    mode: OperatingMode = OperatingMode.PAPER,
) -> AiLabContext:
    backtest = backtest_context(tmp_path, store)
    backtest.execution.save(ExecutionConfig(mode=mode))
    return AiLabContext(
        trades=lambda: TRADES,
        strategies=backtest.strategies,
        execution=backtest.execution,
        export_dir=tmp_path,
        backtest=backtest,
        store=store,
    )


def checked_page(qtbot: QtBot, context: AiLabContext) -> AiLabPage:
    page = AiLabPage(context)
    qtbot.addWidget(page)
    page.answer.setPlainText(ANSWER)
    found = page.check_suggestion()
    assert found is not None and found.valid
    return page


def test_without_a_context_nothing_runs(qtbot: QtBot) -> None:
    page = AiLabPage(None)
    qtbot.addWidget(page)
    for button in (page.export_button, page.check_button, page.test_button):
        assert not button.isEnabled()
    assert not page.activate_button.isEnabled()
    assert page.export_for_ai() == [] and page.check_suggestion() is None
    assert page.activate() is False


def test_the_export_writes_the_files_for_the_ai(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        page = AiLabPage(lab(tmp_path, store))
        qtbot.addWidget(page)
        page.export_days.setValue(0)
        paths = page.export_for_ai()
    assert [path.name for path in paths] == ["trades_full.csv", "trades_full.json", "report.md"]
    assert all(path.is_file() for path in paths)
    assert paths[0].parent.parent == tmp_path / "exports"
    assert page.export_status.text().startswith("Saved 12 trades")
    assert "Strategy parameters" in paths[2].read_text(encoding="utf-8")


def test_checking_shows_the_difference_or_the_problems(qtbot: QtBot, tmp_path: Path) -> None:
    page = checked_page(qtbot, lab(tmp_path))
    assert page.diff.rowCount() == 1
    assert page.diff.item(0, 1).text() == "min_adx_h1" and page.diff.item(0, 3).text() == "25"
    assert "weak trends lose" in page.check_status.text()
    assert page.test_button.isEnabled() and not page.activate_button.isEnabled()
    page.answer.setPlainText('{"changes": [{"strategy": "trend_pullback", "params": {"x": 1}}]}')
    found = page.check_suggestion()
    assert found is not None and not found.valid
    assert page.diff.rowCount() == 0 and "Problem:" in page.check_status.text()
    assert not page.test_button.isEnabled()


def test_a_mode_with_real_orders_refuses_activation(qtbot: QtBot, tmp_path: Path) -> None:
    page = checked_page(qtbot, lab(tmp_path, mode=OperatingMode.SEMI_AUTO))
    assert page.suggestion is not None
    page.set_verdict(Verdict(True, ("better",)), page.suggestion)
    assert page.activate() is False
    assert "Switch to Paper" in page.activate_status.text()
    assert "min_adx_h1" not in load_strategy_settings(tmp_path).entry("trend_pullback").params


def test_activation_needs_a_test_then_writes_settings_config_and_audit(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    with temporary_store() as store:
        page = checked_page(qtbot, lab(tmp_path, store))
        assert page.activate() is False
        assert page.activate_status.text() == "Run the backtest comparison first."
        assert page.suggestion is not None
        page.set_verdict(Verdict(False, ("not better",)), page.suggestion)
        assert page.activate_button.isEnabled()
        asked: list[str] = []

        def refuse(text: str) -> bool:
            asked.append(text)
            return False

        page.confirm = refuse
        assert page.activate() is False and asked
        assert page.activate_status.text() == "Not activated."
        page.confirm = lambda text: True
        assert page.activate() is True
        configs = store.db.query("SELECT created_by, notes, is_active FROM strategy_configs")
        audits = store.db.query("SELECT source FROM audit_log")
    params = load_strategy_settings(tmp_path).entry("trend_pullback").params
    assert params["min_adx_h1"] == 25
    assert [row["created_by"] for row in configs] == [CREATED_BY]
    assert "weak trends lose" in configs[0]["notes"]
    assert [row["source"] for row in audits] == [CREATED_BY]
    assert page.activate_status.text().startswith("Active for trend_pullback in Paper")
    assert page.activate() is False  # the saved params moved: check again first
    assert "changed since the check" in page.activate_status.text()


def test_a_comparison_backtests_both_settings(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        context = lab(tmp_path, store)
        page = checked_page(qtbot, context)
        page.start.setDate(QDate(2026, 9, 27))
        page.end.setDate(QDate(2026, 9, 29))
        page.start_test()
        assert page.running and not page.test_button.isEnabled()
        qtbot.waitUntil(lambda: page.verdict is not None, timeout=240_000)
        assert context.backtest is not None and context.backtest.runs is not None
        saved = context.backtest.runs.recent()
    assert page.comparison is not None and not page.running
    assert page.results.rowCount() == 6
    assert page.results.item(0, 0).text() == "Trades"
    assert len(saved) == 2
    assert page.test_status.text().startswith("Done: EURUSD")
    assert page.verdict is not None and "luck" in page.verdict.lines[-1]
    assert page.activate_button.isEnabled()


def test_the_main_window_shows_the_ai_lab(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(), tmp_path)
    qtbot.addWidget(window)
    window.show_page("ai_lab")
    assert isinstance(window.pages.currentWidget(), AiLabPage)
    assert ai_lab_context(None, None) is None
    with temporary_store() as store:
        backtest = backtest_context(tmp_path, store)
        analytics = AnalyticsContext(
            trades=lambda: TRADES,
            balance=lambda: 10_000.0,
            export_dir=tmp_path,
        )
        found = ai_lab_context(analytics, backtest)
    assert found is not None and found.store is store and found.backtest is backtest
