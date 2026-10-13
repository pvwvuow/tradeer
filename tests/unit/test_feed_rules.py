"""Follow-up cards obey the channel's settings; the daily stop counts from the broker day."""

from __future__ import annotations

from datetime import UTC, datetime

from app.channels.feed_rules import channel_day_start, follow_block
from app.channels.policy import Ask, ChannelMode, ChannelPolicy
from app.channels.updates import UpdateKind
from app.core.clock import BrokerClock, DstScheme


def moment(day: int, hour: int) -> float:
    return datetime(2026, 10, day, hour, tzinfo=UTC).timestamp()


def test_an_off_channel_gets_no_follow_up_card() -> None:
    policy = ChannelPolicy(mode=ChannelMode.OFF)
    assert follow_block(policy, UpdateKind.CLOSE) == "the channel is off"


def test_follow_ups_set_to_never() -> None:
    policy = ChannelPolicy(mode=ChannelMode.LIVE, follow_updates=Ask.NEVER)
    assert follow_block(policy, UpdateKind.NEW_SL)
    assert follow_block(policy, UpdateKind.CLOSE)


def test_break_even_never_blocks_only_stop_to_entry() -> None:
    policy = ChannelPolicy(mode=ChannelMode.LIVE, break_even=Ask.NEVER)
    assert follow_block(policy, UpdateKind.BREAK_EVEN)
    assert follow_block(policy, UpdateKind.CLOSE) == ""
    assert follow_block(policy, UpdateKind.NEW_SL) == ""


def test_the_defaults_show_follow_up_cards() -> None:
    assert follow_block(ChannelPolicy(), UpdateKind.CLOSE) == ""
    assert follow_block(ChannelPolicy(), UpdateKind.BREAK_EVEN) == ""


def test_the_channel_day_starts_at_the_broker_midnight() -> None:
    assert channel_day_start(None, moment(13, 8)) == moment(12, 21)
    assert channel_day_start(None, moment(13, 22)) == moment(13, 21)


def test_a_measured_utc_broker_starts_at_utc_midnight() -> None:
    clock = BrokerClock(0.0, DstScheme.FIXED, measured=True)
    assert channel_day_start(clock, moment(13, 8)) == moment(13, 0)
