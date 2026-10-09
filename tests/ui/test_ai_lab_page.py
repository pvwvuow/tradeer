"""The AI Lab page (spec C13, F3.9, docs/NOCURVE_V2.md 20e): export, paste and check,
backtest against the current settings, activation only in Paper with a new config version and
an audit row; the loop as cards in the chat, the four steps, the charts from the app's own
data, the experiments and the saved prompts."""

from pathlib import Path

from PySide6.QtCore import QDate, Qt
from pytestqt.qtbot import QtBot

from app.analytics.ai_import import CREATED_BY, Verdict
from app.core.execution_settings import ExecutionConfig, ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource, load_strategy_settings
from app.core.ui_prefs import UiPrefs
from app.domain.modes import OperatingMode
from app.risk.settings import RiskSettingsSource
from app.storage.backtest_store import BacktestRepository
from app.storage.repositories import Store
from app.ui.ai_lab_page import SUBTITLE_FA, AiLabContext, AiLabPage, ai_lab_context
from app.ui.analytics_page import AnalyticsContext
from app.ui.backtest_page import BacktestContext
from app.ui.lab_cards import ChartCard
from app.ui.lab_parts import LabCard
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
    assert not page.inspector.buttons["history"].isEnabled()


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
    card = page.chat.extras[-1]
    assert isinstance(card, LabCard) and card.objectName() == "AiExportCard"
    assert [name.split(" ")[0] for name, _size in card.rows] == [path.name for path in paths]
    assert page.steps.done[0] and not page.steps.done[1]
    page.inspector.show_panel("files")
    assert {item.name for item in page.files_panel.files} == {path.name for path in paths}


def test_checking_shows_the_difference_or_the_problems(qtbot: QtBot, tmp_path: Path) -> None:
    page = checked_page(qtbot, lab(tmp_path))
    assert page.diff.rowCount() == 1
    assert page.diff.item(0, 1).text() == "min_adx_h1" and page.diff.item(0, 3).text() == "25"
    assert "weak trends lose" in page.check_status.text()
    assert page.test_button.isEnabled() and not page.activate_button.isEnabled()
    assert page.chat.shown(page.paste_card) and page.steps.done[1]
    assert page.paste_card.state_tag.text == "VALID"
    page.answer.setPlainText('{"changes": [{"strategy": "trend_pullback", "params": {"x": 1}}]}')
    found = page.check_suggestion()
    assert found is not None and not found.valid
    assert page.diff.rowCount() == 0 and "Problem:" in page.check_status.text()
    assert not page.test_button.isEnabled() and page.paste_card.state_tag.text == "INVALID"


def test_a_pasted_answer_in_the_composer_is_checked_without_an_ai(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page = AiLabPage(lab(tmp_path))
    qtbot.addWidget(page)
    page.chat.input.setPlainText(ANSWER)
    assert page.chat.send() is True and page.chat.input.toPlainText() == ""
    assert page.suggestion is not None and page.suggestion.valid
    assert page.chat.views == [] and page.chat.shown(page.paste_card)
    assert page.experiments is not None and len(page.experiments.experiments()) == 1


def test_a_mode_with_real_orders_refuses_activation(qtbot: QtBot, tmp_path: Path) -> None:
    page = checked_page(qtbot, lab(tmp_path, mode=OperatingMode.SEMI_AUTO))
    assert page.suggestion is not None
    page.set_verdict(Verdict(True, ("better",)), page.suggestion)
    assert page.activate() is False
    assert "Switch to Paper" in page.activate_status.text()
    assert "min_adx_h1" not in load_strategy_settings(tmp_path).entry("trend_pullback").params
    assert page.activate_card.checks.items[0][0] is False


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
        assert page.chat.shown(page.verdict_box) and page.chat.shown(page.activate_card)
        assert [ok for ok, _text, _value in page.activate_card.checks.items] == [
            True,
            True,
            True,
            False,
        ]
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
    assert page.steps.done[3]
    assert page.experiments is not None and page.experiments.experiments()[0].status == "ACTIVE"
    assert page.activate() is False  # the saved params moved: check again first
    assert "changed since the check" in page.activate_status.text()


def test_ignoring_changes_nothing(qtbot: QtBot, tmp_path: Path) -> None:
    page = checked_page(qtbot, lab(tmp_path))
    assert page.suggestion is not None
    page.set_verdict(Verdict(False, ("not better",)), page.suggestion)
    page.ignore()
    assert not page.chat.shown(page.activate_card)
    assert "min_adx_h1" not in load_strategy_settings(tmp_path).entry("trend_pullback").params
    assert page.experiments is not None and page.experiments.experiments()[0].status == "IGNORED"


def test_a_comparison_backtests_both_settings(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        context = lab(tmp_path, store)
        page = checked_page(qtbot, context)
        page.start.setDate(QDate(2026, 9, 27))
        page.end.setDate(QDate(2026, 9, 29))
        page.start_test()
        assert page.running and not page.test_button.isEnabled()
        assert page.chat.shown(page.compare_card)
        qtbot.waitUntil(lambda: page.verdict is not None, timeout=240_000)
        assert context.backtest is not None and context.backtest.runs is not None
        saved = context.backtest.runs.recent()
    assert page.comparison is not None and not page.running
    assert page.results.rowCount() == 6
    assert page.results.item(0, 0).text() == "Trades"
    assert len(saved) == 2
    assert page.test_status.text().startswith("Done: EURUSD")
    assert page.verdict is not None and "luck" in page.verdict.lines[-1]
    assert page.activate_button.isEnabled() and page.steps.done[2]
    assert [check.key for check in page.checks] == ["trades", "expectancy", "drawdown"]
    assert len(page.compare_card.checks.items) == 3
    experiment = page.experiments.experiments()[0] if page.experiments is not None else None
    assert experiment is not None and experiment.verdict in ("BETTER", "NOT BETTER")
    assert experiment.current_curve and experiment.symbol == "EURUSD"
    page.visual("equity")
    assert isinstance(page.chat.extras[-1], ChartCard)


def test_the_charts_come_from_the_trades(qtbot: QtBot, tmp_path: Path) -> None:
    page = AiLabPage(lab(tmp_path))
    qtbot.addWidget(page)
    page.export_days.setValue(0)
    for kind in ("stats", "r_distribution", "monte_carlo", "trades"):
        page.visual(kind)
        assert isinstance(page.chat.extras[-1], LabCard)
    names = [card.objectName() for card in page.chat.extras]
    assert names == ["AiStatsCard", "AiChartCard", "AiChartCard", "AiChartCard"]
    page.visual("equity")  # no comparison yet: it says so
    assert page.chat.extras[-1].objectName() == "AiNoteCard"
    assert "12 trades" in page.visual_summary("stats")
    page.chat.input.setPlainText("Run the Monte Carlo")
    assert page.chat.send() is True and isinstance(page.chat.extras[-1], ChartCard)
    page.chat.new_chat()
    assert page.chat.extras == []


def test_saved_prompts_and_the_inspector(qtbot: QtBot, tmp_path: Path) -> None:
    page = AiLabPage(lab(tmp_path))
    qtbot.addWidget(page)
    assert page.save_prompt() is False
    page.chat.input.setPlainText("Which session loses most?")
    assert page.save_prompt() is True
    assert page.prompts is not None and page.prompts.saved()[0].text == "Which session loses most?"
    assert page.inspector.show_panel("prompts")
    assert page.prompts_panel.shown[0].text == "Which session loses most?"
    page.prompts_panel.use_prompt.emit("Recent trades")
    assert page.chat.input.toPlainText() == "Recent trades"
    for key in ("settings", "history", "files", "experiments", "usage"):
        assert page.inspector.show_panel(key) and page.inspector.current == key
    assert page.inspector.width() == 372


def test_persian_runs_right_to_left_with_the_design_subtitle(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page = AiLabPage(lab(tmp_path), persian=True)
    qtbot.addWidget(page)
    page.dress_header()
    assert page.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert page.header.subtitle.text() == SUBTITLE_FA
    assert page.steps.labels[0].text() == "Export for AI"
    assert page.chat.input.placeholderText().startswith("\u0633\u0624\u0627\u0644")


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
