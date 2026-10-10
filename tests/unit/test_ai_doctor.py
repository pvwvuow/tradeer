"""The AI Lab's app doctor (0.43.1): known problems in the facts and the logs become one
plain finding each, with the one thing to do, the most important first."""

from __future__ import annotations

from app.ai.doctor import INFO, PROBLEM, WARNING, Facts, LogLine, diagnose, log_findings, report


def line(message: str, level: str = "ERROR") -> LogLine:
    return LogLine("2026-10-10T15:06:50", level, "notify", message)


def test_known_log_problems_are_named_once_with_how_often() -> None:
    logs = [
        line("Telegram send failed: <urlopen error [WinError 10060] timed out>"),
        line("Telegram send failed: <urlopen error [Errno 11001] getaddrinfo failed>"),
        line("Order refused: retcode 10027 AutoTrading disabled by client"),
        line("Something nobody knows #1"),
        line("Something nobody knows #2"),
        line("fine", "INFO"),
    ]
    found = log_findings(logs)
    titles = [finding.title for finding in found]
    assert "The Telegram bot cannot reach Telegram (no notices on the phone)" in titles
    assert "MT5 does not let the app trade" in titles
    telegram = next(finding for finding in found if "Telegram bot" in finding.title)
    assert telegram.detail.startswith("2 time(s) in the logs") and "VPN" in telegram.fix
    unknown = [finding for finding in found if "does not know" in finding.title]
    assert len(unknown) == 1 and "(2 time(s))" in unknown[0].title  # numbers shown as #


def test_the_facts_explain_why_nothing_happens() -> None:
    facts = Facts(
        mode="Paper",
        enabled=(),
        halted="daily loss limit",
        reader="error",
        reader_message="Connection to Telegram failed",
        paper_channels=("gg",),
        live_without_budget=("vip",),
    )
    found = diagnose(facts)
    levels = [finding.level for finding in found]
    assert levels == sorted(levels, key=[PROBLEM, WARNING, INFO].index)
    text = report(found, facts)
    assert "No strategy is on" in text and "daily loss limit" in text
    assert "Paper trial: gg" in text and "give gg 100 dollars" in text
    assert "Channel vip is Live without a budget" in text
    assert "reader stopped with an error" in text and "plain words for a beginner" in text


def test_signals_stopped_by_one_check_and_none_traded() -> None:
    facts = Facts(mode="Paper", enabled=("trend_pullback",), signals=10, failed={"spread": 8})
    text = report(diagnose(facts), facts)
    assert "The check 'spread' stopped 8 of 10 signals (80%)" in text
    assert "10 signal(s), none traded" in text
    quiet = Facts(mode="Paper", enabled=("trend_pullback",), signals=0)
    assert "No signal in the last days" in report(diagnose(quiet), quiet)
    healthy = Facts(mode="Paper", enabled=("trend_pullback",), signals=5, traded=2)
    assert "No known problem found" in report(diagnose(healthy), healthy)
