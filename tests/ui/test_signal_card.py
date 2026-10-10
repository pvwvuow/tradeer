"""The Signal desk's order card in the AI Lab (docs/SIGNAL_DESK.md 2.4, phase 21a3): a pasted
signal becomes a card that the pipeline plans, sizes and checks, nothing is sent without the
full hold, Skip dismisses every leg, and the header shows the trading mode."""

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.core.execution_settings import ExecutionConfig, ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource
from app.domain.modes import OperatingMode
from app.domain.signals import Direction, OrderType
from app.engine.signal_pipeline import SignalPipeline
from app.ui.ai_lab_page import AiLabContext, AiLabPage
from app.ui.lab_desk import LabDesk, is_signal
from app.ui.signal_card import (
    HOLD_MS,
    REAL_HOLD_MS,
    OrderCard,
    card_state,
    side_text,
    size_line,
)
from tests.unit.signal_helpers import pipeline
from tests.unit.test_ai_export import TRADES
from tests.unit.test_signal_desk import buy_text, watching


def lab(tmp_path: Path, mode: OperatingMode = OperatingMode.PAPER) -> AiLabContext:
    execution = ExecutionSettingsSource(tmp_path)
    execution.save(ExecutionConfig(mode=mode))
    return AiLabContext(
        trades=lambda: TRADES,
        strategies=StrategySettingsSource(tmp_path),
        execution=execution,
        export_dir=tmp_path,
    )


def desk_page(
    qtbot: QtBot,
    tmp_path: Path,
    mode: OperatingMode = OperatingMode.PAPER,
) -> tuple[AiLabPage, LabDesk, SignalPipeline, float, float]:
    signals, now, price = watching()
    page = AiLabPage(lab(tmp_path, mode))
    qtbot.addWidget(page)
    desk = LabDesk(page, signals)
    return page, desk, signals, now, price


def pasted(page: AiLabPage, text: str) -> OrderCard:
    page.chat.set_text(text)
    assert page.chat.send()
    card = page.chat.extras[-1]
    assert isinstance(card, OrderCard)
    return card


def waiting_card(
    qtbot: QtBot,
    page: AiLabPage,
    desk: LabDesk,
    signals: SignalPipeline,
    now: float,
    price: float,
) -> OrderCard:
    """A pasted signal after the next analysis cycle, with approvals allowed (as with a
    running execution engine)."""
    card = pasted(page, buy_text(price))
    signals.on_cycle(now + 1)
    qtbot.waitUntil(lambda: card.state == "waiting")
    desk.show_snapshot(replace(signals.snapshot, approval_block=""))
    return card


def test_a_pasted_signal_becomes_an_order_card_with_one_leg_per_target(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    card = pasted(page, buy_text(price))
    assert card.state == "checking" and card.state_tag.text == "CHECKING"
    assert card.prices.text().startswith("ENTRY MARKET   SL ")
    assert not card.hold_button.isEnabled()
    signals.on_cycle(now + 1)
    qtbot.waitUntil(lambda: card.state == "waiting")  # the snapshot comes through the listener
    result = signals.snapshot.desk[0]
    assert card.leg_ids == result.signal_ids and len(card.legs) == 2
    assert card.title.text() == "EURUSD  BUY MARKET"
    assert "TP1 " in card.targets.text() and "TP2 " in card.targets.text()
    assert " R " in card.targets.text()
    assert card.status.text().startswith("2 orders wait for your confirmation")
    assert "Sending is off" in card.status.text()  # no execution engine in this test
    assert not card.hold_button.isEnabled()
    assert card.checks_label.text().startswith("Checks: ")
    desk.show_snapshot(replace(signals.snapshot, approval_block=""))
    assert card.hold_button.isEnabled() and card.skip_button.isEnabled()
    assert card.status.text() == "2 orders wait for your confirmation"
    card.toggle_why()
    assert not card.why.isHidden() and "\u2713" in card.why.text()
    sized = [replace(leg, volume=0.02, risk_money=14.0) for leg in card.legs]
    assert size_line(sized, "USD") == "LOTS 0.04 = 2 x 0.02   RISK 28.00 USD"
    card.edit_button.click()
    assert page.chat.input.toPlainText() == buy_text(price)


def test_only_a_full_hold_sends_every_leg_to_the_approval_queue(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    approved: list[str] = []
    monkeypatch.setattr(signals, "approve", approved.append)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    button = card.hold_button
    assert button.hold_ms == HOLD_MS and card.real_tag.isHidden()
    button.hold_ms = 60
    qtbot.mousePress(button, Qt.MouseButton.LeftButton)
    qtbot.mouseRelease(button, Qt.MouseButton.LeftButton)
    qtbot.wait(150)
    assert approved == [] and card.state == "waiting"  # let go early: nothing is sent
    qtbot.mousePress(button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(approved))
    qtbot.mouseRelease(button, Qt.MouseButton.LeftButton)
    assert sorted(approved) == sorted(card.leg_ids)
    assert card.state == "sending" and not button.isEnabled()
    assert "final check" in card.status.text()
    assert desk.send(card.request_id) is False  # one hold sends once


def test_skip_dismisses_every_waiting_leg(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    dismissed: list[str] = []
    monkeypatch.setattr(signals, "dismiss", dismissed.append)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    card.skip_button.click()
    assert sorted(dismissed) == sorted(card.leg_ids)
    assert card.state == "skipped" and card.status.text() == "Skipped: nothing was sent."
    assert not card.hold_button.isEnabled()


def test_refused_signals_and_questions_are_not_orders(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    card = pasted(page, "GBPUSD buy sl 1.2 tp 1.3")
    signals.on_cycle(now + 1)
    qtbot.waitUntil(lambda: card.state == "refused")
    assert "GBPUSD is not on the watchlist yet" in card.status.text()
    assert card.state_tag.text == "REFUSED" and not card.hold_button.isEnabled()
    question = f"why did my EURUSD buy at {price:.5f} sl {price - 0.002:.5f} lose?"
    assert not is_signal(question, ("EURUSD",))
    assert not is_signal("show my recent trades")
    assert not is_signal('{"changes": [{"strategy": "x", "reason": "EURUSD buy 1.1 1.2"}]}')
    assert is_signal(buy_text(price), ("EURUSD",))
    assert page.paper_tag.text == "PAPER" and page.never_tag.text == "REAL ORDERS: NEVER"


def test_without_an_analysis_the_card_says_to_connect(qtbot: QtBot, tmp_path: Path) -> None:
    page = AiLabPage(lab(tmp_path))
    qtbot.addWidget(page)
    signals = pipeline(())
    desk = LabDesk(page, signals)
    card = pasted(page, "EURUSD buy sl 1.0800 tp 1.0900")
    signals.on_cycle()
    desk.show_snapshot(signals.snapshot)
    assert card.state == "refused" and "connect to MT5" in card.status.text()
    assert not card.hold_button.isEnabled()


def test_a_real_order_says_so_and_needs_the_longer_hold(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path, OperatingMode.SEMI_AUTO)
    desk.real_account = lambda: True
    desk.update_tags()
    assert page.paper_tag.text == "SEMI-AUTO"
    assert page.never_tag.text == "REAL ORDERS: ONLY WITH YOUR CONFIRM"
    card = pasted(page, buy_text(price))
    assert not card.real_tag.isHidden() and card.hold_button.hold_ms == REAL_HOLD_MS
    assert card.hold_button.text() == "Hold to send a REAL order"
    desk.real_account = lambda: False
    desk.update_tags()
    assert page.never_tag.text == "REAL ORDERS: NEVER"


def test_the_card_words() -> None:
    assert side_text(Direction.SHORT, OrderType.LIMIT) == "SELL LIMIT"
    assert side_text(Direction.LONG, None) == "BUY"
    assert side_text(None, None) == "?"
    assert card_state(None, ()) == "checking"
    assert card_state(None, (), skipped=True) == "skipped"
    assert size_line(()) == ""
