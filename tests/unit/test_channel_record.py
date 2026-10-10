"""Shadow results, stats and the ranking of channels from the local store (docs/SIGNAL_DESK.md
3.5, 3.7 and 3.8, phases 21d2 and 21e)."""

import json
from dataclasses import replace

import numpy as np
import pytest

from app.analysis.bars import Bars
from app.channels.folder import ChannelMessage, Peer, PeerKind
from app.channels.policy import ChannelMode, ChannelPolicy
from app.channels.record import shadow_rs, stats_of, trial_of
from app.channels.shadow_follow import follow_signals
from app.channels.stats import Card, Trade, channel_stats, drawdown, duplicate_of, ranking, trial
from app.domain.signals import Direction
from app.storage.channel_store import ChannelRepository, ChannelSignal
from app.storage.signal_store import iso_time
from tests.unit.storage_helpers import temporary_store

START = 1_791_331_200.0  # 2026-10-07 00:00 UTC
GOLD = Peer(-1001, "Gold Room", PeerKind.CHANNEL)


def rising(symbol: str, start: float, end: float) -> tuple[Bars, float]:
    count = 120
    close = 1.1000 + 0.0005 * np.arange(count)
    bars = Bars.build(
        "EURUSD",
        "M1",
        time=START + 60 * np.arange(count),
        open=np.concatenate(([close[0]], close[:-1])),
        high=close + 0.0001,
        low=close - 0.0001,
        close=close,
        spread=np.zeros(count),
    )
    return bars, 0.00001


def test_every_signal_gets_a_shadow_result_and_the_channel_its_record() -> None:
    with temporary_store() as store:
        repository = ChannelRepository(store)
        (source,) = repository.sync_folder([GOLD], START - 86_400)
        texts = {
            1: "EURUSD buy sl 1.0950 tp 1.1020 tp 1.1040",
            2: "EURUSD sell sl 1.1050 tp 1.0900",
        }
        for message_id, text in texts.items():
            repository.save_message(ChannelMessage(GOLD.id, message_id, START, text), START)
            action, request = ("card", "r1") if message_id == 1 else ("paper", "")
            signal = ChannelSignal(GOLD.id, message_id, START, "EURUSD", "long", action)
            signal = replace(signal, request_id=request)
            repository.save_signal(signal, START)
        skipped = ChannelSignal(GOLD.id, 3, START, "GBPJPY", "long", "skip", "not allowed")
        repository.save_signal(skipped, START)
        assert follow_signals(repository, rising, START + 7200) == 3
        rows = {(row.message_id, row.leg): row for row in repository.shadow(GOLD.id)}
        assert rows[(1, 1)].state == "win" and rows[(1, 2)].state == "win"
        assert rows[(2, 1)].state == "loss" and rows[(2, 1)].r == -1.0
        assert sorted(shadow_rs(repository, GOLD.id)) == pytest.approx([-1.0, 0.6])  # 0.4, 0.8
        assert follow_signals(repository, rising, START + 7300) == 0  # all resolved
        features = json.dumps({"features": {"leg_group": "r1"}})
        with store.db.transaction() as connection:
            connection.execute(
                "INSERT INTO signals (id, features_json, created_at, updated_at) "
                "VALUES ('s1', ?, ?, ?)",
                (features, iso_time(START), iso_time(START)),
            )
            connection.execute(
                "INSERT INTO trades (id, signal_id, magic, open_time, close_time, net_profit, "
                "r_multiple, slippage, created_at, updated_at) "
                "VALUES ('t1', 's1', ?, ?, ?, 14.0, 1.4, 2.0, ?, ?)",
                (
                    source.magic,
                    iso_time(START + 4),
                    iso_time(START + 900),
                    iso_time(START),
                    iso_time(START),
                ),
            )
        policy = ChannelPolicy(mode=ChannelMode.LIVE, budget=100.0)
        found = stats_of(repository, source, policy)
        assert (found.signals, found.taken, found.skipped) == (3, 1, 0)
        assert found.refused == {"not allowed": 1}
        assert found.trades == 1 and found.win_rate == 1.0 and found.profit == 14.0
        assert found.profit_percent == 14.0 and found.latency == 4.0 and found.slippage == 2.0
        assert found.shadow_signals == 2 and found.shadow_win_rate == 0.5
        assert any(line.startswith("Your trades 1: win rate 100%") for line in found.lines())
        assert not trial_of(repository, source, START).done
        assert trial_of(repository, source, START + 14 * 86_400).done


def test_follow_ups_close_or_protect_the_shadow() -> None:
    with temporary_store() as store:
        repository = ChannelRepository(store)
        repository.sync_folder([GOLD], START)
        text = "EURUSD buy sl 1.0950 tp 1.2000"
        repository.save_message(ChannelMessage(GOLD.id, 1, START, text), START)
        repository.save_signal(ChannelSignal(GOLD.id, 1, START, "EURUSD", "long", "paper"), START)
        assert repository.mark_signal(GOLD.id, 1, "close_at", START + 600)
        follow_signals(repository, rising, START + 7200)
        (row,) = repository.shadow(GOLD.id)
        assert row.state == "closed" and row.r is not None and 0 < row.r < 1


def test_the_pure_numbers() -> None:
    trades = [Trade(10.0, 1.0, None, None), Trade(-4.0, -1.0, None, None)]
    found = channel_stats(["card", "skip"], ["noise"], 1, trades, [0.5], 0.0)
    assert found.profit_factor == 2.5 and found.profit_percent is None
    assert drawdown([10.0, -4.0, -3.0, 5.0]) == 7.0
    many = channel_stats(["paper"] * 20, [], 0, [], [0.3] * 20, 100.0)
    few = channel_stats(["paper"], [], 0, [], [2.0], 100.0)
    assert ranking([("Few", few), ("Many", many)]) == [
        "1. Many: +0.30 R over 20 signals",
        "Few: too few signals to rank",
    ]
    assert trial([1.0] * 20, START, START).done
    assert trial([], START, START + 3 * 86_400).text().startswith("Paper trial: 0 of 20")
    first = Card("a", "Gold Room", "XAUUSD", Direction.LONG, 2345.0, START)
    again = Card("b", "FX Pro", "XAUUSD", Direction.LONG, 2345.8, START + 60)
    assert duplicate_of(again, [first], 5.0) == first
    assert duplicate_of(again, [first], 1.0) is None  # 0.8 is more than 0.2 ATR
    late = Card("c", "FX Pro", "XAUUSD", Direction.LONG, 2345.0, START + 3600)
    assert duplicate_of(late, [first], 5.0) is None
    same = Card("d", "Gold Room", "XAUUSD", Direction.LONG, 2345.0, START + 60)
    assert duplicate_of(same, [first], 5.0) is None  # the same channel twice is no duplicate
