"""Notifications (spec C14): changes become notices, settings and quiet hours decide where
they go, and the Telegram bot answers only whitelisted, unlocked chats."""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.domain.modes import OperatingMode
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.notify.center import NotificationCenter
from app.notify.events import EventKind, Notice, connection_notices, execution_notices
from app.notify.settings import NotificationSettings, NotificationSettingsSource
from app.notify.telegram import TelegramApi, TelegramBot, TelegramError
from app.notify.watcher import NoticeWatcher
from app.storage.sync import SyncState, SyncStatus
from tests.unit.signal_helpers import make_record, pending


def view(ticket: int, **changes: Any) -> PositionView:
    values: dict[str, Any] = {
        "mode": "paper",
        "ticket": ticket,
        "symbol": "EURUSD",
        "direction": "long",
        "volume": 0.1,
        "entry": 1.1,
        "sl": 1.099,
        "tp": 1.102,
        "profit": 12.5,
        "strategy": "trend_pullback",
        "pending": False,
    }
    values.update(changes)
    return PositionView(**values)


def test_positions_that_appear_and_disappear_notify_once() -> None:
    first = ExecutionSnapshot(OperatingMode.PAPER, (view(1),))
    second = ExecutionSnapshot(OperatingMode.PAPER, (view(2), view(3, pending=True)))
    assert execution_notices(None, first) == []
    found = execution_notices(first, second)
    kinds = sorted(n.kind.value for n in found)
    assert kinds == ["trade_closed", "trade_opened"]
    opened = next(n for n in found if n.kind is EventKind.TRADE_OPENED)
    assert opened.text.startswith("Buy 0.1 EURUSD at 1.1")
    stopped = ExecutionSnapshot(OperatingMode.PAPER, (), stopped="kill switch pressed")
    kinds = [n.kind for n in execution_notices(ExecutionSnapshot(), stopped)]
    assert kinds == [EventKind.LIMIT_HIT]


def test_a_lost_connection_notifies_but_a_chosen_disconnect_does_not() -> None:
    up = ConnectionStatus(ConnectionState.CONNECTED)
    lost = ConnectionStatus(ConnectionState.RECONNECTING, "Reconnecting", open_positions=2)
    assert connection_notices(up, lost)[0].kind is EventKind.DISCONNECTED
    assert "2 open position" in connection_notices(up, lost)[0].text
    assert connection_notices(up, ConnectionStatus(ConnectionState.DISCONNECTED)) == []


def center_with(
    settings: NotificationSettings,
    hour: int = 12,
) -> tuple[NotificationCenter, list[str]]:
    sent: list[str] = []
    center = NotificationCenter(
        lambda: settings,
        toast=lambda n: sent.append(f"toast:{n.kind.value}"),
        telegram=lambda n: sent.append(f"telegram:{n.kind.value}"),
        local=lambda: datetime(2026, 10, 3, hour, 30),
    )
    return center, sent


def test_settings_quiet_hours_and_repeats() -> None:
    settings = NotificationSettings(
        quiet_hours=True,
        quiet_start="22:00",
        quiet_end="07:00",
        telegram_enabled=True,
        chat_ids=[42],
    )
    center, sent = center_with(settings, hour=23)
    held = center.publish(Notice(EventKind.TRADE_CLOSED, "closed", "a"))
    assert held.held and sent == []
    center.publish(Notice(EventKind.LIMIT_HIT, "daily loss", "b"))  # urgent: always
    assert sent == ["toast:limit_hit", "telegram:limit_hit"]
    center.publish(Notice(EventKind.LIMIT_HIT, "daily loss", "b"))  # the same within 10 min
    assert len(sent) == 2
    day, sent_day = center_with(settings, hour=12)
    day.publish(Notice(EventKind.APPROVAL_NEEDED, "waiting", "c"))
    assert sent_day == ["toast:approval_needed"]  # not to Telegram by default
    assert settings.quiet_at(datetime(2026, 1, 1, 6, 59))
    assert not settings.quiet_at(datetime(2026, 1, 1, 7))


def test_the_watcher_notifies_new_approvals_and_failing_sync() -> None:
    center, sent = center_with(NotificationSettings())
    watcher = NoticeWatcher(center)
    waiting = pending(make_record())
    watcher.on_signals(SignalsSnapshot())
    watcher.on_signals(SignalsSnapshot(signals=(waiting,)))
    watcher.on_signals(SignalsSnapshot(signals=(waiting,)))
    assert sent == ["toast:approval_needed"]
    watcher.on_sync(SyncStatus(SyncState.UP_TO_DATE))
    watcher.on_sync(SyncStatus(SyncState.OFFLINE, pending=3, message="Offline"))
    assert sent[-1] == "toast:sync_failing"
    watcher.on_error("boom")
    watcher.on_error("boom again")
    assert sent.count("toast:error") == 1


def test_settings_survive_a_restart_and_keep_only_a_pin_hash(tmp_path: Path) -> None:
    source = NotificationSettingsSource(tmp_path)
    settings = source.settings.model_copy(update={"chat_ids": [7]}).with_pin("2468")
    source.save(settings)
    again = NotificationSettingsSource(tmp_path).settings
    assert again.chat_ids == [7] and again.pin_matches("2468") and not again.pin_matches("1111")
    assert "2468" not in (tmp_path / "notifications.json").read_text()
    with pytest.raises(ValueError):
        settings.with_pin("12")
    with pytest.raises(ValueError):
        NotificationSettings(quiet_start="25:00")


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.updates: list[Mapping[str, Any]] = []

    def __call__(self, url: str, payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            self.sent.append((int(payload["chat_id"]), str(payload["text"])))
            return {"ok": True, "result": {}}
        if method == "getUpdates":
            found, self.updates = self.updates, []
            return {"ok": True, "result": found}
        return {"ok": False, "description": "Unauthorized"}


class Control:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def status(self) -> str:
        return "Mode: Paper"

    def positions(self) -> str:
        return "No open positions or orders."

    def pnl(self) -> str:
        return "Today: +0.00"

    def pause(self) -> str:
        self.calls.append("pause")
        return "paused"

    def resume(self) -> str:
        self.calls.append("resume")
        return "resumed"

    def approve(self, signal_id: str) -> str:
        self.calls.append(f"approve {signal_id}")
        return "approved"

    def kill(self) -> str:
        self.calls.append("kill")
        return "killed"


def bot() -> tuple[TelegramBot, FakeTelegram, Control, list[tuple[str, str, str]], list[float]]:
    fake = FakeTelegram()
    control = Control()
    audits: list[tuple[str, str, str]] = []
    now = [1000.0]
    settings = NotificationSettings(telegram_enabled=True, chat_ids=[42]).with_pin("2468")
    found = TelegramBot(
        TelegramApi("123:abc", fake),
        lambda: settings,
        control,
        audit=lambda action, before, after: audits.append((action, before, after)),
        clock=lambda: now[0],
    )
    return found, fake, control, audits, now


def message(text: str, chat: int = 42) -> dict[str, Any]:
    return {"chat": {"id": chat}, "text": text}


def test_only_whitelisted_unlocked_chats_can_command() -> None:
    telegram, fake, control, audits, now = bot()
    assert telegram.handle(message("/status", chat=99)) == ""
    assert fake.sent == []
    assert telegram.handle(message("/pause")).startswith("Locked")
    assert telegram.handle(message("/pin 1111")) == "Wrong PIN."
    assert telegram.handle(message("/pin 2468")).startswith("Unlocked")
    assert telegram.handle(message("/pause")) == "paused"
    assert audits[-1][0] == "telegram /pause"
    now[0] += 16 * 60
    assert telegram.handle(message("/resume")).startswith("Locked")
    assert control.calls == ["pause"]


def test_the_killswitch_needs_a_confirmation_in_time() -> None:
    telegram, _fake, control, _audits, now = bot()
    telegram.handle(message("/pin 2468"))
    assert "CONFIRM" in telegram.handle(message("/killswitch"))
    now[0] += 61
    assert telegram.handle(message("/killswitch CONFIRM")).startswith("Send /killswitch first")
    telegram.handle(message("/killswitch"))
    assert telegram.handle(message("/killswitch confirm")) == "killed"
    assert control.calls == ["kill"]
    assert "6 characters" in telegram.handle(message("/approve ab"))
    assert telegram.handle(message("/approve abcdef12")) == "approved"


def test_five_wrong_pins_lock_the_chat() -> None:
    telegram, _fake, _control, _audits, _now = bot()
    for _ in range(4):
        telegram.handle(message("/pin 0000"))
    assert "Locked for 15 minutes" in telegram.handle(message("/pin 0000"))
    assert telegram.handle(message("/pin 2468")).startswith("Too many")


def test_polling_reads_updates_and_notices_go_to_every_chat() -> None:
    telegram, fake, _control, _audits, _now = bot()
    fake.updates = [{"update_id": 5, "message": message("/help")}]
    assert telegram.poll_once(timeout=0) == 1
    assert fake.sent[-1][1].startswith("Commands")
    telegram.notify(Notice(EventKind.LIMIT_HIT, "daily loss 3%"))
    assert fake.sent[-1] == (42, "Trading stopped by a limit\ndaily loss 3%")
    with pytest.raises(TelegramError, match="Unauthorized"):
        TelegramApi("x", fake).me()
