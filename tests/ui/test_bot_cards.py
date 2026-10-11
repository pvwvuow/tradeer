"""A channel signal in the Telegram bot (phase 22b, 0.47.0): the card's first answer becomes
the bot's message (Approve and Skip only while it waits), a signal that makes no card is told
at once, and the buttons do what the hold and Skip do, on the UI thread."""

import threading
from dataclasses import replace
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage
from app.channels.policy import ChannelPolicy
from app.domain.modes import OperatingMode
from app.notify.bot_text import APPROVE, INFO, REFUSED, SignalAlert
from app.ui.bot_cards import CardActions, alert_from_card, first_line
from app.ui.channel_feed import ChannelFeed
from tests.ui.test_channel_feed import GOLD, LIVE, channel
from tests.ui.test_signal_card import desk_page, pasted, waiting_card
from tests.unit.storage_helpers import temporary_store
from tests.unit.test_signal_desk import buy_text


def test_a_card_that_waits_is_told_with_its_prices_and_buttons(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    alert = alert_from_card(card, "Gold Room")
    assert alert.kind == APPROVE and alert.request_id == card.request_id
    assert alert.symbol == "EURUSD" and alert.side == "BUY MARKET"
    labels = [label for label, _value, _note in alert.rows]
    assert labels[:2] == ["Entry", "SL"] and "TP1" in labels and "TP2" in labels
    assert alert.status == "Waiting for your approval" and alert.expires
    blocked = replace(signals.snapshot, approval_block="no engine")
    desk.show_snapshot(blocked)
    sending_off = alert_from_card(card, "Gold Room")
    assert sending_off.kind == INFO and "sending is off" in sending_off.status
    assert first_line("\n  \nfirst line\nsecond") == "first line"


def test_a_refused_card_is_told_as_refused(qtbot: QtBot, tmp_path: Path) -> None:
    page, _desk, signals, now, _price = desk_page(qtbot, tmp_path)
    card = pasted(page, "GBPUSD buy sl 1.2 tp 1.3")
    signals.on_cycle(now + 1)
    qtbot.waitUntil(lambda: card.state == "refused")
    alert = alert_from_card(card, "Gold Room")
    assert alert.kind == REFUSED and "not on the watchlist" in alert.status
    assert alert.symbol == "GBPUSD" and alert.rows[0][0] == "Entry"


def test_a_channel_signal_reaches_the_bot_once_the_desk_answers(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        told: list[SignalAlert] = []
        feed.alerts.append(told.append)
        feed.handle(ChannelMessage(GOLD.id, 7, now, buy_text(price)))
        assert told == []  # the desk has not answered yet
        signals.on_cycle(now + 1)
        qtbot.waitUntil(lambda: bool(told))
        assert told[0].channel == "Gold Room" and told[0].symbol == "EURUSD"
        desk.show_snapshot(signals.snapshot)
        assert len(told) == 1  # told once per card

        def broken(alert: SignalAlert) -> None:
            raise RuntimeError("boom")

        feed.alerts.insert(0, broken)  # a failing sink stops nothing
        feed.tell(told[0])
        assert len(told) == 2


def test_a_paper_trial_is_told_at_once_without_buttons(qtbot: QtBot, tmp_path: Path) -> None:
    _page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, ChannelPolicy())
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        told: list[SignalAlert] = []
        feed.alerts.append(told.append)
        feed.handle(ChannelMessage(GOLD.id, 1, now, buy_text(price)))
        feed.handle(ChannelMessage(GOLD.id, 2, now, "Join our VIP group today"))
        assert len(told) == 1 and told[0].kind == INFO and not told[0].request_id
        assert told[0].status[:1].isupper() and told[0].channel == "Gold Room"


def test_approve_does_what_the_hold_does(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    approved: list[str] = []
    monkeypatch.setattr(signals, "approve", approved.append)
    actions = CardActions(desk)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    gone = actions.act("ok", "nothing-like-it")
    assert gone.done and "gone" in gone.toast
    reply = actions.act("ok", card.request_id[:32])
    assert reply.done and reply.toast == "Approved." and "checks the price" in reply.note
    assert sorted(approved) == sorted(card.leg_ids) and card.state == "sending"
    again = actions.act("ok", card.request_id[:32])
    assert again.done and "Not waiting any more" in again.toast
    assert len(approved) == len(card.leg_ids)  # one press sends once


def test_skip_dismisses_the_waiting_legs(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    dismissed: list[str] = []
    monkeypatch.setattr(signals, "dismiss", dismissed.append)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    skipped = CardActions(desk).act("no", card.request_id)
    assert skipped.done and "Nothing was sent" in skipped.note
    assert sorted(dismissed) == sorted(card.leg_ids) and card.state == "skipped"


def test_a_real_order_asks_once_more(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path, OperatingMode.SEMI_AUTO)
    desk.real_account = lambda: True
    desk.update_tags()
    approved: list[str] = []
    monkeypatch.setattr(signals, "approve", approved.append)
    actions = CardActions(desk)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    assert card.real
    asked = actions.act("ok", card.request_id)
    assert asked.buttons is not None and not asked.done and approved == []
    assert asked.buttons[0][0][0].startswith("\u26a0")
    back = actions.act("back", card.request_id)
    assert back.buttons is not None and back.buttons[0][0][1].startswith("ok:")
    sent = actions.act("yes", card.request_id)
    assert sent.done and sorted(approved) == sorted(card.leg_ids)


def test_the_bot_thread_gets_its_answer_from_the_ui_thread(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    dismissed: list[str] = []
    monkeypatch.setattr(signals, "dismiss", dismissed.append)
    actions = CardActions(desk)
    card = waiting_card(qtbot, page, desk, signals, now, price)
    answers: list[str] = []
    thread = threading.Thread(
        target=lambda: answers.append(actions.handle("no", card.request_id[:32]).toast),
    )
    thread.start()
    qtbot.waitUntil(lambda: bool(answers), timeout=10_000)
    thread.join()
    assert answers == ["Skipped."] and card.state == "skipped"
