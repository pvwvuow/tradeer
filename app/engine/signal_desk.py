"""The Signal desk's requests and results (docs/SIGNAL_DESK.md 2.3 to 2.6).

The AI Lab (and later a channel) hands the pipeline a `DeskRequest`: the parsed signal and
where it came from. In the analysis thread the pipeline plans it against the live price,
sizes it, splits it into one leg per target and runs every leg through the normal path; the
`DeskResult` says what happened, in words the card can show. Pure helpers only: no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.signals.legs import split
from app.signals.parse import ParsedSignal
from app.signals.plan import OrderPlan
from app.storage.ids import stable_id

MARKET_MINUTES = 15  # a market signal is stale after this: the card asks for a new one
PENDING_MINUTES = 480  # a pending order waits at most 8 hours
KEEP_RESULTS = 20
REASON_CHARACTERS = 200


@dataclass(frozen=True)
class DeskRequest:
    id: str
    parsed: ParsedSignal
    source: str = "pasted"  # "pasted", later "channel:<id>"
    market_minutes: int = MARKET_MINUTES
    pending_minutes: int = PENDING_MINUTES


@dataclass(frozen=True)
class DeskResult:
    request_id: str
    ok: bool  # every leg waits for your confirmation
    message: str
    plan: OrderPlan | None = None
    signal_ids: tuple[str, ...] = ()
    at: float = 0.0


def new_request(parsed: ParsedSignal, now: float, source: str = "pasted") -> DeskRequest:
    return DeskRequest(stable_id("desk", source, parsed.text, f"{now:.3f}"), parsed, source)


def desk_reason(request: DeskRequest) -> str:
    """The signal's reason: where it came from and its text on one short line."""
    text = " ".join(request.parsed.text.split())
    if len(text) > REASON_CHARACTERS:
        text = text[: REASON_CHARACTERS - 1] + "\u2026"
    return f"{request.source}: {text}"


def leg_shares(
    total: float,
    targets: int,
    min_lot: float,
    step: float,
) -> tuple[float, ...]:
    """Each leg's share of one trade's risk: the lots split per target (fewer legs when a
    leg would be under the minimum lot), as fractions of the total. Without a size (no risk
    manager, or a rejection) the targets share it evenly, or one leg carries the rejection."""
    if targets <= 0:
        return ()
    if not (math.isfinite(total) and total > 0 and min_lot > 0 and step > 0):
        return tuple(1.0 / targets for _ in range(targets))
    volumes = split(total, targets, min_lot, step)
    if not volumes:
        return (1.0,)
    whole = sum(volumes)
    return tuple(volume / whole for volume in volumes)


def summary(states: Sequence[str], reasons: Sequence[str]) -> tuple[bool, str]:
    """The card's line: how many legs wait for you, or why they did not."""
    waiting = sum(1 for state in states if state == "PENDING_APPROVAL")
    if states and waiting == len(states):
        legs = "order waits" if waiting == 1 else f"{waiting} orders wait"
        return True, f"{legs} for your confirmation"
    why = "; ".join(sorted({reason for reason in reasons if reason})) or "not sent"
    if waiting:
        return False, f"{waiting} of {len(states)} orders wait; the others: {why}"
    return False, why
