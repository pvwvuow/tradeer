"""The Telegram bot tells channel signals neatly and takes the buttons (phase 22b, 0.47.0):
HTML messages with the outside text escaped, Approve and Skip only on a card that waits, a
button needs a whitelisted and unlocked chat like a command, and every press is audited."""

from collections.abc import Mapping
from typing import Any

from app.notify.bot_text import (
    APPROVE,
    INFO,
    CardReply,
    SignalAlert,
    alert_buttons,
    format_alert,
    format_notice,
    real_buttons,
)
from app.notify.events import EventKind, Notice
from app.notify.settings import NotificationSettings
from app.notify.telegram import LOCKED_BUTTON, TelegramApi, TelegramBot

REQUEST = "a1b2c3d4e5f60718293a4b5c6d7e8f90abcdef12"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[Mapping[str, Any]] = []

    def __call__(self, url: str, payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, dict(payload)))
        if method == "getUpdates":
            found, self.updates = self.updates, []
            return {"ok": True, "result": found}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 77}}
        return {"ok": True, "result": True}

    def of(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]


class Control:
    def status(self) -> str:
        return "ok"

    def positions(self) -> str:
        return "none"

    def pnl(self) -> str:
        return "0"

    def pause(self) -> str:
        return "paused"

    def resume(self) -> str:
        return "resumed"

    def approve(self, signal_id: str) -> str:
        return "approved"

    def kill(self) -> str:
        return "killed"


def waiting(**changes: Any) -> SignalAlert:
    values: dict[str, Any] = {
        "channel": "Gold Room",
        "symbol": "XAUUSD",
        "side": "BUY LIMIT",
        "rows": (
            ("Entry", "4190.00", ""),
            ("SL", "4180.00", ""),
            ("TP1", "4205.00", "R 1.5"),
        ),
        "size": "LOTS 0.05 \u00b7 RISK 25.00 USD",
        "status": "Waiting for your approval",
        "kind": APPROVE,
        "request_id": REQUEST,
        "expires": "11:40",
    }
    values.update(changes)
    return SignalAlert(**values)


def make_bot() -> tuple[TelegramBot, Recorder, list[tuple[str, str]], list[tuple[str, str, str]]]:
    recorder = Recorder()
    pressed: list[tuple[str, str]] = []
    audits: list[tuple[str, str, str]] = []
    settings = NotificationSettings(telegram_enabled=True, chat_ids=[42]).with_pin("2468")
    bot = TelegramBot(
        TelegramApi("123:abc", recorder),
        lambda: settings,
        Control(),
        audit=lambda action, before, after: audits.append((action, before, after)),
        clock=lambda: 1000.0,
    )

    def handler(action: str, key: str) -> CardReply:
        pressed.append((action, key))
        if action == "ok":
            return CardReply("Approved.", note="\u2705 <b>Approved.</b>", done=True)
        if action == "real":
            return CardReply("REAL.", buttons=real_buttons(REQUEST))
        return CardReply("Skipped.", done=True)

    bot.card_handler = handler
    return bot, recorder, pressed, audits


def press(data: str, chat: int = 42) -> dict[str, Any]:
    message = {"chat": {"id": chat}, "message_id": 9}
    return {"id": "q1", "data": data, "message": message}


def test_a_signal_message_is_neat_html_with_the_outside_text_escaped() -> None:
    text = format_alert(waiting(channel="Gold <b>Room</b> & co"))
    lines = text.splitlines()
    assert lines[0] == "\U0001f7e2 <b>BUY LIMIT XAUUSD</b>"
    assert lines[1] == "\U0001f4e1 Gold &lt;b&gt;Room&lt;/b&gt; &amp; co"
    assert "<code>Entry</code> <code>4190.00</code>" in text
    assert "<code>TP1  </code> <code>4205.00</code>  <i>R 1.5</i>" in text
    assert "\U0001f4bc LOTS 0.05 \u00b7 RISK 25.00 USD" in text
    assert text.endswith("\u23f3 Waiting for your approval \u00b7 expires 11:40")
    sell = format_alert(waiting(side="SELL", kind=INFO, status="Paper trial", expires=""))
    assert sell.startswith("\U0001f534 <b>SELL XAUUSD</b>")
    assert sell.endswith("\u2139\ufe0f Paper trial")
    notice = format_notice(Notice(EventKind.LIMIT_HIT, "daily loss <3%"))
    assert notice == "\U0001f6d1 <b>Trading stopped by a limit</b>\ndaily loss &lt;3%"
    assert alert_buttons(REQUEST)[0][0] == ("\u2705 Approve", f"ok:{REQUEST[:32]}")
    assert max(len(data) for row in alert_buttons(REQUEST) for _, data in row) < 64


def test_only_a_card_that_waits_gets_buttons() -> None:
    bot, recorder, _pressed, _audits = make_bot()
    assert bot.send_alert(waiting()) == 1
    assert bot.send_alert(waiting(kind=INFO, status="Paper trial: counted only")) == 1
    first, second = recorder.of("sendMessage")
    assert first["parse_mode"] == "HTML" and first["chat_id"] == 42
    keyboard = first["reply_markup"]["inline_keyboard"]
    assert [button["text"] for button in keyboard[0]] == ["\u2705 Approve", "\u23ed Skip"]
    assert "reply_markup" not in second


def test_a_button_needs_a_whitelisted_and_unlocked_chat() -> None:
    bot, recorder, pressed, audits = make_bot()
    assert bot.handle_button(press("ok:abc", chat=99)) == ""
    assert recorder.calls == [] and pressed == []
    assert bot.handle_button(press("ok:abc")) == LOCKED_BUTTON
    answer = recorder.of("answerCallbackQuery")[-1]
    assert answer["show_alert"] is True and answer["callback_query_id"] == "q1"
    assert pressed == [] and audits == []
    bot.handle({"chat": {"id": 42}, "text": "/pin 2468"})
    assert bot.handle_button(press("ok:abc")) == "Approved."
    assert pressed == [("ok", "abc")]
    assert audits[-1] == ("telegram button ok", "abc", "chat 42: Approved.")
    removed = recorder.of("editMessageReplyMarkup")[-1]
    assert removed["message_id"] == 9 and removed["reply_markup"] == {"inline_keyboard": []}
    note = recorder.of("sendMessage")[-1]
    assert note["text"] == "\u2705 <b>Approved.</b>" and note["parse_mode"] == "HTML"
    assert note["reply_parameters"]["message_id"] == 9


def test_a_real_order_changes_the_buttons_and_a_failing_handler_is_told() -> None:
    bot, recorder, _pressed, _audits = make_bot()
    bot.handle({"chat": {"id": 42}, "text": "/pin 2468"})
    bot.handle_button(press("real:abc"))
    rows = recorder.of("editMessageReplyMarkup")[-1]["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in rows] == ["\u26a0\ufe0f Yes, send the REAL order", "Cancel"]

    def broken(action: str, key: str) -> CardReply:
        raise RuntimeError("boom")

    bot.card_handler = broken
    assert bot.handle_button(press("ok:abc")).startswith("Something went wrong")
    bot.card_handler = None
    assert "cannot take this button" in bot.handle_button(press("ok:abc"))


def test_polling_reads_button_presses_and_asks_for_them() -> None:
    bot, recorder, pressed, _audits = make_bot()
    bot.handle({"chat": {"id": 42}, "text": "/pin 2468"})
    recorder.updates = [{"update_id": 3, "callback_query": press("no:zzz")}]
    assert bot.poll_once(timeout=0) == 1
    assert pressed == [("no", "zzz")]
    asked = recorder.of("getUpdates")[-1]
    assert asked["allowed_updates"] == ["message", "callback_query"]


def test_notices_are_html_and_plain_replies_stay_plain() -> None:
    bot, recorder, _pressed, _audits = make_bot()
    bot.notify(Notice(EventKind.LIMIT_HIT, "daily loss 3%"))
    sent = recorder.of("sendMessage")[-1]
    assert sent["text"] == "\U0001f6d1 <b>Trading stopped by a limit</b>\ndaily loss 3%"
    assert sent["parse_mode"] == "HTML"
    bot.handle({"chat": {"id": 42}, "text": "/help"})
    assert "parse_mode" not in recorder.of("sendMessage")[-1]
