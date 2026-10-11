"""The money given to the Telegram channels, for the main pages (phase 22g, 0.48.0).

The Dashboard and the Risk page show it next to the account's own figures: each channel with a
budget, what it is worth now (its budget plus what its closed trades made), what it made
today, the risk of one trade and a stop when its daily loss or drawdown limit is reached
(`app.channels.policy`). Pure: the repository comes in through a small protocol.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.channels.policy import ChannelMode, ChannelMoney, ChannelPolicy, budget_state

DAY = 86_400.0
DAILY_STOP = "daily stop"
DRAWDOWN_STOP = "drawdown stop"


class Channel(Protocol):
    @property
    def channel_id(self) -> int: ...

    @property
    def title(self) -> str: ...

    @property
    def magic(self) -> int: ...

    @property
    def enabled(self) -> bool: ...

    @property
    def in_folder(self) -> bool: ...


class Repository(Protocol):
    def channels(self) -> Sequence[Channel]: ...

    def policy(self, channel_id: int) -> ChannelPolicy: ...

    def money(self, magic: int, day_start: float) -> ChannelMoney: ...


@dataclass(frozen=True)
class ChannelLine:
    title: str
    live: bool  # Live (cards ask for money) or only a Paper trial with a budget saved
    budget: float
    closed: float  # net result of the channel's closed trades
    today: float  # the part of it closed today (UTC)
    equity: float  # budget + closed
    risk_money: float  # what one trade may risk
    risk_percent: float
    stop: str = ""  # DAILY_STOP, DRAWDOWN_STOP or ""


@dataclass(frozen=True)
class Overview:
    lines: tuple[ChannelLine, ...] = ()

    @property
    def budget(self) -> float:
        return sum(line.budget for line in self.lines)

    @property
    def equity(self) -> float:
        return sum(line.equity for line in self.lines)

    @property
    def closed(self) -> float:
        return sum(line.closed for line in self.lines)

    @property
    def today(self) -> float:
        return sum(line.today for line in self.lines)

    @property
    def live(self) -> int:
        return sum(1 for line in self.lines if line.live)


def build_overview(repository: Repository, now: float) -> Overview:
    """Every channel that is on and has a budget, in the order of its magic number."""
    day_start = math.floor(now / DAY) * DAY
    lines: list[ChannelLine] = []
    for channel in repository.channels():
        if not channel.enabled or not channel.in_folder:
            continue
        policy = repository.policy(channel.channel_id)
        if policy.mode is ChannelMode.OFF or policy.budget <= 0:
            continue
        money = repository.money(channel.magic, day_start)
        state = budget_state(policy, money)
        stop = DRAWDOWN_STOP if state.drawdown_stop else DAILY_STOP if state.daily_stop else ""
        lines.append(
            ChannelLine(
                title=channel.title,
                live=policy.mode is ChannelMode.LIVE,
                budget=policy.budget,
                closed=money.closed,
                today=money.today,
                equity=state.equity,
                risk_money=state.risk_money,
                risk_percent=policy.risk_percent,
                stop=stop,
            ),
        )
    return Overview(tuple(lines))
