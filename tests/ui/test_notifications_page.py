"""Settings > Notifications and the tray (spec C14)."""

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from app.core.credentials import MemoryStore
from app.notify.center import NotificationCenter
from app.notify.events import EventKind, Notice
from app.notify.settings import NotificationSettingsSource
from app.ui.notifications_page import NotificationsContext, NotificationsPage, parse_chat_ids
from app.ui.tray import TrayNotifier


def page_with(qtbot: QtBot, tmp_path: Path) -> tuple[NotificationsPage, NotificationsContext]:
    source = NotificationSettingsSource(tmp_path)
    context = NotificationsContext(
        source=source,
        credentials=MemoryStore(),
        token_name="default/telegram-bot-token",
        center=NotificationCenter(lambda: source.settings),
        apply_bot=lambda: "Telegram bot started.",
        test=lambda: "test sent",
    )
    page = NotificationsPage(context)
    qtbot.addWidget(page)
    return page, context


def test_saving_keeps_the_token_in_the_credential_store(qtbot: QtBot, tmp_path: Path) -> None:
    page, context = page_with(qtbot, tmp_path)
    page.toast_boxes[EventKind.TRADE_OPENED].setChecked(False)
    page.quiet_box.setChecked(True)
    page.quiet_start.setText("23:30")
    page.telegram_box.setChecked(True)
    page.token.setText("123:secret")
    page.chats.setText("42, 43")
    page.pin.setText("2468")
    assert page.save()
    settings = context.source.settings
    assert not settings.wants("toast", EventKind.TRADE_OPENED)
    assert settings.quiet_hours and settings.quiet_start == "23:30"
    assert settings.chat_ids == [42, 43] and settings.pin_matches("2468")
    assert context.credentials.get("default/telegram-bot-token") == "123:secret"
    text = (tmp_path / "notifications.json").read_text()
    assert "123:secret" not in text
    # The PIN is kept only as a salted hash. Its hex digits may contain "2468" by chance,
    # so look at the saved values, not at the raw text.
    values = [str(value) for value in json.loads(text).values()]
    assert "2468" not in values
    assert page.status.text() == "Saved. Telegram bot started."
    assert page.token.text() == "" and "saved" in page.token.placeholderText()


def test_bad_values_are_not_saved(qtbot: QtBot, tmp_path: Path) -> None:
    page, context = page_with(qtbot, tmp_path)
    page.telegram_box.setChecked(True)
    assert not page.save()
    assert "chat id" in page.status.text()
    page.chats.setText("42")
    page.quiet_start.setText("25:00")
    assert not page.save()
    page.quiet_start.setText("22:00")
    page.pin.setText("12")
    assert not page.save() and "PIN" in page.status.text()
    assert not context.source.settings.telegram_enabled


def test_the_test_button_and_the_history(qtbot: QtBot, tmp_path: Path) -> None:
    page, context = page_with(qtbot, tmp_path)
    context.center.publish(Notice(EventKind.LIMIT_HIT, "daily loss"))
    assert page.send_test() == "test sent"
    assert page.history.count() == 1
    assert page.history.item(0) is not None


def test_chat_ids() -> None:
    assert parse_chat_ids("42; -100123 42") == [42, -100123]
    with pytest.raises(ValueError):
        parse_chat_ids("abc")


def test_the_tray_shows_notices_from_any_thread(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    tray = TrayNotifier(window)
    tray.notify(Notice(EventKind.TRADE_CLOSED, "EURUSD closed"))
    qtbot.waitUntil(lambda: bool(tray.shown))
    assert tray.shown == [("Trade closed", "EURUSD closed")]
    tray.hide()
