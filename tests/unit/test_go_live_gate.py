"""The Go-Live gate (spec C9): every check, the typed override, approvals per config and
account, and the Auto block on REAL accounts."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from app.engine.go_live_gate import (
    GATE_FILE_NAME,
    OVERRIDE_PHRASE,
    GateInputs,
    GateThresholds,
    GoLiveSource,
    GoLiveState,
    approve,
    auto_block,
    config_hash,
    error_counts,
    evaluate_gate,
    load_gate_state,
    params_hash_of,
)
from app.risk.settings import RiskConfig
from app.storage.backtest_store import SavedRun
from app.strategies.registry import create_strategy
from tests.unit.test_ai_export import trade

STRATEGY = "trend_pullback"
HASH = create_strategy(STRATEGY).params_hash
DEFAULTS = create_strategy(STRATEGY).params.model_dump(mode="json")
RISK = config_hash(RiskConfig())


def run(trades: int = 120, expectancy: float = 0.2, params: Any = None) -> SavedRun:
    return SavedRun(
        id="run1",
        created_at=1_790_000_000.0,
        config_id="cfg",
        period="2026-01-01..2026-09-30",
        costs={},
        metrics={"symbol": "EURUSD", "strategies": [STRATEGY], "params": {STRATEGY: params}},
        walk_forward={
            "strategy": STRATEGY,
            "passed": True,
            "cancelled": False,
            "out_of_sample": {"trades": trades, "expectancy_r": expectancy},
        },
        monte_carlo=None,
    )


def paper(count: int = 40, **changes: Any) -> list[Any]:
    found = [trade(number, slippage=0.5) for number in range(count)]
    return [replace(item, **changes) for item in found]


def inputs(**changes: Any) -> GateInputs:
    base = GateInputs(
        strategy=STRATEGY,
        params_hash=HASH,
        runs=[run(params=DEFAULTS)],
        trades=paper(),
        risk_hash=RISK,
        reviewed_risk_hash=RISK,
    )
    return replace(base, **changes)


def check(report: Any, key: str) -> Any:
    return next(item for item in report.checks if item.key == key)


def calibrated(count: int = 40) -> list[Any]:
    # 2 of 3 trades win (see the helper): predicted 0.65 is within 10 points of 66.7%.
    return paper(count, probability=0.65)


def test_everything_ready_passes() -> None:
    report = evaluate_gate(inputs(trades=calibrated()))
    assert report.passed, report.lines
    assert report.failed == [] and report.summary.endswith("(ready)")
    assert [item.key for item in report.checks] == [
        "walk_forward",
        "paper",
        "slippage",
        "calibration",
        "health",
        "risk",
    ]
    assert all(line.startswith("\u2713") for line in report.lines)


def test_the_walk_forward_must_match_these_params_and_be_enough() -> None:
    assert not check(evaluate_gate(inputs(runs=[])), "walk_forward").passed
    few = evaluate_gate(inputs(runs=[run(trades=60, params=DEFAULTS)]))
    assert not check(few, "walk_forward").passed and "60 out-of-sample" in few.lines[0]
    losing = evaluate_gate(inputs(runs=[run(expectancy=-0.1, params=DEFAULTS)]))
    assert not check(losing, "walk_forward").passed
    other = {**DEFAULTS, "min_adx_h1": 30}
    assert not check(evaluate_gate(inputs(runs=[run(params=other)])), "walk_forward").passed
    newest_first = [run(params=other), run(params=DEFAULTS)]
    assert check(evaluate_gate(inputs(runs=newest_first)), "walk_forward").passed


def test_paper_trades_need_a_count_a_positive_expectancy_and_small_slippage() -> None:
    assert not check(evaluate_gate(inputs(trades=paper(10))), "paper").passed
    losers = paper(40, r_multiple=-0.5, net_profit=-5.0)
    assert not check(evaluate_gate(inputs(trades=losers)), "paper").passed
    live = paper(40, mode="live")
    report = evaluate_gate(inputs(trades=live))
    assert not check(report, "paper").passed and not check(report, "slippage").passed
    slow = evaluate_gate(inputs(trades=paper(40, slippage=3.5)))
    assert not check(slow, "slippage").passed and "3.50 points" in check(slow, "slippage").value
    unknown = evaluate_gate(inputs(trades=paper(40, slippage=None)))
    assert check(unknown, "slippage").passed and check(unknown, "slippage").value == "not recorded"


def test_calibration_compares_the_win_rate_with_the_prediction() -> None:
    assert check(evaluate_gate(inputs(trades=calibrated())), "calibration").passed
    too_sure = evaluate_gate(inputs(trades=paper(40, probability=0.9)))
    assert not check(too_sure, "calibration").passed
    none = evaluate_gate(inputs(trades=paper(40, probability=None)))
    assert check(none, "calibration").value == "no rated trades"
    loose = GateThresholds(calibration_band=0.3)
    relaxed = evaluate_gate(inputs(trades=paper(40, probability=0.9)), loose)
    assert check(relaxed, "calibration").passed


def test_errors_health_and_the_risk_review() -> None:
    assert not check(evaluate_gate(inputs(critical_errors=1)), "health").passed
    assert not check(evaluate_gate(inputs(failing_health=2)), "health").passed
    unreviewed = evaluate_gate(inputs(reviewed_risk_hash=""))
    assert check(unreviewed, "risk").value == "not reviewed"
    changed = evaluate_gate(inputs(reviewed_risk_hash="old"))
    assert check(changed, "risk").value == "changed since the review"
    assert not check(changed, "risk").passed


def test_a_failed_gate_needs_the_typed_phrase_and_records_the_override() -> None:
    report = evaluate_gate(inputs(runs=[]))
    state, approval, message = approve(report, GoLiveState(), "acc", 10.0, "yes")
    assert state is None and approval is None and OVERRIDE_PHRASE in message
    state, approval, message = approve(report, GoLiveState(), "acc", 10.0, OVERRIDE_PHRASE)
    assert state is not None and approval is not None
    assert approval.override and approval.failed == ["Walk-forward backtest", "Calibration"]
    assert "override" in message
    passed = evaluate_gate(inputs(trades=calibrated()))
    state2, approval2, _ = approve(passed, GoLiveState(), "acc", 11.0)
    assert state2 is not None and approval2 is not None and not approval2.override


def test_auto_on_real_needs_an_approval_of_this_config_on_this_account() -> None:
    passed = evaluate_gate(inputs(trades=calibrated()))
    state, _, _ = approve(passed, GoLiveState(), "acc", 11.0)
    assert state is not None
    assert auto_block(GoLiveState(), STRATEGY, HASH, "acc", real=False) == ""
    assert "no Go-Live approval" in auto_block(GoLiveState(), STRATEGY, HASH, "acc", real=True)
    assert auto_block(state, STRATEGY, HASH, "acc", real=True) == ""
    assert "no Go-Live approval" in auto_block(state, STRATEGY, HASH, "other", real=True)
    assert "changed since" in auto_block(state, STRATEGY, "newhash", "acc", real=True)
    assert "no Go-Live approval" in auto_block(
        state.without_approval(STRATEGY, "acc"),
        STRATEGY,
        HASH,
        "acc",
        real=True,
    )


def test_the_state_round_trips_and_a_broken_file_approves_nothing(tmp_path: Path) -> None:
    source = GoLiveSource(tmp_path)
    assert source.state == GoLiveState()
    passed = evaluate_gate(inputs(trades=calibrated()))
    state, _, _ = approve(passed, GoLiveState().with_risk_review(RISK, 5.0), "acc", 11.0)
    assert state is not None
    source.save(state)
    again = source.state
    assert again.risk_hash == RISK and again.approval_for(STRATEGY, "acc") is not None
    replaced, _, _ = approve(passed, again, "acc", 12.0)
    assert replaced is not None and len(replaced.approvals) == 1
    (tmp_path / GATE_FILE_NAME).write_text("{broken", encoding="utf-8")
    assert load_gate_state(tmp_path).approvals == []


def test_params_hash_and_config_hash() -> None:
    assert params_hash_of(STRATEGY, DEFAULTS) == HASH
    assert params_hash_of(STRATEGY, {"min_adx_h1": 999}) == ""
    assert params_hash_of("nope", {}) == "" and params_hash_of(STRATEGY, None) == ""
    assert config_hash(RiskConfig()) == RISK and len(RISK) == 16


class FakeDb:
    def __init__(self, critical: int, failing: int) -> None:
        self.answers = [[{"n": critical}], [{"n": failing}]]
        self.calls: list[tuple[str, Any]] = []

    def query(self, sql: str, parameters: Any = ()) -> list[dict[str, int]]:
        self.calls.append((sql, parameters))
        return self.answers[len(self.calls) - 1]


def test_error_counts_read_logs_and_health_checks() -> None:
    db = FakeDb(2, 1)
    assert error_counts(db, 1_790_000_000.0, 7) == (2, 1)
    assert "CRITICAL" in db.calls[0][0] and "health_checks" in db.calls[1][0]
    assert db.calls[1][1][1:] == ("ok", "pass", "passed", "green", "healthy")
