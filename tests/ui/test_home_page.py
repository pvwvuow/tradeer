"""Phase 9 acceptance (spec G3 row 9) in the No Curve v2 Simple view (Phase 20d): a fresh
Paper-mode signal is a proposal in plain words with money-based results, approved by holding
the button, and the emergency button under its cover is the Advanced kill switch."""

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from pytestqt.qtbot import QtBot

from app.core.execution_settings import ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource
from app.core.ui_prefs import Language, UiPrefs
from app.domain.modes import OperatingMode
from app.domain.probability import ProbabilityEstimate
from app.domain.signals import SignalState
from app.engine.filters import FilterSettings
from app.engine.signal_pipeline import SignalPipeline
from app.ui.home_model import jargon_in
from app.ui.home_page import (
    LRE,
    PDF,
    STOP_SENT,
    WORDS_FA,
    HomePage,
    home_qss,
    loss_share,
    percent_bounds,
)
from app.ui.i18n import Translator
from app.ui.main_window import MainWindow
from app.ui.positions_page import TradingContext
from app.ui.signals_page import SignalsContext
from app.ui.theme import DARK, LIGHT
from tests.unit.execution_helpers import NOW, Rig, eurusd_record, rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store

MINUS = "\u2212"


def signals_for(r: Rig) -> SignalPipeline:
    return SignalPipeline(
        lambda: [],
        FilterSettings,
        executor=r.engine,
        utc_now=lambda: r.clock[0],
    )


def test_the_home_stylesheet_parses(qtbot: QtBot) -> None:
    messages: list[str] = []

    def handler(kind: QtMsgType, _context: object, text: str) -> None:
        messages.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        for tokens in (LIGHT, DARK):
            page = HomePage()
            qtbot.addWidget(page)
            page.apply_tokens(tokens)
            page.show()
            assert tokens.loss in home_qss(tokens)
    finally:
        qInstallMessageHandler(previous)
    assert not [text for text in messages if "Could not parse" in text]


def test_the_numbers_of_the_design() -> None:
    assert loss_share(2.0) == 1 / 3  # risk 1, reward 2: the loss is a third of the bar
    assert loss_share(0.0) == 0.5 and loss_share(float("nan")) == 0.5
    known = ProbabilityEstimate(value=0.54, low=0.41, high=0.67, samples=38, source="baseline")
    assert percent_bounds(known) == (41, 54, 67)
    unknown = ProbabilityEstimate(value=None, low=None, high=None, samples=4, source="none")
    assert percent_bounds(unknown) is None


def test_without_the_engine_home_is_an_honest_empty_screen(qtbot: QtBot) -> None:
    page = HomePage()
    qtbot.addWidget(page)
    page.show()
    assert page.empty_title.isVisible() and not page.approve_button.isVisible()
    assert page.proposal_label.text() == "PROPOSAL 0/0"
    assert page.data_tag.text == "WAITING FOR DATA" and page.data_tag.tone == "warning"
    assert not page.stop_button.isEnabled() and not page.stop_trading()
    assert not page.stop_cover.isEnabled() and not page.arm_stop()
    assert not page.approve() and not page.skip()
    assert page.state_line.text().startswith("Not connected")
    assert page.today_label.text() == "\u2014" and page.no_trades.isVisible()
    assert page.trades_count.text == "0"
    assert jargon_in(page.default_texts()) == []


def test_a_fresh_paper_signal_is_a_proposal_held_to_approve(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    fake = connected()
    record = replace(eurusd_record(), volume=0.5, risk_money=50.0)
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        signals = signals_for(r)
        signals._remember(record)  # a pending signal, as `on_analysis` leaves it
        signals.on_cycle(NOW)
        source = ExecutionSettingsSource(tmp_path)  # Paper, the default
        page = HomePage(
            SignalsContext(signals, StrategySettingsSource(tmp_path)),
            None,
            TradingContext(r.engine, source, lambda: False),
        )
        qtbot.addWidget(page)
        page.now = lambda: NOW
        page.show()
        page.set_connection(True, "Status: connected to the demo account.")
        page.show_signals(signals.snapshot)
        assert page.proposal_label.text() == "PROPOSAL 1/1"
        assert page.data_tag.text == "LIVE" and page.data_tag.tone == "profit"
        assert page.card_title.text() == "EUR/USD.m"
        assert page.card_title.toolTip() == "Euro vs US dollar (EURUSD.m)"
        assert page.side_tag.text == "Buy" and page.side_tag.icon == "up"
        assert page.sentence_text == (
            "Buy EUR/USD.m with a size of 0.50. If it goes wrong you lose about 50.00, and "
            "if it goes right you make about 100.00."
        )
        assert page.risk_bar.lose_text == f"{MINUS}50.00"
        assert page.risk_bar.make_text == "+100.00"
        assert abs(page.risk_bar.share - 1 / 3) < 1e-9
        assert page.entry_cap.text() == "ENTRY \u00b7 R:R 1:2.0"
        assert "trending up" in page.reason_labels[0].text()
        assert page.reason_labels[-1].text() == "It passed the app's filters before it was shown."
        bounds = percent_bounds(record.probability)
        assert page.dots.bounds == bounds
        if bounds is None:  # too few past results: no number is made up
            assert page.win_label.text() == "\u2014"
            assert page.range_label.text().startswith("Not known yet:")
        else:
            assert page.win_label.text() == f"{bounds[1]}%"
        assert page.ends_label.text() == "This suggestion ends in 30 min"
        assert page.approve_button.isEnabled() and page.skip_button.isEnabled()
        assert page.approve_button.text() == "Hold to place it in Paper"
        assert page.state_line.text() == "Found a trade for you."
        assert "Practice money" in page.mode_line.text()
        assert jargon_in(page.default_texts()) == []
        assert not page.details_label.isVisible()
        page.details_button.click()
        assert page.details_label.isVisible() and "stop loss" in page.details_label.text()
        page.confirm = lambda title, text: False
        assert not page.approve()
        questions: list[str] = []

        def yes(title: str, text: str) -> bool:
            questions.append(text)
            return True

        page.confirm = yes
        page.approve_button.start_hold()  # a short hold, let go early: nothing happens
        qtbot.wait(200)
        assert page.approve_button.progress > 0
        page.approve_button.stop_hold()
        assert page.approve_button.progress == 0 and questions == []
        page.approve_button.start_hold()  # held to the end: the usual question, once
        qtbot.waitUntil(lambda: len(questions) == 1, timeout=3000)
        assert "practice money" in questions[0]
        assert not page.approve_button.holding and page.toast.isVisible()
        assert page.toast.text() == "Approved: it is checked again and placed in a few seconds."
        signals.on_cycle(NOW + 2)
        found = {item.id: item for item in signals.snapshot.signals}[record.id]
        assert found.signal.state is SignalState.MANAGED
        page.show_signals(signals.snapshot)
        page.show_trading(r.engine.snapshot)
        assert page.empty_title.isVisible() and not page.approve_button.isVisible()
        assert [row.title for row in page.rows] == ["Euro vs US dollar (EURUSD.m)"]
        assert len(page.close_buttons) == 1 and fake.trading_calls == []
        assert page.trades_count.text == "1" and not page.no_trades.isVisible()
        assert page.state_line.text() == "Trade running."
        assert jargon_in(page.default_texts()) == []
        row = page.rows[0]
        assert page.close_trade(row.mode, row.ticket)
        assert "Close Euro vs US dollar (EURUSD.m) now" in questions[-1]
        r.engine.cycle(NOW + 3)
        page.show_trading(r.engine.snapshot)
        assert page.rows == [] and page.no_trades.isVisible()


def test_the_persian_proposal_uses_the_design_words(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    record = replace(eurusd_record(), volume=0.5, risk_money=50.0)
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        signals = signals_for(r)
        signals._remember(record)
        signals.on_cycle(NOW)
        page = HomePage(SignalsContext(signals, StrategySettingsSource(tmp_path)))
        qtbot.addWidget(page)
        page.translator = Translator(Language.FA)
        page.now = lambda: NOW
        page.set_connection(True, "Status: connected to the demo account.")
        page.show_signals(signals.snapshot)
        assert page.side_tag.text == "خرید"
        assert page.data_tag.text == "زنده"
        assert page.sentence_text.startswith(f"{LRE}0.50{PDF} لات {LRE}EUR/USD.m{PDF} بخرید.")
        assert page.approve_button.text() == WORDS_FA["Hold to place it in Paper"]
        assert page.skip_button.text() == "رد کردن"
        assert page.stop_button.text() == "همین الان همه را ببند"
        assert page.reason_labels[0].text().startswith("روند")  # the model's words, translated


def test_skip_dismisses_the_suggestion(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    record = eurusd_record()
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        signals = signals_for(r)
        signals._remember(record)
        signals.on_cycle(NOW)
        page = HomePage(SignalsContext(signals, StrategySettingsSource(tmp_path)))
        qtbot.addWidget(page)
        page.now = lambda: NOW
        page.show_signals(signals.snapshot)
        assert page.suggestion is not None
        assert page.skip()
        assert page.suggestion is None
        signals.on_cycle(NOW + 2)
        found = {item.id: item for item in signals.snapshot.signals}[record.id]
        assert found.signal.state is SignalState.USER_REJECTED


def test_the_emergency_button_is_the_advanced_kill_switch(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    fake = connected()
    with temporary_store() as store, rig(fake, store, OperatingMode.PAPER) as r:
        r.engine.execute(eurusd_record(), NOW)
        r.engine.cycle(NOW + 1)
        source = ExecutionSettingsSource(tmp_path)
        trading = TradingContext(r.engine, source, lambda: False)
        window = MainWindow(UiPrefs(onboarded=True), tmp_path, trading=trading)
        qtbot.addWidget(window)
        window.show()
        home = window.home
        assert home.stop_button.isVisible() and not home.stop_button.isEnabled()  # covered
        assert home.stop_cover.isEnabled() and not home.confirm_stop()
        assert home.arm_stop() and home.armed and home.stop_button.isEnabled()
        assert home.cancel_button.isVisible()
        home.disarm_stop()
        assert not home.armed and not home.stop_button.isEnabled()
        titles: list[str] = []

        def no(title: str, text: str) -> bool:
            titles.append(title)
            return False

        window.positions_page.confirm = no
        assert home.arm_stop()
        assert not home.confirm_stop()
        assert titles == ["Kill switch"]  # the very same question as the Advanced view
        assert not home.armed  # the cover closes again
        window.positions_page.confirm = lambda title, text: True
        assert home.arm_stop() and home.confirm_stop()
        assert home.toast.text() == STOP_SENT and not home.armed
        r.engine.cycle(NOW + 5)
        home.set_connection(True, "Status: connected to the demo account.")
        home.show_trading(r.engine.snapshot)
        window.set_execution_status(r.engine.snapshot)
        assert r.engine.snapshot.positions == ()
        assert window.bot_state_label.text() == "Bot: stopped (kill switch)"
        assert home.state_line.text().startswith("Stopped: you pressed Stop trading")
