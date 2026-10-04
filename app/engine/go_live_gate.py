"""The Go-Live gate (spec C9): the readiness checklist before Auto mode may trade a strategy
and config on a REAL account.

Five checks, thresholds configurable (saved per profile in `go_live.json`):

1. Walk-forward: the newest saved backtest of this config with a walk-forward has at least
   100 out-of-sample trades and a positive out-of-sample expectancy.
2. Paper trades: at least 30 paper trades of the strategy with a positive expectancy.
3. Slippage: their mean slippage stays within the assumption (points).
4. Calibration: the actual win rate is within the band of the mean predicted probability.
5. Health: no CRITICAL error and no failing health check in the last 7 days, and the risk
   settings were reviewed and confirmed (unchanged since).

An approval is stored per strategy, params hash and account. When a check fails the user can
still approve, only by typing the override phrase; the approval then says it was an override
and which checks failed, and the caller writes it to the audit log. The gate is pure apart
from the small JSON file; the execution engine asks `auto_block` before an Auto order.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.analytics.trades import Database, TradeRecord
from app.storage.backtest_store import SavedRun
from app.storage.signal_store import iso_time
from app.strategies.registry import STRATEGIES, create_strategy

GATE_FILE_NAME = "go_live.json"
OVERRIDE_PHRASE = "I ACCEPT THE RISK"
DAY = 86_400
HEALTHY = ("ok", "pass", "passed", "green", "healthy")


class GateThresholds(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_walk_forward_trades: int = Field(
        default=100,
        ge=1,
        le=100_000,
        description="Out-of-sample walk-forward trades at least",
    )
    min_paper_trades: int = Field(
        default=30,
        ge=1,
        le=100_000,
        description="Paper trades at least",
    )
    max_slippage_points: float = Field(
        default=2.0,
        ge=0,
        le=1000,
        description="Mean paper slippage at most (points)",
    )
    calibration_band: float = Field(
        default=0.10,
        gt=0,
        le=1,
        description="Actual win rate within this distance of the predicted one",
    )
    error_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Days without a CRITICAL error or failing health check",
    )


def _thresholds() -> GateThresholds:
    return GateThresholds()


class Approval(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategy: str
    params_hash: str
    account: str = ""
    at: float = 0.0
    override: bool = False
    failed: list[str] = Field(default_factory=list)


def _approvals() -> list[Approval]:
    return []


class GoLiveState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thresholds: GateThresholds = Field(default_factory=_thresholds)
    risk_hash: str = ""
    risk_reviewed_at: float = 0.0
    approvals: list[Approval] = Field(default_factory=_approvals)

    def approval_for(self, strategy: str, account: str) -> Approval | None:
        for item in self.approvals:
            if item.strategy == strategy and item.account == account:
                return item
        return None

    def with_approval(self, approval: Approval) -> GoLiveState:
        kept = [
            item
            for item in self.approvals
            if not (item.strategy == approval.strategy and item.account == approval.account)
        ]
        return self.model_copy(update={"approvals": [*kept, approval]})

    def without_approval(self, strategy: str, account: str) -> GoLiveState:
        kept = [
            item
            for item in self.approvals
            if not (item.strategy == strategy and item.account == account)
        ]
        return self.model_copy(update={"approvals": kept})

    def with_risk_review(self, risk_hash: str, at: float) -> GoLiveState:
        return self.model_copy(update={"risk_hash": risk_hash, "risk_reviewed_at": at})


def load_gate_state(directory: Path) -> GoLiveState:
    """The saved gate state; a missing or broken file gives no approvals (the safe side)."""
    try:
        raw = json.loads((directory / GATE_FILE_NAME).read_text(encoding="utf-8"))
        return GoLiveState.model_validate(raw)
    except (OSError, ValueError, ValidationError):
        return GoLiveState()


def save_gate_state(directory: Path, state: GoLiveState) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / GATE_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class GoLiveSource:
    """The gate state of one profile (read from the file each time: it is tiny)."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def state(self) -> GoLiveState:
        return load_gate_state(self._directory)

    def save(self, state: GoLiveState) -> None:
        save_gate_state(self._directory, state)


def config_hash(value: BaseModel) -> str:
    """A short stable hash of a settings model (to see whether it changed since a review)."""
    raw = json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def params_hash_of(strategy: str, params: Any) -> str:
    """The strategy's params hash, "" when the params are not valid for it."""
    if strategy not in STRATEGIES or not isinstance(params, dict):
        return ""
    try:
        return create_strategy(strategy, params).params_hash
    except (KeyError, ValueError):
        return ""


@dataclass(frozen=True)
class GateCheck:
    key: str
    name: str
    passed: bool
    value: str
    needed: str
    detail: str = ""

    @property
    def line(self) -> str:
        mark = "\u2713" if self.passed else "\u2717"
        text = f"{mark} {self.name}: {self.value} (needs {self.needed})"
        return f"{text}. {self.detail}" if self.detail else text


@dataclass(frozen=True)
class GateReport:
    strategy: str
    params_hash: str
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed(self) -> list[str]:
        return [check.name for check in self.checks if not check.passed]

    @property
    def lines(self) -> list[str]:
        return [check.line for check in self.checks]

    @property
    def summary(self) -> str:
        done = sum(1 for check in self.checks if check.passed)
        state = "ready" if self.passed else "not ready"
        return f"{self.strategy}: {done} of {len(self.checks)} Go-Live checks passed ({state})"


@dataclass(frozen=True)
class GateInputs:
    strategy: str
    params_hash: str
    runs: Sequence[SavedRun] = ()  # newest first
    trades: Sequence[TradeRecord] = ()
    critical_errors: int = 0
    failing_health: int = 0
    risk_hash: str = ""
    reviewed_risk_hash: str = ""


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _number(value: float | None, digits: int = 3) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:+.{digits}f}"


def _walk_forward(inputs: GateInputs, limits: GateThresholds) -> GateCheck:
    needed = f">= {limits.min_walk_forward_trades} trades, expectancy > 0"
    name = "Walk-forward backtest"
    for run in inputs.runs:
        walk = run.walk_forward
        if not walk or walk.get("strategy") != inputs.strategy or walk.get("cancelled"):
            continue
        params = (run.metrics.get("params") or {}).get(inputs.strategy)
        if params_hash_of(inputs.strategy, params) != inputs.params_hash:
            continue
        out = walk.get("out_of_sample") or {}
        trades = int(out.get("trades") or 0)
        expectancy = out.get("expectancy_r")
        value = float(expectancy) if isinstance(expectancy, int | float) else None
        passed = trades >= limits.min_walk_forward_trades and value is not None and value > 0
        found = f"{trades} out-of-sample trades, {_number(value)} R"
        return GateCheck("walk_forward", name, passed, found, needed, run.title)
    detail = "Run a backtest with walk-forward for this strategy on the Backtest page."
    return GateCheck("walk_forward", name, False, "none for these params", needed, detail)


def _paper(inputs: GateInputs, limits: GateThresholds) -> tuple[GateCheck, GateCheck]:
    paper = [t for t in inputs.trades if t.strategy == inputs.strategy and t.mode == "paper"]
    expectancy = _mean([t.r_multiple for t in paper if t.r_multiple is not None])
    enough = len(paper) >= limits.min_paper_trades
    positive = expectancy is not None and expectancy > 0
    trades = GateCheck(
        "paper",
        "Paper trades",
        enough and positive,
        f"{len(paper)} trades, {_number(expectancy)} R",
        f">= {limits.min_paper_trades} trades, expectancy > 0",
    )
    slips = [abs(t.slippage) for t in paper if t.slippage is not None]
    mean = _mean(slips)
    passed = bool(paper) and (mean is None or mean <= limits.max_slippage_points)
    found = "no paper trades" if not paper else "not recorded"
    if mean is not None:
        found = f"{mean:.2f} points on average"
    slippage = GateCheck(
        "slippage",
        "Paper slippage",
        passed,
        found,
        f"<= {limits.max_slippage_points:g} points",
    )
    return trades, slippage


def _calibration(inputs: GateInputs, limits: GateThresholds) -> GateCheck:
    rated = [
        t for t in inputs.trades if t.strategy == inputs.strategy and t.probability is not None
    ]
    needed = f"within {limits.calibration_band * 100:.0f} points, >= {limits.min_paper_trades}"
    if not rated:
        return GateCheck("calibration", "Calibration", False, "no rated trades", needed)
    predicted = _mean([t.probability for t in rated if t.probability is not None]) or 0.0
    actual = sum(1 for t in rated if t.win) / len(rated)
    gap = abs(actual - predicted)
    passed = len(rated) >= limits.min_paper_trades and gap <= limits.calibration_band
    found = f"{actual * 100:.1f}% won vs {predicted * 100:.1f}% predicted over {len(rated)} trades"
    return GateCheck("calibration", "Calibration", passed, found, needed)


def _health(inputs: GateInputs, limits: GateThresholds) -> GateCheck:
    passed = inputs.critical_errors == 0 and inputs.failing_health == 0
    found = f"{inputs.critical_errors} CRITICAL errors, {inputs.failing_health} failing checks"
    needed = f"none in {limits.error_days} days"
    return GateCheck("health", "Errors and health", passed, found, needed)


def _risk(inputs: GateInputs) -> GateCheck:
    reviewed = bool(inputs.reviewed_risk_hash) and inputs.reviewed_risk_hash == inputs.risk_hash
    found = "reviewed" if reviewed else "not reviewed"
    if inputs.reviewed_risk_hash and not reviewed:
        found = "changed since the review"
    return GateCheck("risk", "Risk settings", reviewed, found, "reviewed and confirmed")


def evaluate_gate(inputs: GateInputs, limits: GateThresholds | None = None) -> GateReport:
    found = limits or GateThresholds()
    trades, slippage = _paper(inputs, found)
    checks = (
        _walk_forward(inputs, found),
        trades,
        slippage,
        _calibration(inputs, found),
        _health(inputs, found),
        _risk(inputs),
    )
    return GateReport(inputs.strategy, inputs.params_hash, checks)


def error_counts(db: Database, now: float, days: int) -> tuple[int, int]:
    """CRITICAL log lines and failing health checks since `days` days ago."""
    since = iso_time(now - days * DAY)
    critical = db.query(
        "SELECT COUNT(*) AS n FROM app_logs WHERE level = 'CRITICAL' AND time >= ?",
        (since,),
    )
    marks = ", ".join("?" for _ in HEALTHY)
    failing = db.query(
        "SELECT COUNT(*) AS n FROM health_checks WHERE time >= ? "
        f"AND LOWER(COALESCE(status, '')) NOT IN ({marks})",
        (since, *HEALTHY),
    )
    return int(critical[0]["n"]) if critical else 0, int(failing[0]["n"]) if failing else 0


def approve(
    report: GateReport,
    state: GoLiveState,
    account: str,
    now: float,
    typed: str = "",
) -> tuple[GoLiveState | None, Approval | None, str]:
    """Record an approval: always when every check passed, else only with the typed phrase."""
    override = not report.passed
    if override and typed.strip() != OVERRIDE_PHRASE:
        failed = ", ".join(report.failed)
        return None, None, f"Not approved: {failed} failed. Type {OVERRIDE_PHRASE} to override."
    approval = Approval(
        strategy=report.strategy,
        params_hash=report.params_hash,
        account=account,
        at=now,
        override=override,
        failed=report.failed,
    )
    how = "override (checks failed: " + ", ".join(report.failed) + ")" if override else "passed"
    message = f"Go-Live approved for {report.strategy}: {how}"
    return state.with_approval(approval), approval, message


def auto_block(
    state: GoLiveState,
    strategy: str,
    params_hash: str,
    account: str,
    *,
    real: bool,
) -> str:
    """Why Auto may not send this strategy's signal now, "" when it may.

    A demo or contest account needs no approval; a REAL account needs an approval of
    exactly this config (params hash) on exactly this account.
    """
    if not real:
        return ""
    found = state.approval_for(strategy, account)
    if found is None:
        return f"{strategy} has no Go-Live approval on this REAL account"
    if found.params_hash != params_hash:
        return f"{strategy} changed since its Go-Live approval: approve the new settings again"
    return ""
