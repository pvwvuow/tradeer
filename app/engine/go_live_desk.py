"""The Go-Live desk (Phase 13b, spec C9): the gate's checklist with the app's real data.

It joins the saved backtests, the closed trades, the error log, the health monitor and the
risk settings into a `GateReport` per strategy, records the risk review and the approvals
(each in the audit log), and answers the two questions the app asks: may Auto be switched on
now (`auto_check`, the Positions page) and may Auto send this signal (`guard`, the execution
engine).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.analytics.trades import TradeRecord
from app.core.strategy_settings import StrategySettingsSource
from app.domain.signals import Signal
from app.engine.go_live_gate import (
    GateInputs,
    GateReport,
    GoLiveSource,
    approve,
    auto_block,
    config_hash,
    evaluate_gate,
    params_hash_of,
)
from app.storage.backtest_store import SavedRun
from app.strategies.registry import create_strategy

Audit = Callable[[str, Any, Any], None]
Errors = Callable[[float, int], tuple[int, int]]


def _no_runs() -> Sequence[SavedRun]:
    return ()


def _no_trades() -> Sequence[TradeRecord]:
    return ()


def _no_errors(now: float, days: int) -> tuple[int, int]:
    return 0, 0


def _not_checked() -> str | None:
    return None


def _no_audit(action: str, before: Any, after: Any) -> None:
    return None


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")


@dataclass
class GoLiveDesk:
    gate: GoLiveSource
    strategies: StrategySettingsSource
    risk: Callable[[], BaseModel]
    account: Callable[[], str | None]
    real: Callable[[], bool]
    runs: Callable[[], Sequence[SavedRun]] = _no_runs
    trades: Callable[[], Sequence[TradeRecord]] = _no_trades
    errors: Errors = _no_errors
    record: Audit = _no_audit
    now: Callable[[], float] = time.time
    # The health monitor's verdict: None = not checked yet, "" = all green, else the issues.
    health: Callable[[], str | None] = _not_checked

    @property
    def account_id(self) -> str:
        return self.account() or ""

    def params_hash(self, name: str) -> str:
        """The hash of the params the pipeline runs (the defaults when the saved are invalid)."""
        found = params_hash_of(name, self.strategies.settings.entry(name).params)
        return found or create_strategy(name).params_hash

    def report(self, name: str) -> GateReport:
        state = self.gate.state
        critical, failing = self.errors(self.now(), state.thresholds.error_days)
        inputs = GateInputs(
            strategy=name,
            params_hash=self.params_hash(name),
            runs=tuple(self.runs()),
            trades=tuple(self.trades()),
            critical_errors=critical,
            failing_health=failing,
            risk_hash=config_hash(self.risk()),
            reviewed_risk_hash=state.risk_hash,
            health_now=self.health(),
        )
        return evaluate_gate(inputs, state.thresholds)

    def review_risk(self) -> str:
        state = self.gate.state
        found = config_hash(self.risk())
        self.gate.save(state.with_risk_review(found, self.now()))
        self.record("go-live risk settings reviewed", state.risk_hash or None, found)
        return "Risk settings confirmed as reviewed. A later change needs a new review."

    def approve(self, name: str, typed: str = "") -> tuple[bool, str]:
        report = self.report(name)
        state = self.gate.state
        before = state.approval_for(name, self.account_id)
        found, approval, message = approve(report, state, self.account_id, self.now(), typed)
        if found is None or approval is None:
            return False, message
        self.gate.save(found)
        action = "go-live override" if approval.override else "go-live approved"
        old = before.model_dump(mode="json") if before is not None else None
        self.record(action, old, approval.model_dump(mode="json"))
        return True, message

    def revoke(self, name: str) -> str:
        state = self.gate.state
        before = state.approval_for(name, self.account_id)
        if before is None:
            return f"{name} has no Go-Live approval on this account."
        self.gate.save(state.without_approval(name, self.account_id))
        self.record("go-live approval removed", before.model_dump(mode="json"), None)
        return f"Go-Live approval of {name} removed: Auto no longer sends its signals on REAL."

    def approval_text(self, name: str) -> str:
        found = self.gate.state.approval_for(name, self.account_id)
        if found is None:
            return "Not approved on this account."
        how = "override" if found.override else "all checks passed"
        text = f"Approved {_utc(found.at)} ({how})."
        if found.params_hash != self.params_hash(name):
            text += " The settings changed since: approve again."
        return text

    def guard(self, signal: Signal) -> str:
        """Why Auto may not send `signal` now, "" when it may (the engine's gate)."""
        state = self.gate.state
        account = self.account_id
        return auto_block(state, signal.strategy, signal.params_hash, account, real=self.real())

    def auto_check(self) -> str:
        """Why Auto may not be switched on now, "" when it may."""
        enabled = list(self.strategies.settings.enabled())
        if not enabled:
            return "No strategy is on: turn one on first on the Strategies page."
        if not self.real():
            return ""
        state = self.gate.state
        blocks = [
            auto_block(state, name, self.params_hash(name), self.account_id, real=True)
            for name in enabled
        ]
        return "; ".join(text for text in blocks if text)

    def readiness(self) -> str:
        """One line for the Dashboard."""
        enabled = list(self.strategies.settings.enabled())
        if not enabled:
            return "Go-Live: no strategy is on."
        state = self.gate.state
        parts: list[str] = []
        for name in enabled:
            report = self.report(name)
            approval = state.approval_for(name, self.account_id)
            ok = approval is not None and approval.params_hash == report.params_hash
            parts.append(f"{report.summary}, {'approved' if ok else 'not approved'}")
        where = "REAL account: Auto trades only approved strategies"
        if not self.real():
            where = "demo account: Auto needs no approval here"
        return f"Go-Live ({where}). " + "; ".join(parts) + "."

    def checklist(self) -> GoLiveChecklist:
        """The Dashboard's Go-Live checklist: the seven gate checks, then the approval.

        A check is done only when it passed for every strategy that is on; its detail gives
        each strategy's line. The approval is done when every strategy that is on has an
        approval of its current settings on this account.
        """
        enabled = list(self.strategies.settings.enabled())
        real = self.real()
        block = self.auto_check()
        if not enabled:
            return GoLiveChecklist(real=real, auto_block=block, note="No strategy is on.")
        reports = [self.report(name) for name in enabled]
        items: list[ChecklistItem] = []
        for first in reports[0].checks:
            found = [
                (report.strategy, check)
                for report in reports
                for check in report.checks
                if check.key == first.key
            ]
            lines = [f"{name}: {check.line}" for name, check in found]
            passed = all(check.passed for _, check in found)
            items.append(ChecklistItem(first.key, first.name, passed, "\n".join(lines)))
        state = self.gate.state
        approved: list[str] = []
        lines = []
        for report in reports:
            approval = state.approval_for(report.strategy, self.account_id)
            if approval is not None and approval.params_hash == report.params_hash:
                approved.append(report.strategy)
            lines.append(f"{report.strategy}: {self.approval_text(report.strategy)}")
        everyone = len(approved) == len(reports)
        items.append(ChecklistItem(APPROVAL, "Approval", everyone, "\n".join(lines)))
        return GoLiveChecklist(tuple(items), real=real, auto_block=block)


APPROVAL = "approval"


@dataclass(frozen=True)
class ChecklistItem:
    """One line of the Go-Live checklist over every strategy that is on."""

    key: str
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class GoLiveChecklist:
    items: tuple[ChecklistItem, ...] = ()
    real: bool = True
    auto_block: str = ""  # why Auto may not be switched on now, "" when it may
    note: str = ""  # why there is no list, e.g. "No strategy is on."

    @property
    def done(self) -> int:
        return sum(1 for item in self.items if item.passed)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def passed(self) -> bool:
        return bool(self.items) and self.done == self.total

    @property
    def checks_passed(self) -> bool:
        """Every gate check passed (the approval may follow)."""
        checks = [item for item in self.items if item.key != APPROVAL]
        return bool(checks) and all(item.passed for item in checks)
