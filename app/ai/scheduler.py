"""When the AI Desk calls the AI (AI Desk spec 3.1 and 8.1): a fixed cadence inside the
chosen sessions plus events, never when the markets are closed, the MT5 link is down, the
data is stale, nothing is new, the budget is used up or the service keeps failing.

Pure: the caller passes the state and gets (run, reason) back; the other strategies never
wait for the AI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

MIN_CADENCE_MINUTES = 15
BACKOFF_SECONDS: tuple[float, ...] = (300.0, 900.0, 3600.0)
RATE_LIMIT_SECONDS = 60.0
OFFLINE_AFTER = 3


def clock_text(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime("%H:%M UTC")


@dataclass
class Backoff:
    """5, 15 and 60 minutes after failures; offline after three in a row."""

    failures: int = 0
    until: float = 0.0

    def failed(self, now: float, retry_after: float | None = None) -> float:
        """Count a failure and return when the next call may go."""
        self.failures += 1
        step = BACKOFF_SECONDS[min(self.failures, len(BACKOFF_SECONDS)) - 1]
        wait = step if retry_after is None else max(retry_after, 1.0)
        self.until = now + wait
        return self.until

    def succeeded(self) -> None:
        self.failures = 0
        self.until = 0.0

    @property
    def offline(self) -> bool:
        return self.failures >= OFFLINE_AFTER

    def waiting(self, now: float) -> bool:
        return now < self.until


@dataclass(frozen=True)
class DeskState:
    now: float
    enabled: bool
    link_up: bool
    markets_open: bool
    in_session: bool
    cadence_minutes: int
    last_run: float | None  # last call, UTC seconds
    last_bar: float  # close time of the newest M15 bar
    answered_bar: float | None  # newest M15 bar the last answer saw
    event: str = ""  # a fill, +1R or -0.5R, news soon, a session open or end
    manual: bool = False  # Run now
    budget: str = ""  # why the budget stops the call, or ""
    backoff_until: float = 0.0


def cadence_seconds(minutes: int) -> float:
    return max(minutes, MIN_CADENCE_MINUTES) * 60.0


def decide(state: DeskState) -> tuple[bool, str]:
    """(run now, reason): the reason is shown on the page next to the status chip."""
    if not state.enabled:
        return False, "The AI Desk is off."
    if state.budget:
        return False, state.budget
    if not state.link_up:
        return False, "MetaTrader 5 is not connected."
    if not state.markets_open:
        return False, "The markets are closed: no call, no cost."
    if state.manual:
        return True, "Run now."
    if state.now < state.backoff_until:
        return False, f"Waiting after an error until {clock_text(state.backoff_until)}."
    if state.event:
        return True, f"Event: {state.event}."
    if not state.in_session:
        return False, "Outside the chosen sessions."
    if state.answered_bar is not None and state.answered_bar >= state.last_bar:
        return False, "Nothing new since the last answer."
    if state.last_run is None:
        return True, "First run."
    due = state.last_run + cadence_seconds(state.cadence_minutes)
    if state.now >= due:
        return True, "Cadence."
    return False, f"Next run at {clock_text(due)}."
