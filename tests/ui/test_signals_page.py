"""Signals and Strategies pages (spec F3) with real pipeline snapshots."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.clock import BrokerClock
from app.core.strategy_settings import StrategySettingsSource, load_strategy_settings
from app.core.ui_prefs import UiPrefs
from app.domain.signals import SignalState
from app.ui.main_window import MainWindow
from app.ui.signals_page import SignalsContext, SignalsPage
from app.ui.strategies_page import StrategiesPage
from tests.unit.signal_helpers import pipeline
from tests.unit.strategy_helpers import analysis_at, london_days


def context(tmp_path: Path) -> SignalsContext:
    signals = pipeline()
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    signals.on_analysis(analysis, clock=BrokerClock.assumed(), spec=None, spread=2e-5, now=now)
    return SignalsContext(signals, StrategySettingsSource(tmp_path))


def test_the_feed_shows_signals_with_their_trace(qtbot: QtBot, tmp_path: Path) -> None:
    found = context(tmp_path)
    page = SignalsPage(found)
    qtbot.addWidget(page)
    assert page.feed.rowCount() == 2
    assert page.scanner.rowCount() == 1
    assert not page.approve_button.isEnabled()
    page.feed.selectRow(0)
    text = page.trace.toPlainText()
    assert "Decision: PENDING_APPROVAL" in text and "risk:" in text
    assert page.dismiss_button.isEnabled()
    page.state_filter.setCurrentText("Filtered out")
    assert page.feed.rowCount() == 0
    page.state_filter.setCurrentText("All signals")
    page.feed.selectRow(0)
    chosen = page.selected_record()
    assert chosen is not None
    page.dismiss_selected()
    found.pipeline.on_cycle()
    page.show_snapshot(found.pipeline.snapshot)
    states = {r.id: r.signal.state for r in found.pipeline.snapshot.signals}
    assert states[chosen.id] is SignalState.USER_REJECTED


def test_strategy_settings_are_saved_from_the_cards(qtbot: QtBot, tmp_path: Path) -> None:
    source = StrategySettingsSource(tmp_path)
    page = StrategiesPage(source)
    qtbot.addWidget(page)
    assert sorted(page.cards) == ["london_breakout", "trend_pullback"]
    card = page.cards["trend_pullback"]
    card.enabled.setChecked(False)
    assert page.save()
    assert load_strategy_settings(tmp_path).enabled() == ["london_breakout"]
    times = page.cards["london_breakout"].form.editors["range_start"]
    times.setText("09:00")  # type: ignore[union-attr]
    assert not page.save()
    assert "Not saved" in page.status.text()


def test_the_window_uses_the_real_pages(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(), tmp_path, signals=context(tmp_path))
    qtbot.addWidget(window)
    window.show_page("signals")
    assert isinstance(window.pages.currentWidget(), SignalsPage)
    window.show_page("strategies")
    assert isinstance(window.pages.currentWidget(), StrategiesPage)


def test_approve_asks_then_sends_through_the_engine(qtbot: QtBot, tmp_path: Path) -> None:
    from app.engine.filters import FilterSettings
    from app.engine.signal_pipeline import SignalPipeline
    from tests.unit.execution_helpers import NOW, eurusd_record, rig
    from tests.unit.risk_helpers import connected
    from tests.unit.storage_helpers import temporary_store

    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store) as r:
        signals = SignalPipeline(lambda: [], FilterSettings, executor=r.engine, utc_now=lambda: NOW)
        signals._remember(record)
        signals.on_cycle(NOW)
        page = SignalsPage(SignalsContext(signals, StrategySettingsSource(tmp_path)))
        qtbot.addWidget(page)
        page.show_snapshot(signals.snapshot)
        page.feed.selectRow(0)
        assert page.approve_button.isEnabled()
        page.confirm = lambda title, text: False
        assert not page.approve_selected()
        asked: list[str] = []
        page.confirm = lambda title, text: asked.append(text) is None
        assert page.approve_selected()
        assert "You could lose about" in asked[0]
        signals.on_cycle(NOW + 2)
        assert signals.snapshot.signals[0].signal.state is SignalState.MANAGED
        assert len(fake.positions) == 1
