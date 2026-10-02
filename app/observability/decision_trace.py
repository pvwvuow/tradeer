"""Decision traces (spec C5, E3): every step from the closed bar to the final decision.

Each step records what was checked, the value, the threshold and whether it passed. `None`
means the step is information only or was not applied (for example a probability that needs
more history); the reason is in `detail`. A trace is complete when it covers every stage of
the pipeline, so the Signals page can always answer "why?".
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

REQUIRED_STAGES: tuple[str, ...] = (
    "context",
    "strategy",
    "features",
    "probability",
    "ev",
    "filter",
    "risk",
    "decision",
)

Value = float | int | str | bool | None


def _plain(value: Value) -> Value:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _shown(value: Value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


@dataclass(frozen=True)
class TraceStep:
    stage: str
    name: str
    passed: bool | None
    value: Value = None
    threshold: Value = None
    detail: str = ""
    at: float = 0.0

    def text(self) -> str:
        mark = {True: "\u2713", False: "\u2717", None: "\u00b7"}[self.passed]
        parts = [f"{mark} {self.stage}: {self.name}"]
        if self.value is not None or self.threshold is not None:
            limit = f" (limit {_shown(self.threshold)})" if self.threshold is not None else ""
            parts.append(f" = {_shown(self.value)}{limit}")
        if self.detail:
            parts.append(f". {self.detail}")
        return "".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "name": self.name,
            "passed": self.passed,
            "value": _plain(self.value),
            "threshold": _plain(self.threshold),
            "detail": self.detail,
            "at": datetime.fromtimestamp(self.at, UTC).isoformat() if self.at else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TraceStep:
        at = raw.get("at")
        moment = datetime.fromisoformat(at).timestamp() if isinstance(at, str) else 0.0
        return cls(
            stage=str(raw.get("stage", "")),
            name=str(raw.get("name", "")),
            passed=raw.get("passed"),
            value=raw.get("value"),
            threshold=raw.get("threshold"),
            detail=str(raw.get("detail", "")),
            at=moment,
        )


@dataclass
class DecisionTrace:
    signal_id: str
    trace_id: str
    steps: list[TraceStep] = field(default_factory=list)
    final_decision: str = ""

    def add(
        self,
        stage: str,
        name: str,
        passed: bool | None,
        *,
        value: Value = None,
        threshold: Value = None,
        detail: str = "",
        at: float = 0.0,
    ) -> TraceStep:
        step = TraceStep(stage, name, passed, value, threshold, detail, at)
        self.steps.append(step)
        return step

    def extend(self, steps: Sequence[TraceStep]) -> None:
        self.steps.extend(steps)

    def stages(self) -> set[str]:
        return {step.stage for step in self.steps}

    def missing_stages(self) -> list[str]:
        present = self.stages()
        return [stage for stage in REQUIRED_STAGES if stage not in present]

    @property
    def complete(self) -> bool:
        return not self.missing_stages() and bool(self.final_decision)

    def failed(self) -> list[TraceStep]:
        return [step for step in self.steps if step.passed is False]

    def lines(self) -> list[str]:
        lines = [step.text() for step in self.steps]
        if self.final_decision:
            lines.append(f"Decision: {self.final_decision}")
        return lines

    def steps_json(self) -> list[dict[str, Any]]:
        return [step.to_dict() for step in self.steps]

    def to_json(self) -> str:
        return json.dumps(self.steps_json(), ensure_ascii=False)

    @classmethod
    def from_steps(
        cls,
        signal_id: str,
        trace_id: str,
        steps: Sequence[Mapping[str, Any]],
        final_decision: str,
    ) -> DecisionTrace:
        return cls(signal_id, trace_id, [TraceStep.from_dict(raw) for raw in steps], final_decision)
