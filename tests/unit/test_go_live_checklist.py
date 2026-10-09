"""The Dashboard's Go-Live checklist (No Curve v2, 20c): the seven gate checks over every
strategy that is on, then the approval."""

from pathlib import Path

from app.core.strategy_settings import StrategyEntry
from app.engine.go_live_desk import APPROVAL
from tests.unit.test_go_live_desk import OTHER, make_desk
from tests.unit.test_go_live_gate import STRATEGY

KEYS = ["walk_forward", "paper", "slippage", "calibration", "health", "health_now", "risk"]


def test_the_checklist_has_the_seven_checks_and_the_approval(tmp_path: Path) -> None:
    desk, _ = make_desk(tmp_path)
    found = desk.checklist()
    assert [item.key for item in found.items] == [*KEYS, APPROVAL]
    assert found.total == 8 and found.done == 6 and not found.passed
    risk = found.items[KEYS.index("risk")]
    assert not risk.passed and risk.detail.startswith(f"{STRATEGY}: ")
    assert not found.checks_passed and "no Go-Live approval" in found.auto_block
    desk.review_risk()
    assert desk.checklist().done == 7 and desk.checklist().checks_passed
    desk.approve(STRATEGY)
    ready = desk.checklist()
    assert ready.passed and ready.done == 8 and ready.auto_block == ""


def test_a_check_is_done_only_when_every_strategy_passed_it(tmp_path: Path) -> None:
    desk, _ = make_desk(tmp_path)
    desk.review_risk()
    both = desk.strategies.settings.with_entry(OTHER, StrategyEntry(enabled=True, params={}))
    desk.strategies.save(both)
    found = desk.checklist()
    walk = found.items[0]
    assert not walk.passed and STRATEGY in walk.detail and OTHER in walk.detail
    assert found.items[KEYS.index("risk")].passed


def test_no_strategy_on_gives_a_note_and_no_list(tmp_path: Path) -> None:
    desk, _ = make_desk(tmp_path, real=False)
    off = desk.strategies.settings.with_entry(STRATEGY, StrategyEntry(enabled=False, params={}))
    desk.strategies.save(off)
    found = desk.checklist()
    assert found.items == () and found.note == "No strategy is on." and not found.real
    assert found.total == 0 and not found.passed
