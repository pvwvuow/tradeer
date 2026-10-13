"""Auto-take: off by default, turned on or off by one chat line (0.50.5)."""

from __future__ import annotations

from app.channels.auto_take import channel_named, read_auto
from app.channels.policy import ChannelPolicy, policy_from_json

TITLES = ["Araz Forex | Signals", "Gold Room", "Gold Room VIP"]


def test_auto_take_is_off_by_default_and_saved() -> None:
    assert not ChannelPolicy().auto_take
    saved = ChannelPolicy(auto_take=True).to_json()
    assert policy_from_json(saved).auto_take


def test_the_chat_turns_it_on_and_off() -> None:
    assert read_auto("Gold Room VIP auto-take on", TITLES) == ("Gold Room VIP", True)
    assert read_auto("Gold Room auto off", TITLES) == ("Gold Room", False)
    assert read_auto("Gold Room autotake start", TITLES) == ("Gold Room", True)


def test_the_first_part_of_a_title_is_enough() -> None:
    assert channel_named("araz forex auto on", TITLES) == "Araz Forex | Signals"
    assert read_auto("Araz Forex auto-take on", TITLES) == ("Araz Forex | Signals", True)


def test_other_lines_are_not_auto_take() -> None:
    assert read_auto("Gold Room live", TITLES) is None
    assert read_auto("auto on", TITLES) is None
    assert read_auto("Gold Room auto-take", TITLES) is None
