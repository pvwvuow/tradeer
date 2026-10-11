"""The money given to the channels, for the Dashboard and the Risk page (phase 22g)."""

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from app.channels.overview import (
    DAILY_STOP,
    DRAWDOWN_STOP,
    Channel,
    build_overview,
)
from app.channels.policy import ChannelMode, ChannelMoney, ChannelPolicy


@dataclass(frozen=True)
class Source:
    channel_id: int
    title: str
    magic: int
    enabled: bool = True
    in_folder: bool = True


class Repo:
    def __init__(self) -> None:
        self.sources = [
            Source(1, "Gold Room", 26_071_001),
            Source(2, "FX Room", 26_071_002),
            Source(3, "Off Room", 26_071_003),
            Source(4, "Unfunded", 26_071_004),
            Source(5, "Gone", 26_071_005, in_folder=False),
            Source(6, "Disabled", 26_071_006, enabled=False),
        ]
        self.policies = {
            1: ChannelPolicy(mode=ChannelMode.LIVE, budget=100.0, daily_loss_percent=5.0),
            2: ChannelPolicy(mode=ChannelMode.PAPER, budget=50.0),
            3: ChannelPolicy(mode=ChannelMode.OFF, budget=80.0),
            4: ChannelPolicy(mode=ChannelMode.LIVE),
            5: ChannelPolicy(mode=ChannelMode.LIVE, budget=10.0),
            6: ChannelPolicy(mode=ChannelMode.LIVE, budget=10.0),
        }
        self.days: list[float] = []

    def channels(self) -> Sequence[Channel]:
        return self.sources

    def policy(self, channel_id: int) -> ChannelPolicy:
        return self.policies[channel_id]

    def money(self, magic: int, day_start: float) -> ChannelMoney:
        self.days.append(day_start)
        if magic == 26_071_001:
            return ChannelMoney(closed=-12.0, today=-6.0)
        return ChannelMoney()


def test_only_channels_that_are_on_with_a_budget_are_shown() -> None:
    repo = Repo()
    found = build_overview(repo, 86_400.0 * 5 + 3_600.0)
    assert [line.title for line in found.lines] == ["Gold Room", "FX Room"]
    assert set(repo.days) == {86_400.0 * 5}  # the day starts at 00:00 UTC
    gold, paper = found.lines
    assert gold.live and gold.equity == 88.0 and gold.today == -6.0
    assert gold.risk_money == pytest.approx(1.76)
    assert gold.stop == DAILY_STOP  # 6 of 88 is over 5%
    assert not paper.live and paper.equity == 50.0 and paper.stop == ""
    assert (found.budget, found.equity, found.closed, found.today, found.live) == (
        150.0,
        138.0,
        -12.0,
        -6.0,
        1,
    )


def test_a_big_loss_is_a_drawdown_stop() -> None:
    repo = Repo()
    repo.policies[1] = ChannelPolicy(mode=ChannelMode.LIVE, budget=100.0, drawdown_percent=10.0)
    (gold, _paper) = build_overview(repo, 0.0).lines
    assert gold.stop == DRAWDOWN_STOP
    assert build_overview(Repo(), 0.0) != build_overview(repo, 0.0)
