"""The AI Lab's app doctor (0.43.1, docs/AI_LAB_AGENT.md section 6 "Check the app").

"Something is wrong and I do not know what": the `diagnose` tool reads the facts of the app
(the mode, the strategies that are on, the risk pause, the kill switch, the Telegram reader,
the signals of the last days and why they were filtered out) and the warnings and errors of
the logs, and turns each known problem into one plain finding: what is wrong, and the one
thing to do about it. Pure: the facts come in, the findings go out.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

PROBLEM = "problem"
WARNING = "warning"
INFO = "info"
ORDER = {PROBLEM: 0, WARNING: 1, INFO: 2}
MOST = 0.5  # one check that stops at least half of the signals


@dataclass(frozen=True)
class Known:
    pattern: re.Pattern[str]
    level: str
    title: str
    fix: str


def _known(pattern: str, level: str, title: str, fix: str) -> Known:
    return Known(re.compile(pattern, re.IGNORECASE), level, title, fix)


KNOWN: tuple[Known, ...] = (
    _known(
        r"10027|autotrading|algo ?trading|automated trading is disabled|disable automatic",
        PROBLEM,
        "MT5 does not let the app trade",
        "In MT5 press the Algo Trading button (green), and in Tools > Options > Expert "
        "Advisors untick 'Disable automatic trading through the external Python API'",
    ),
    _known(
        r"api\.telegram\.org|WinError 1006[01]|getaddrinfo failed|telegram.*(timed out|refused)",
        WARNING,
        "The Telegram bot cannot reach Telegram (no notices on the phone)",
        "Turn the VPN on as a system proxy, then Settings > Notifications > Test",
    ),
    _known(
        r"10019|not enough money|no money",
        PROBLEM,
        "The account has not enough free margin for the order",
        "Lower the risk per trade on the Risk page or close other trades",
    ),
    _known(
        r"10016|invalid stops",
        WARNING,
        "The broker refused a stop loss or take profit (too close to the price)",
        "Nothing to change in a hurry: the next signal uses fresh prices; if it repeats, "
        "ask the chat to read the signal's trace",
    ),
    _known(
        r"10018|market is closed|market closed",
        INFO,
        "The market was closed for an order (weekend or holiday)",
        "Nothing to do: orders go again when the market opens",
    ),
    _known(
        r"broker time jumped",
        WARNING,
        "The broker clock jumped",
        "Restart the MT5 terminal; if it repeats on Monday, send the debug bundle (Health)",
    ),
    _known(
        r"mt5.*not connected|terminal is not running|\bipc\b|connection lost|no connection to mt5",
        PROBLEM,
        "The app lost the MT5 terminal",
        "Open MT5, log in to the account, then Settings > Account & connection > Connect",
    ),
    _known(
        r"disk (is )?(full|space|low)|free disk|no space left|errno 28",
        WARNING,
        "The disk is nearly full",
        "Free a few GB on the drive of the app's profile (Windows Settings > Storage)",
    ),
    _known(
        r"telethon (is )?not installed",
        PROBLEM,
        "The Telegram channel reader is missing a part",
        "Update the app (Settings > Updates); the installer brings Telethon",
    ),
    _known(
        r"database is locked",
        PROBLEM,
        "Two copies of the app use the same database",
        "Close every copy of the app (also in the tray) and start it once",
    ),
    _known(
        r"too (few|short).*(bars|history)|history.*(missing|empty|too short)",
        WARNING,
        "MT5 gives too little price history",
        "In MT5 Tools > Options > Charts set Max bars in chart to Unlimited and restart MT5",
    ),
)


@dataclass(frozen=True)
class LogLine:
    time: str
    level: str
    category: str
    message: str


@dataclass(frozen=True)
class Facts:
    mode: str = ""  # the operating mode's label
    analysis_only: bool = False
    enabled: tuple[str, ...] = ()  # strategies that are on
    halted: str = ""  # why the risk limits pause new trades
    stopped: str = ""  # why the kill switch stopped trading
    reader: str = ""  # the channel reader's status, "" without channels
    reader_message: str = ""
    paper_channels: tuple[str, ...] = ()  # on channels in Paper trial
    live_without_budget: tuple[str, ...] = ()
    signals: int = -1  # signals of the last days, -1 = unknown
    traded: int = 0  # of them sent or filled
    failed: Mapping[str, int] = field(default_factory=dict)  # check name -> signals stopped
    logs: Sequence[LogLine] = ()  # warnings and errors of the last day


@dataclass(frozen=True)
class Finding:
    level: str
    title: str
    fix: str
    detail: str = ""

    def line(self) -> str:
        text = f"[{self.level}] {self.title}. What to do: {self.fix}."
        return text + (f" ({self.detail})" if self.detail else "")


def log_findings(logs: Sequence[LogLine]) -> list[Finding]:
    """Known problems in the warnings and errors, each once with how often and when last."""
    hits: dict[int, list[LogLine]] = {}
    other: Counter[str] = Counter()
    for line in logs:
        if line.level.upper() not in ("WARNING", "ERROR", "CRITICAL"):
            continue
        for index, known in enumerate(KNOWN):
            if known.pattern.search(line.message):
                hits.setdefault(index, []).append(line)
                break
        else:
            if line.level.upper() != "WARNING":
                other[re.sub(r"\d+", "#", line.message)[:160]] += 1
    found: list[Finding] = []
    for index, lines in hits.items():
        known = KNOWN[index]
        last = lines[-1]
        detail = f"{len(lines)} time(s) in the logs, last {last.time[:16]}: {last.message[:120]}"
        found.append(Finding(known.level, known.title, known.fix, detail))
    for text, count in other.most_common(3):
        found.append(
            Finding(
                WARNING,
                f"An error the doctor does not know ({count} time(s))",
                "Ask the chat to read the logs about it, or send the debug bundle",
                text,
            ),
        )
    return found


def fact_findings(facts: Facts) -> list[Finding]:
    found: list[Finding] = []
    if facts.stopped:
        found.append(
            Finding(
                PROBLEM,
                "The kill switch stopped trading",
                "Find out why first; then Resume on the Positions & Trades page",
                facts.stopped,
            ),
        )
    if facts.halted:
        found.append(
            Finding(
                WARNING,
                "A risk limit pauses new trades",
                "Wait for the next day or review the limits on the Risk page",
                facts.halted,
            ),
        )
    if facts.analysis_only:
        found.append(
            Finding(
                INFO,
                "The mode is Analysis-only: signals are shown, no order is placed",
                "Switch to Paper on the Positions & Trades page to see fills",
            ),
        )
    if not facts.enabled:
        found.append(
            Finding(
                PROBLEM,
                "No strategy is on, so the app makes no signals",
                "Turn one on on the Strategies page (or ask the chat to propose it)",
            ),
        )
    found += _signal_findings(facts)
    found += _channel_findings(facts)
    return found


def _signal_findings(facts: Facts) -> list[Finding]:
    if facts.signals < 0 or not facts.enabled:
        return []
    if facts.signals == 0:
        return [
            Finding(
                WARNING,
                "No signal in the last days",
                "Check that MT5 is connected and the market is open; the strategies may "
                "simply have found no setup (ask the chat about their rules)",
            ),
        ]
    found: list[Finding] = []
    if facts.failed:
        name, count = max(facts.failed.items(), key=lambda item: item[1])
        if count >= facts.signals * MOST:
            share = 100 * count / facts.signals
            found.append(
                Finding(
                    INFO,
                    f"The check '{name}' stopped {count} of {facts.signals} signals ({share:.0f}%)",
                    "Usually right (it protects you); ask the chat why, before changing it",
                ),
            )
    if facts.traded == 0 and not facts.analysis_only:
        found.append(
            Finding(
                INFO,
                f"{facts.signals} signal(s), none traded",
                "Read why on the Signals page, or ask the chat 'why was nothing traded?'",
            ),
        )
    return found


def _channel_findings(facts: Facts) -> list[Finding]:
    found: list[Finding] = []
    status = facts.reader
    if status in ("phone", "code", "password"):
        found.append(
            Finding(
                PROBLEM,
                "The Telegram channel reader waits for your login",
                "Settings > Telegram channels: enter the phone, the code, the password",
            ),
        )
    elif status == "error":
        found.append(
            Finding(
                PROBLEM,
                "The Telegram channel reader stopped with an error",
                "Turn the VPN on as a system proxy, then Save in Settings > Telegram channels",
                facts.reader_message,
            ),
        )
    elif status == "missing":
        found.append(
            Finding(PROBLEM, "Telethon is missing", "Update the app (Settings > Updates)"),
        )
    if facts.paper_channels:
        names = ", ".join(facts.paper_channels)
        found.append(
            Finding(
                INFO,
                f"Channel(s) in Paper trial: {names}: their signals are counted, no card",
                "Set one to Live with a budget to get order cards (ask the chat: 'give "
                f"{facts.paper_channels[0]} 100 dollars and set it to Live')",
            ),
        )
    for name in facts.live_without_budget:
        found.append(
            Finding(
                WARNING,
                f"Channel {name} is Live without a budget, so it makes no card",
                f"Give it a budget (ask the chat: 'give {name} 100 dollars')",
            ),
        )
    return found


def diagnose(facts: Facts) -> list[Finding]:
    found = fact_findings(facts) + log_findings(facts.logs)
    return sorted(found, key=lambda item: ORDER.get(item.level, 3))


def report(findings: Sequence[Finding], facts: Facts) -> str:
    head = f"Mode {facts.mode or '?'}; strategies on: {', '.join(facts.enabled) or 'none'}."
    if not findings:
        return head + "\nNo known problem found. Everything the doctor checks looks fine."
    problems = sum(1 for item in findings if item.level == PROBLEM)
    lines = [head, f"{len(findings)} finding(s), {problems} problem(s), most important first:"]
    lines += [f"- {item.line()}" for item in findings]
    lines.append(
        "Explain each in plain words for a beginner, the most important first, with its one "
        "next step; use propose where a card can fix it.",
    )
    return "\n".join(lines)
