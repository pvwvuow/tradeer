"""The Go-Live desk (spec C9) with real settings files: the risk review, approvals and
overrides in the audit log, Auto checks on REAL and demo accounts, and the engine's guard."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.strategy_settings import StrategyEntry, StrategySettingsSource
from app.engine.go_live_desk import GoLiveDesk
from app.engine.go_live_gate import OVERRIDE_PHRASE, GoLiveSource
from app.risk.settings import RiskConfig
from tests.unit.signal_helpers import make_signal
from tests.unit.test_go_live_gate import DEFAULTS, HASH, STRATEGY, calibrated, check, run

NOW = 1_790_000_000.0
OTHER = "london_breakout"


def make_desk(
    tmp_path: Path,
    *,
    real: bool = True,
    ready: bool = True,
) -> tuple[GoLiveDesk, list[tuple[str, Any, Any]]]:
    source = StrategySettingsSource(tmp_path)
    settings = source.settings.with_entry(STRATEGY, StrategyEntry(enabled=True, params={}))
    source.save(settings.with_entry(OTHER, StrategyEntry(enabled=False, params={})))
    audits: list[tuple[str, Any, Any]] = []
    runs = [run(params=DEFAULTS)] if ready else []
    trades = calibrated() if ready else []

    def record(action: str, before: Any, after: Any) -> None:
        audits.append((action, before, after))

    desk = GoLiveDesk(
        gate=GoLiveSource(tmp_path),
        strategies=source,
        risk=RiskConfig,
        account=lambda: "acc",
        real=lambda: real,
        runs=lambda: runs,
        trades=lambda: trades,
        record=record,
        now=lambda: NOW,
    )
    return desk, audits


def signal(params_hash: str = HASH) -> Any:
    return replace(make_signal(), strategy=STRATEGY, params_hash=params_hash)


def test_a_ready_strategy_is_approved_and_auto_may_trade_it(tmp_path: Path) -> None:
    desk, audits = make_desk(tmp_path)
    assert desk.params_hash(STRATEGY) == HASH
    assert not check(desk.report(STRATEGY), "risk").passed
    desk.review_risk()
    assert audits[-1][0] == "go-live risk settings reviewed"
    assert desk.report(STRATEGY).passed
    assert "no Go-Live approval" in desk.auto_check()
    assert "no Go-Live approval" in desk.guard(signal())
    ok, message = desk.approve(STRATEGY)
    assert ok and "passed" in message and audits[-1][0] == "go-live approved"
    assert desk.auto_check() == "" and desk.guard(signal()) == ""
    assert desk.approval_text(STRATEGY).endswith("(all checks passed).")
    assert ", approved" in desk.readiness() and "REAL account" in desk.readiness()


def test_a_failed_gate_needs_the_override_phrase(tmp_path: Path) -> None:
    desk, audits = make_desk(tmp_path, ready=False)
    ok, message = desk.approve(STRATEGY)
    assert not ok and OVERRIDE_PHRASE in message and audits == []
    ok, message = desk.approve(STRATEGY, OVERRIDE_PHRASE)
    assert ok and "override" in message
    action, before, after = audits[-1]
    assert action == "go-live override" and before is None and after["override"]
    assert "Walk-forward backtest" in after["failed"]
    assert "(override)" in desk.approval_text(STRATEGY)


def test_changed_settings_need_a_new_approval(tmp_path: Path) -> None:
    desk, _ = make_desk(tmp_path, ready=False)
    assert desk.approve(STRATEGY, OVERRIDE_PHRASE)[0]
    changed = desk.strategies.settings.with_entry(
        STRATEGY,
        StrategyEntry(enabled=True, params={**DEFAULTS, "min_adx_h1": 30}),
    )
    desk.strategies.save(changed)
    new_hash = desk.params_hash(STRATEGY)
    assert new_hash != HASH
    assert "changed since" in desk.auto_check()
    assert "approve again" in desk.approval_text(STRATEGY)
    assert "changed since" in desk.guard(signal(new_hash))
    assert ", not approved" in desk.readiness()


def test_a_demo_account_needs_no_approval_but_a_strategy_must_be_on(tmp_path: Path) -> None:
    desk, _ = make_desk(tmp_path, real=False, ready=False)
    assert desk.auto_check() == "" and desk.guard(signal()) == ""
    assert "demo account" in desk.readiness()
    off = desk.strategies.settings.with_entry(STRATEGY, StrategyEntry(enabled=False, params={}))
    desk.strategies.save(off)
    assert "No strategy is on" in desk.auto_check()
    assert desk.readiness() == "Go-Live: no strategy is on."


def test_removing_an_approval_blocks_auto_again(tmp_path: Path) -> None:
    desk, audits = make_desk(tmp_path, ready=False)
    assert "no Go-Live approval" in desk.revoke(STRATEGY)
    desk.approve(STRATEGY, OVERRIDE_PHRASE)
    assert "removed" in desk.revoke(STRATEGY)
    assert audits[-1][0] == "go-live approval removed" and audits[-1][2] is None
    assert "no Go-Live approval" in desk.auto_check()
    assert desk.approval_text(STRATEGY) == "Not approved on this account."


def test_errors_and_failing_health_checks_fail_the_gate(tmp_path: Path) -> None:
    desk, _ = make_desk(tmp_path)
    desk.review_risk()
    desk.errors = lambda now, days: (1, 0)
    report = desk.report(STRATEGY)
    assert not report.passed and report.failed == ["Errors and health"]
