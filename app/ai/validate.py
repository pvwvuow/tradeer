"""Checks every AI idea and position note before anything uses it (AI Desk spec 3.3).

Pure: the live prices, ATR and broker limits come in, verdicts with every reason go out. A
refused idea is still shadow-tracked by the caller; nothing in an answer is ever run.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.ai.schema import Idea, PositionNote


@dataclass(frozen=True)
class SymbolState:
    symbol: str
    digits: int
    point: float
    bid: float
    ask: float
    atr_m15: float
    stops_level: int = 0  # points
    freeze_level: int = 0  # points
    volume_min: float = 0.01
    volume_step: float = 0.01
    market_open: bool = True
    fresh: bool = True


@dataclass(frozen=True)
class OpenPosition:
    ticket: int
    symbol: str
    side: str  # "buy" or "sell"
    entry: float
    sl: float  # 0 = no stop
    volume: float


@dataclass(frozen=True)
class Limits:
    min_reward_risk: float = 1.2
    min_stop_atr: float = 0.5
    max_stop_atr: float = 4.0
    market_drift_atr: float = 0.3
    pending_distance_atr: float = 2.0
    min_expiry_minutes: int = 15
    max_expiry_minutes: int = 480
    max_ideas: int = 2
    min_partial: float = 0.25
    max_partial: float = 0.75


@dataclass(frozen=True)
class IdeaVerdict:
    idea: Idea  # prices rounded to the symbol's digits
    reasons: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class NoteVerdict:
    note: PositionNote
    reasons: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.reasons


def sign_of(side: str) -> int:
    return 1 if side == "buy" else -1


def _rounded(idea: Idea, digits: int) -> Idea:
    return idea.model_copy(
        update={
            "symbol": idea.symbol.upper(),
            "entry": round(idea.entry, digits),
            "sl": round(idea.sl, digits),
            "tp": round(idea.tp, digits),
        },
    )


def _pending_side(idea: Idea, state: SymbolState) -> str:
    """Why a limit or stop entry is on the wrong side of the price, or ""."""
    price = state.ask if idea.side == "buy" else state.bid
    above = idea.entry > price
    below = idea.entry < price
    if idea.order == "limit" and idea.side == "buy" and not below:
        return "a buy limit must be below the ask"
    if idea.order == "limit" and idea.side == "sell" and not above:
        return "a sell limit must be above the bid"
    if idea.order == "stop" and idea.side == "buy" and not above:
        return "a buy stop must be above the ask"
    if idea.order == "stop" and idea.side == "sell" and not below:
        return "a sell stop must be below the bid"
    return ""


def _prices(idea: Idea, state: SymbolState, limits: Limits) -> list[str]:
    reasons: list[str] = []
    atr = state.atr_m15
    if not (math.isfinite(atr) and atr > 0):
        return ["no ATR for this symbol"]
    sign = sign_of(idea.side)
    risk = (idea.entry - idea.sl) * sign
    reward = (idea.tp - idea.entry) * sign
    if risk <= 0:
        reasons.append("the stop is not on the losing side")
    if reward <= 0:
        reasons.append("the target is not on the winning side")
    if risk > 0 and reward > 0 and reward / risk < limits.min_reward_risk:
        rr = reward / risk
        reasons.append(f"reward to risk {rr:.2f} is under {limits.min_reward_risk:g}")
    if risk > 0:
        stop_atr = risk / atr
        if not limits.min_stop_atr <= stop_atr <= limits.max_stop_atr:
            allowed = f"{limits.min_stop_atr:g} to {limits.max_stop_atr:g}"
            reasons.append(f"the stop is {stop_atr:.2f} ATR away (allowed {allowed})")
        spread_points = (state.ask - state.bid) / state.point if state.point > 0 else 0.0
        if state.point > 0 and risk / state.point < state.stops_level + spread_points:
            reasons.append("the stop is inside the broker's stops level plus the spread")
    price = state.ask if idea.side == "buy" else state.bid
    distance = abs(idea.entry - price) / atr
    if idea.order == "market" and distance > limits.market_drift_atr:
        reasons.append(f"the market entry is {distance:.2f} ATR from the live price")
    if idea.order != "market":
        wrong = _pending_side(idea, state)
        if wrong:
            reasons.append(wrong)
        if distance > limits.pending_distance_atr:
            reasons.append(f"the entry is {distance:.2f} ATR from the price")
    return reasons


def check_ideas(
    ideas: Sequence[Idea],
    symbols: Mapping[str, SymbolState],
    *,
    busy: Iterable[tuple[str, str]] = (),
    now: float = 0.0,
    market_close: float | None = None,
    limits: Limits | None = None,
) -> list[IdeaVerdict]:
    """A verdict for every idea, in order. `busy` holds the (symbol, side) pairs that
    already have an AI signal waiting or an open AI position."""
    rules = limits or Limits()
    taken = {(symbol.upper(), side) for symbol, side in busy}
    verdicts: list[IdeaVerdict] = []
    for index, idea in enumerate(ideas):
        symbol = idea.symbol.upper()
        state = symbols.get(symbol)
        if state is None:
            verdicts.append(IdeaVerdict(idea, ("the symbol is not watched",)))
            continue
        rounded = _rounded(idea, state.digits)
        reasons: list[str] = []
        if index >= rules.max_ideas:
            reasons.append(f"more than {rules.max_ideas} ideas in one answer")
        if not state.market_open:
            reasons.append("the market is closed")
        if not state.fresh:
            reasons.append("the price data is stale")
        reasons += _prices(rounded, state, rules)
        minutes = rounded.expires_minutes
        if not rules.min_expiry_minutes <= minutes <= rules.max_expiry_minutes:
            allowed = f"{rules.min_expiry_minutes} to {rules.max_expiry_minutes}"
            reasons.append(f"it expires in {minutes} minutes (allowed {allowed})")
        elif market_close is not None and now + minutes * 60 > market_close:
            reasons.append("it expires after the market closes")
        if (symbol, rounded.side) in taken:
            reasons.append("an AI signal or trade is already open on this symbol and side")
        if not reasons:
            taken.add((symbol, rounded.side))
        verdicts.append(IdeaVerdict(rounded, tuple(reasons)))
    return verdicts


def _part(volume: float, fraction: float, step: float) -> float:
    size = step if step > 0 else 0.01
    return math.floor(volume * fraction / size + 1e-9) * size


def _note(
    note: PositionNote,
    position: OpenPosition,
    state: SymbolState,
    rules: Limits,
) -> NoteVerdict:
    reasons: list[str] = []
    sign = sign_of(position.side)
    if note.action == "tighten_sl":
        if note.new_sl is None:
            return NoteVerdict(note, ("tighten_sl needs new_sl",))
        new_sl = round(note.new_sl, state.digits)
        note = note.model_copy(update={"new_sl": new_sl})
        if position.sl > 0 and (new_sl - position.sl) * sign <= 0:
            reasons.append("the new stop is not closer: a stop is never moved away")
        price = state.bid if position.side == "buy" else state.ask
        gap = (price - new_sl) * sign
        if gap <= 0:
            reasons.append("the new stop is on the wrong side of the price")
        elif state.point > 0 and gap / state.point < max(state.stops_level, state.freeze_level):
            reasons.append("the new stop is inside the broker's stops or freeze level")
    elif note.action == "partial":
        fraction = note.partial_fraction
        if fraction is None or not rules.min_partial <= fraction <= rules.max_partial:
            allowed = f"{rules.min_partial:g} to {rules.max_partial:g}"
            reasons.append(f"partial needs partial_fraction from {allowed}")
        else:
            part = _part(position.volume, fraction, state.volume_step)
            rest = position.volume - part
            if part < state.volume_min - 1e-9 or rest < state.volume_min - 1e-9:
                reasons.append("the part or the rest would be under the minimum lot")
    return NoteVerdict(note, tuple(reasons))


def check_notes(
    notes: Sequence[PositionNote],
    positions: Mapping[int, OpenPosition],
    symbols: Mapping[str, SymbolState],
    limits: Limits | None = None,
) -> list[NoteVerdict]:
    """A verdict for every position note; `positions` holds the open bot positions."""
    rules = limits or Limits()
    verdicts: list[NoteVerdict] = []
    for note in notes:
        position = positions.get(note.ticket)
        if position is None:
            verdicts.append(NoteVerdict(note, ("the ticket is not an open bot position",)))
            continue
        state = symbols.get(position.symbol.upper())
        if state is None:
            verdicts.append(NoteVerdict(note, ("the symbol is not watched",)))
            continue
        verdicts.append(_note(note, position, state, rules))
    return verdicts
