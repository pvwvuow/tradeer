"""A channel's record from the local store (docs/SIGNAL_DESK.md 3.5 and 3.8): its signals,
the trades you took (by its magic), their latency and slippage and the shadow results of
every signal, turned into `app.channels.stats` numbers for the page and the ranking."""

from __future__ import annotations

from collections import defaultdict

from app.channels.policy import ChannelPolicy
from app.channels.stats import ChannelStats, Trade, Trial, channel_stats, trial
from app.storage.channel_store import ChannelRepository, ChannelSource

RESOLVED = ("win", "loss", "timeout", "closed")


def shadow_rs(repository: ChannelRepository, channel_id: int) -> list[float]:
    """One R per resolved shadow signal: the mean of its resolved legs."""
    legs: dict[int, list[float]] = defaultdict(list)
    open_signals: set[int] = set()
    for row in repository.shadow(channel_id):
        if row.state in RESOLVED and row.r is not None:
            legs[row.message_id].append(row.r)
        elif row.state in ("waiting", "open"):
            open_signals.add(row.message_id)
    return [sum(rs) / len(rs) for message_id, rs in legs.items() if message_id not in open_signals]


def stats_of(
    repository: ChannelRepository,
    source: ChannelSource,
    policy: ChannelPolicy,
) -> ChannelStats:
    signals = repository.signals(source.channel_id)
    by_request = {signal.request_id: signal for signal in signals if signal.request_id}
    opened = repository.opened(source.magic)
    taken = sum(1 for request in by_request if request in opened)
    trades: list[Trade] = []
    for profit, r, slippage, group in repository.channel_trades(source.magic):
        signal = by_request.get(group)
        latency: float | None = None
        if signal is not None and group in opened:
            latency = opened[group] - signal.date
        trades.append(Trade(profit, r, slippage, latency))
    deleted, edited = repository.edited_after(source.channel_id)
    return channel_stats(
        [signal.action for signal in signals],
        [signal.reason for signal in signals if signal.action == "skip"],
        taken,
        trades,
        shadow_rs(repository, source.channel_id),
        policy.budget,
        deleted=deleted,
        edited_after=edited,
    )


def trial_of(repository: ChannelRepository, source: ChannelSource, now: float) -> Trial:
    return trial(shadow_rs(repository, source.channel_id), source.first_seen, now)
