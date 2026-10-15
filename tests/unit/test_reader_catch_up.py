"""Catch-up after the app was closed or Telegram was cut (0.51.2, docs/AI_AUTOPILOT.md B9)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from app.channels.folder import ChannelMessage
from app.channels.reader import FRESH_SECONDS, ChannelReader
from app.channels.settings import ChannelSettings

NOW = 1_800_000_000.0


class Store:
    def __init__(self) -> None:
        self.saved: dict[tuple[int, int], ChannelMessage] = {}
        self.on = {1, 2}

    def read_ids(self) -> set[int]:
        return set(self.on)

    def messages(self, channel_id: int, limit: int) -> list[Any]:
        mine = sorted(
            (m for (c, _), m in self.saved.items() if c == channel_id),
            key=lambda m: -m.message_id,
        )
        return [SimpleNamespace(message_id=m.message_id) for m in mine[:limit]]

    def save_message(self, message: ChannelMessage, now: float) -> bool:
        key = (message.channel_id, message.message_id)
        if key in self.saved:
            return False
        self.saved[key] = message
        return True


class Api:
    def __init__(self, missed: list[ChannelMessage]) -> None:
        self.missed = missed
        self.asked: list[tuple[int, int]] = []
        self.fail = False

    async def history(
        self,
        channel_id: int,
        after_id: int,
        limit: int,
        photos_since: float,
    ) -> list[ChannelMessage]:
        if self.fail:
            raise OSError("cut")
        self.asked.append((channel_id, after_id))
        found = [m for m in self.missed if m.channel_id == channel_id and m.message_id > after_id]
        return found[:limit]


def message(channel: int, number: int, age: float) -> ChannelMessage:
    return ChannelMessage(channel, number, NOW - age, f"message {number}")


def reader_with(store: Store, api: Api, logs: list[str]) -> tuple[ChannelReader, list[int]]:
    handled: list[int] = []
    reader = ChannelReader(
        ChannelSettings,
        None,
        cast(Any, None),
        cast(Any, store),
        log=lambda level, text: logs.append(text),
        utc_now=lambda: NOW,
    )
    reader._api = cast(Any, api)
    reader.add_message_listener(lambda found: handled.append(found.message_id))
    return reader, handled


def test_missed_messages_are_stored_and_only_young_ones_handled() -> None:
    store = Store()
    store.save_message(message(1, 10, 3600), NOW)  # the last one before the cut
    api = Api([message(1, 11, FRESH_SECONDS + 60), message(1, 12, 30), message(2, 5, 10)])
    logs: list[str] = []
    reader, handled = reader_with(store, api, logs)
    assert asyncio.run(reader.catch_up()) == 2
    assert (1, 11) in store.saved and (1, 12) in store.saved
    assert (2, 5) not in store.saved  # channel 2 had nothing stored: it starts with its next
    assert handled == [12]
    assert api.asked == [(1, 10)]
    assert asyncio.run(reader.catch_up()) == 0  # nothing is handled twice
    assert handled == [12]


def test_a_failure_is_told_once() -> None:
    store = Store()
    store.save_message(message(1, 10, 60), NOW)
    api = Api([])
    api.fail = True
    logs: list[str] = []
    reader, _handled = reader_with(store, api, logs)
    asyncio.run(reader.catch_up())
    asyncio.run(reader.catch_up())
    assert sum("catch-up failed" in line for line in logs) == 1
