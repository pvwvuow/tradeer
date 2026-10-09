"""The AI Lab's experiments (docs/NOCURVE_V2.md 20e3, the inspector's Experiments panel).

Every checked suggestion becomes one numbered experiment (#1, #2, ...) in
`ai_experiments.json`: the changed parameters with their reason, then, after the comparison,
the symbol and period, the verdict (BETTER or NOT BETTER), both runs' main metrics and their
equity curves (at most `CURVE_POINTS` points each), and at the end whether it was activated
in Paper or ignored. Checking the same suggestion again reuses its experiment. Nothing here
changes a setting; a broken file is ignored, never fatal.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

EXPERIMENT_FILE = "ai_experiments.json"
CURVE_POINTS = 120
MAX_EXPERIMENTS = 200
VERSION = 1
UNTESTED = "UNTESTED"
BETTER = "BETTER"
NOT_BETTER = "NOT BETTER"
ACTIVE = "ACTIVE"
IGNORED = "IGNORED"
Change = tuple[str, str, str, str]  # strategy, parameter, now, suggested


def fingerprint(changes: Sequence[Change]) -> str:
    text = json.dumps([list(change) for change in changes], sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def thin(values: Sequence[float], points: int = CURVE_POINTS) -> tuple[float, ...]:
    """At most `points` values, evenly picked, the first and the last always kept."""
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if len(finite) <= points:
        return tuple(finite)
    step = (len(finite) - 1) / (points - 1)
    return tuple(finite[round(index * step)] for index in range(points))


@dataclass(frozen=True)
class Experiment:
    number: int
    created: float
    changes: tuple[Change, ...]
    reason: str = ""
    key: str = ""
    verdict: str = UNTESTED
    symbol: str = ""
    period: str = ""
    current: Mapping[str, float | None] = field(default_factory=dict)
    proposed: Mapping[str, float | None] = field(default_factory=dict)
    current_curve: tuple[float, ...] = ()
    proposed_curve: tuple[float, ...] = ()
    activated: float = 0.0  # when it was activated in Paper; 0 = not
    ignored: bool = False

    @property
    def title(self) -> str:
        parts = [f"{name} {old} \u2192 {new}" for _, name, old, new in self.changes[:2]]
        more = len(self.changes) - 2
        text = ", ".join(parts)
        return f"{text} (+{more})" if more > 0 else text

    @property
    def strategies(self) -> list[str]:
        return sorted({change[0] for change in self.changes})

    @property
    def status(self) -> str:
        if self.activated:
            return ACTIVE
        if self.ignored:
            return IGNORED
        return self.verdict


def _numbers(data: Any) -> dict[str, float | None]:
    if not isinstance(data, dict):
        return {}
    found: dict[str, float | None] = {}
    for key, value in data.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            found[str(key)] = float(value) if math.isfinite(float(value)) else None
        elif value is None:
            found[str(key)] = None
    return found


def _curve(data: Any) -> tuple[float, ...]:
    if not isinstance(data, list):
        return ()
    items = [item for item in data if isinstance(item, int | float) and not isinstance(item, bool)]
    return thin([float(item) for item in items])


def experiment_data(item: Experiment) -> dict[str, Any]:
    return {
        "number": item.number,
        "created": item.created,
        "changes": [list(change) for change in item.changes],
        "reason": item.reason,
        "key": item.key,
        "verdict": item.verdict,
        "symbol": item.symbol,
        "period": item.period,
        "current": dict(item.current),
        "proposed": dict(item.proposed),
        "current_curve": [round(value, 2) for value in item.current_curve],
        "proposed_curve": [round(value, 2) for value in item.proposed_curve],
        "activated": item.activated,
        "ignored": item.ignored,
    }


def experiment_from(data: Any) -> Experiment | None:
    if not isinstance(data, dict):
        return None
    number = data.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    raw = data.get("changes")
    changes: list[Change] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, list) and len(item) == 4:
            a, b, c, d = (str(part) for part in item)
            changes.append((a, b, c, d))
    if not changes:
        return None
    created = data.get("created")
    activated = data.get("activated")
    verdict = data.get("verdict")
    return Experiment(
        number=number,
        created=float(created) if isinstance(created, int | float) else 0.0,
        changes=tuple(changes),
        reason=str(data.get("reason") or ""),
        key=str(data.get("key") or fingerprint(changes)),
        verdict=verdict if verdict in (UNTESTED, BETTER, NOT_BETTER) else UNTESTED,
        symbol=str(data.get("symbol") or ""),
        period=str(data.get("period") or ""),
        current=_numbers(data.get("current")),
        proposed=_numbers(data.get("proposed")),
        current_curve=_curve(data.get("current_curve")),
        proposed_curve=_curve(data.get("proposed_curve")),
        activated=float(activated) if isinstance(activated, int | float) else 0.0,
        ignored=data.get("ignored") is True,
    )


class ExperimentStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @property
    def path(self) -> Path:
        return self.folder / EXPERIMENT_FILE

    def experiments(self) -> list[Experiment]:
        """Newest first."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("experiments") if isinstance(data, dict) else None
        found = [experiment_from(item) for item in items or []]
        return sorted((item for item in found if item is not None), key=lambda e: -e.number)

    def get(self, number: int) -> Experiment | None:
        return next((item for item in self.experiments() if item.number == number), None)

    def start(self, changes: Sequence[Change], reason: str, now: float) -> Experiment | None:
        """The experiment of a checked suggestion: the same untouched one again, or a new one."""
        if not changes:
            return None
        key = fingerprint(changes)
        found = self.experiments()
        for item in found:
            if item.key == key and not item.activated and not item.ignored:
                return item
        number = max((item.number for item in found), default=0) + 1
        item = Experiment(number, now, tuple(changes), reason.strip()[:400], key)
        self._write([item, *found])
        return item

    def update(self, number: int, **values: Any) -> Experiment | None:
        found = self.experiments()
        for index, item in enumerate(found):
            if item.number == number:
                changed = replace(item, **values)
                found[index] = changed
                self._write(found)
                return changed
        return None

    def record_result(
        self,
        number: int,
        *,
        better: bool,
        symbol: str,
        period: str,
        current: Mapping[str, float | None],
        proposed: Mapping[str, float | None],
        current_curve: Sequence[float],
        proposed_curve: Sequence[float],
    ) -> Experiment | None:
        return self.update(
            number,
            verdict=BETTER if better else NOT_BETTER,
            symbol=symbol,
            period=period,
            current=dict(current),
            proposed=dict(proposed),
            current_curve=thin(current_curve),
            proposed_curve=thin(proposed_curve),
        )

    def _write(self, items: Sequence[Experiment]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        ordered = sorted(items, key=lambda e: -e.number)[:MAX_EXPERIMENTS]
        data = {"version": VERSION, "experiments": [experiment_data(item) for item in ordered]}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
        os.replace(temporary, self.path)
