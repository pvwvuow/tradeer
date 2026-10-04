"""AI suggestions (spec C13): the schema check, the difference, the new config version,
the backtest verdict and the Paper-only rule."""

import json

from app.analytics.ai_import import (
    CREATED_BY,
    NO_CHANGES,
    RunSummary,
    activation_block,
    apply_suggestion,
    audit_row,
    compare_runs,
    config_rows,
    diff_rows,
    parse_suggestion,
    summary_rows,
)
from app.core.strategy_settings import StrategyEntry, StrategySettings, build_strategies
from app.domain.modes import OperatingMode
from app.storage.signal_store import config_id
from app.strategies.registry import STRATEGIES, create_strategy
from tests.unit.storage_helpers import temporary_store

ANSWER = """Your trend strategy loses in weak trends. Here is the change:

```json
{"changes": [{"strategy": "trend_pullback", "params": {"min_adx_h1": 25, "reward_r": 2.5},
  "reason": "ADX under 25 lost 0.3 R per trade", "expected_impact": "fewer, better trades"}]}
```
"""


def payload(**item: object) -> str:
    return json.dumps({"changes": [item]})


def change(params: dict[str, object], strategy: str = "trend_pullback") -> str:
    return payload(strategy=strategy, params=params, reason="why")


def test_a_fenced_answer_becomes_a_checked_difference() -> None:
    found = parse_suggestion(ANSWER, StrategySettings())
    assert found.valid and found.problems == ()
    assert found.strategies == ["trend_pullback"]
    item = found.changes[0]
    assert item.reason.startswith("ADX under 25")
    assert item.expected_impact == "fewer, better trades"
    assert diff_rows(found) == [
        ["trend_pullback", "min_adx_h1", "20", "25"],
        ["trend_pullback", "reward_r", "2", "2.5"],
    ]
    assert item.old_params["min_adx_h1"] == 20 and item.params["min_adx_h1"] == 25
    assert item.params["slow_ema_h1"] == item.old_params["slow_ema_h1"]


def test_a_bare_list_or_object_is_accepted() -> None:
    settings = StrategySettings()
    item = {"strategy": "trend_pullback", "params": {"rsi_level": 55}, "reason": "x"}
    listed = json.dumps([item])
    single = json.dumps(item)
    for text in (listed, single, "Sure! " + single + " Good luck."):
        assert parse_suggestion(text, settings).valid


def test_everything_outside_the_schema_is_refused_with_a_reason() -> None:
    settings = StrategySettings()
    cases = {
        "not json at all": "Not valid JSON",
        change({"min_adx_h1": 25}, strategy="martingale"): "unknown strategy martingale",
        change({"lot_size": 5}): "unknown parameter(s) lot_size",
        change({"min_adx_h1": 500}): "min_adx_h1",
        change({"min_adx_h1": 20}): "the suggested values are the current ones",
        payload(strategy="trend_pullback", params={"rsi_level": 55}): "needs a reason",
        payload(strategy="trend_pullback", params={}, reason="x"): "at least one parameter",
    }
    for text, expected in cases.items():
        found = parse_suggestion(text, settings)
        assert not found.valid and not found.changes, text
        assert expected in found.problems[0], (text, found.problems)
    assert parse_suggestion('{"changes": []}', settings).problems == (NO_CHANGES,)


def test_a_strategy_twice_keeps_the_first_and_reports_the_second() -> None:
    first = {"strategy": "trend_pullback", "params": {"rsi_level": 55}, "reason": "a"}
    second = {"strategy": "trend_pullback", "params": {"rsi_level": 60}, "reason": "b"}
    found = parse_suggestion(json.dumps({"changes": [first, second]}), StrategySettings())
    assert len(found.changes) == 1 and "appears twice" in found.problems[0]
    assert not found.valid


def test_the_difference_is_against_the_saved_params() -> None:
    saved = StrategySettings().with_entry(
        "trend_pullback",
        StrategyEntry(enabled=False, params={"min_adx_h1": 30}),
    )
    found = parse_suggestion(change({"min_adx_h1": 25}), saved)
    assert diff_rows(found) == [["trend_pullback", "min_adx_h1", "30", "25"]]
    applied = apply_suggestion(saved, found)
    entry = applied.entry("trend_pullback")
    assert entry.params["min_adx_h1"] == 25 and entry.enabled is False
    assert applied.entry("london_breakout") == saved.entry("london_breakout")
    other = parse_suggestion(change({"rsi_level": 55}), StrategySettings())
    built, notes = build_strategies(apply_suggestion(StrategySettings(), other))
    assert notes == [] and {s.name for s in built} == set(STRATEGIES)


def test_a_new_config_version_links_to_the_one_it_replaces() -> None:
    found = parse_suggestion(ANSWER, StrategySettings())
    rows = config_rows(found, None)
    assert len(rows) == 1
    row = rows[0]
    version = STRATEGIES["trend_pullback"].version
    old_hash = create_strategy("trend_pullback").params_hash
    assert row["created_by"] == CREATED_BY and row["is_active"] is True
    assert row["parent_config_id"] == config_id("trend_pullback", version, old_hash)
    assert row["id"] != row["parent_config_id"]
    assert row["params_json"]["min_adx_h1"] == 25
    assert "Expected impact: fewer, better trades" in row["notes"]


def test_config_and_audit_rows_fit_the_database() -> None:
    found = parse_suggestion(ANSWER, StrategySettings())
    verdict = compare_runs(summary(40, 0.1, 5.0), summary(40, 0.2, 5.0))
    with temporary_store() as store:
        for row in config_rows(found, None):
            store.upsert("strategy_configs", row)
        store.upsert("audit_log", audit_row(found, None, 1_790_000_000.0, verdict))
        configs = store.db.query("SELECT created_by, parent_config_id FROM strategy_configs")
        audits = store.db.query("SELECT source, action, before_json, after_json FROM audit_log")
    assert [row["created_by"] for row in configs] == [CREATED_BY]
    assert len(audits) == 1 and audits[0]["source"] == CREATED_BY
    before = json.loads(audits[0]["before_json"])
    after = json.loads(audits[0]["after_json"])
    assert before["trend_pullback"]["min_adx_h1"] == 20
    assert after["trend_pullback"]["params"]["min_adx_h1"] == 25
    assert after["backtest"]["better"] is True


def summary(trades: int, expectancy: float | None, drawdown: float) -> RunSummary:
    return RunSummary(trades, 0.5, 100.0, expectancy, 1.3, drawdown)


def test_the_verdict_needs_trades_a_higher_expectancy_and_a_similar_drawdown() -> None:
    current = summary(60, 0.10, 8.0)
    assert compare_runs(current, summary(55, 0.15, 9.0)).better
    assert not compare_runs(current, summary(12, 0.40, 5.0)).better
    assert not compare_runs(current, summary(60, 0.08, 5.0)).better
    assert not compare_runs(current, summary(60, 0.30, 12.0)).better
    assert not compare_runs(current, summary(60, None, 1.0)).better
    assert compare_runs(summary(0, None, 0.0), summary(40, 0.05, 0.5)).better
    lines = compare_runs(current, summary(12, 0.40, 5.0)).lines
    assert "too few to judge" in lines[1] and "luck" in lines[-1]


def test_summary_rows_show_both_runs() -> None:
    rows = summary_rows(summary(60, 0.1, 8.0), summary(55, None, 9.5))
    assert rows[0] == ["Trades", "60", "55"]
    assert rows[3] == ["Expectancy R", "0.100", "n/a"]
    assert rows[5] == ["Max drawdown", "8.00%", "9.50%"]


def test_only_modes_without_real_orders_may_activate() -> None:
    assert activation_block(OperatingMode.PAPER) == ""
    assert activation_block(OperatingMode.ANALYSIS_ONLY) == ""
    assert "Switch to Paper" in activation_block(OperatingMode.SEMI_AUTO)
    assert "Switch to Paper" in activation_block(OperatingMode.AUTO)
