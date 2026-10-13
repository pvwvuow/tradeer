"""Channel stats show the Paper trial signals too (B7, 0.50.4)."""

from __future__ import annotations

from app.channels.stats import channel_stats


def test_paper_signals_are_counted() -> None:
    found = channel_stats(["card", "paper", "paper", "skip"], ["not a signal"], 1, [], [], 0.0)
    assert found.paper == 2
    assert found.skipped == 0
    assert "Paper trial 2" in found.lines()[0]


def test_no_paper_line_without_paper_signals() -> None:
    found = channel_stats(["card"], [], 1, [], [], 0.0)
    assert "Paper trial" not in found.lines()[0]
