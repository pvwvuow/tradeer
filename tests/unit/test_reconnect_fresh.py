"""0.50.3 (PC log of 12 October 2026): a long run of failed reconnects starts the MT5 link
fresh, a restart does not tell "trade opened" again, and a channel signal whose symbol was
not watched goes on once it is."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.channels.folder import ChannelMessage
from app.channels.reading import ReadResult
from app.engine.execution import ExecutionSnapshot, PositionView
from app.mt5.api import MT5Api
from app.mt5.checklist import ConnectRequest
from app.mt5.connection import FRESH_EVERY, _reconnect, start_fresh
from app.notify.events import EventKind, execution_notices, known_again
from app.ui.channel_feed import SYMBOL_WAIT_SECONDS, ChannelFeed, Reading


class Terminal:
    """Records what the reconnect asked of the MT5 library and the helper."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def close(self) -> None:
        self.calls.append("close")

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append("initialize")
        return True

    def terminal_info(self) -> None:
        return None


def request() -> ConnectRequest:
    found = SimpleNamespace(timeout_ms=60_000, terminal_path="", login=None, password="", server="")
    return cast(ConnectRequest, found)


def test_every_third_reconnect_starts_the_link_fresh() -> None:
    assert FRESH_EVERY == 3
    plain = Terminal()
    _reconnect(cast(MT5Api, plain), request())
    assert plain.calls == ["initialize"]
    fresh = Terminal()
    _reconnect(cast(MT5Api, fresh), request(), True)
    assert fresh.calls == ["shutdown", "close", "initialize"]
    start_fresh(cast(MT5Api, SimpleNamespace(shutdown=lambda: None)))  # no helper to close


def view(ticket: int) -> PositionView:
    return PositionView(
        "live",
        ticket,
        "EURUSD",
        "sell",
        0.09,
        1.1192,
        1.12171,
        1.11553,
        0.0,
        "london_breakout",
        False,
    )


def test_a_restart_does_not_tell_trade_opened_again() -> None:
    old = ExecutionSnapshot(messages=("08:25:00 UTC  older line",))
    resumed = "08:26:13 UTC  Resumed managing 1 bot trade(s) after a restart"
    new = ExecutionSnapshot(positions=(view(34969404),), messages=(resumed, *old.messages))
    assert execution_notices(old, new) == []
    assert known_again(old.messages, new.messages) == (True, set())
    adopted = "08:26:13 UTC  Adopted bot position 5 EURUSD (london_breakout, live): no signal"
    new = ExecutionSnapshot(positions=(view(5), view(6)), messages=(adopted,))
    kinds = [(notice.kind, notice.text.split()[2]) for notice in execution_notices(old, new)]
    assert kinds == [(EventKind.TRADE_OPENED, "EURUSD")]
    assert known_again(old.messages, new.messages) == (False, {5})


def test_a_reading_waits_for_its_symbol_and_goes_on_once_it_is_watched() -> None:
    shown: list[object] = []
    lines: list[str] = []
    watched = ["EURUSD"]
    clock = [1000.0]
    message = ChannelMessage(7, 2498, 990.0, "AUD buy 0.696 sl 0.693 tp 0.699")
    outcome = ReadResult("signal", "AUDUSD", "buy", "", (0.696,), 0.693, (0.699,))
    reading = Reading(7, message, (message.text,), outcome, model="grok")
    feed = SimpleNamespace(
        symbol_waits=[(clock[0], reading)],
        now=lambda: clock[0],
        desk=SimpleNamespace(symbols=lambda: tuple(watched)),
        repository=SimpleNamespace(policy=lambda channel: SimpleNamespace(aliases={})),
        log=lambda level, text: lines.append(text),
        show_read=shown.append,
    )
    assert ChannelFeed.retry_symbols(cast(ChannelFeed, feed)) == 0
    assert len(feed.symbol_waits) == 1
    watched.append("AUDUSD")
    clock[0] += 60
    assert ChannelFeed.retry_symbols(cast(ChannelFeed, feed)) == 1
    assert feed.symbol_waits == [] and len(shown) == 1
    again = shown[0]
    assert isinstance(again, Reading) and again.model == ""  # nothing is spent twice
    feed.symbol_waits = [(clock[0] - SYMBOL_WAIT_SECONDS - 1, reading)]
    assert ChannelFeed.retry_symbols(cast(ChannelFeed, feed)) == 0
    assert feed.symbol_waits == []
