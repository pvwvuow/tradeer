"""Channel feed rules that need no Qt (0.50.4). Pure.

B4: an update still changes the shadow result of its signal, but the card that would act on
a trade you took obeys the channel's settings: none when the channel is off or its
follow-ups are set to never, and no stop-to-entry card when break even is set to never.
B22: a channel's daily loss stop counts from the broker's trading day (New York close,
UTC+2/+3, until the app has measured the broker's clock), as the account's own limit does,
not from UTC midnight.
"""

from __future__ import annotations

from app.channels.policy import Ask, ChannelMode, ChannelPolicy
from app.channels.updates import UpdateKind
from app.core.clock import BrokerClock


def follow_block(policy: ChannelPolicy, kind: UpdateKind) -> str:
    """Why no follow-up card is shown for an update of this kind ("" = show it)."""
    if policy.mode is ChannelMode.OFF:
        return "the channel is off"
    if policy.follow_updates is Ask.NEVER:
        return "its follow-ups are set to never"
    if kind is UpdateKind.BREAK_EVEN and policy.break_even is Ask.NEVER:
        return "its stop to entry is set to never"
    return ""


def channel_day_start(clock: BrokerClock | None, now: float) -> float:
    """UTC start of the broker's trading day at `now`."""
    found = clock if clock is not None and clock.known else BrokerClock.assumed()
    return float(found.day_start_utc(now))
