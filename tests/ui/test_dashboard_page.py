"""The Dashboard (spec F3 page 1, No Curve v2 layout) reads snapshots and never changes
anything."""

import math
from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast

from pytestqt.qtbot import QtBot

from app.analysis.structure import Trend
from app.domain.modes import OperatingMode
from app.domain.signals import SignalState
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.go_live_desk import APPROVAL, ChecklistItem, GoLiveChecklist
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.models import Quote
from app.risk.limits import RiskUsage
from app.risk.risk_manager import RiskSnapshot
from app.ui.dashboard_page import (
    DASH_FA,
    LIMIT_NAMES,
    DashboardContext,
    DashboardPage,
    checklist_lines,
    direction_words,
    last_price,
    limit_rows,
    money,
    pipeline_steps,
    result_words,
    running_win_rate,
    today_result,
    wilson_range,
)
from app.ui.v2 import Column, grid_widths, meter_tone, session_bands
from tests.unit.analytics_helpers import START, sample
from tests.unit.signal_helpers import make_record


def usage(**changes: object) -> RiskUsage:
    values: dict[str, object] = {
        "capital": 10_000.0,
        "currency": "USD",
        "daily_loss_percent": 1.5,
        "drawdown_percent": 2.0,
        "open_risk_percent": 0.5,
        "open_trades": 1,
        "trades_today": 2,
        "exposure_percent": {},
        "margin_level": math.nan,
        "halted": "",
        "halted_reason": "",
        "day": "2026-10-01",
        "day_start_equity": 10_000.0,
        "day_start_estimated": False,
        "high_water_mark": 10_100.0,
        "balance": 10_000.0,
        "equity": 10_050.0,
    }
    values.update(changes)
    return RiskUsage(**values)  # type: ignore[arg-type]


def checklist(*passed: bool, real: bool = True, block: str = "") -> GoLiveChecklist:
    keys = ["walk_forward", "paper", "slippage", "calibration", "health", "health_now", "risk"]
    pairs = zip(keys, passed, strict=False)
    items = [ChecklistItem(key, key, ok, f"x: {key}") for key, ok in pairs]
    items.append(ChecklistItem(APPROVAL, "Approval", passed[-1] if len(passed) > 7 else False))
    return GoLiveChecklist(tuple(items), real=real, auto_block=block)


def test_an_empty_dashboard_is_honest(qtbot: QtBot) -> None:
    page = DashboardPage(None)
    qtbot.addWidget(page)
    assert page.kpis["Equity"].value.text() == "\u2014"
    assert page.kpis["Equity"].sub.text() == "Not known yet"
    assert page.kpis["Win rate"].caption.text() == "WIN RATE (EST.)"
    assert page.positions.total == 0 and page.positions.empty.isVisibleTo(page)
    assert page.equity_empty.isVisibleTo(page) and not page.equity_chart.isVisibleTo(page)
    assert page.go_live_label.text() == "Go-Live is not known yet."
    assert page.mode_segment.locked, "Auto shows its lock while Go-Live is not known"


def test_kpis_limits_positions_and_signals(qtbot: QtBot) -> None:
    view = PositionView("paper", 7, "EURUSD", "long", 0.1, 1.1, 1.099, 1.102, 12.5, "x", False)
    context = DashboardContext(
        execution=lambda: ExecutionSnapshot(OperatingMode.PAPER, (view,)),
        signals=lambda: SignalsSnapshot(signals=(make_record(),)),
        risk=lambda: RiskSnapshot(usage=usage()),
        trades=sample,
    )
    page = DashboardPage(context)
    qtbot.addWidget(page)
    page.timer.stop()
    page.set_account_kind("Demo account")
    page.refresh(now=START + 6 * 3600)
    equity = page.kpis["Equity"]
    assert equity.caption.text() == "EQUITY \u00b7 PAPER"
    assert equity.value.text() == "10,050.00"
    assert equity.sub.text() == "Demo account \u00b7 Balance 10,000.00 \u00b7 USD"
    assert page.kpis["Today"].value.text() == "+$125.00"
    assert page.kpis["Today"].value.property("tone") == "profit"
    assert page.kpis["Today"].arrow.isVisibleTo(page)
    assert page.kpis["Today"].sub.text() == "5 closed \u00b7 1 open"
    assert page.kpis["Win rate"].value.text() == "40%"
    assert page.kpis["Win rate"].sub.text() == "range 12\u201377% \u00b7 5 trades"
    fall = page.kpis["Max drawdown"]
    assert fall.value.text() == "\u22122.0%" and fall.value.property("tone") == "loss"
    assert fall.sub.text() == "allowed cap 8%"
    assert page.positions.total == 1 and page.signals.total == 1
    assert page.positions.text(0, 0) == "EUR/USD" and page.positions.text(0, 6) == "+$12.50"
    assert page.positions.text(0, 4) == "\u2014", "no live price without the market"
    # 75% used: amber, and said in words too (spec F1: never by color only).
    label = page.bar_labels["Daily loss"]
    assert label.accessibleName() == "Daily loss: 1.50% of 2.00% (near the limit)"
    assert page.bar_tags["Daily loss"].text == "near the limit"
    assert page.bars["Daily loss"].share == 0.75
    assert page.bar_values["Trade size"].text() == "0.5% / 1%"
    assert page.bar_values["Open trades"].text() == "1 / 3"
    assert page.equity_chart.isVisibleTo(page)


def test_halted_trading_shows_the_banner(qtbot: QtBot) -> None:
    halted = RiskSnapshot(usage=usage(halted="daily", halted_reason="daily loss limit"))
    pages: list[str] = []
    page = DashboardPage(DashboardContext(risk=lambda: halted))
    qtbot.addWidget(page)
    page.timer.stop()
    page.go = pages.append
    page.refresh(now=START)
    assert page.halt_banner.isVisibleTo(page)
    page.halt_banner.button.click()
    assert pages == ["risk"]


def test_the_mode_segment_shows_the_mode_and_opens_positions(qtbot: QtBot) -> None:
    snapshot = ExecutionSnapshot(OperatingMode.SEMI_AUTO, ())
    found = checklist(True, True, True, True, True, True, False, block="no approval")
    context = DashboardContext(execution=lambda: snapshot, go_live_checklist=lambda: found)
    page = DashboardPage(context)
    qtbot.addWidget(page)
    page.timer.stop()
    pages: list[str] = []
    page.go = pages.append
    page.refresh(now=START)
    assert page.mode_segment.group.checkedId() == 1 and page.mode_segment.locked
    assert page.kpis["Equity"].caption.text() == "EQUITY \u00b7 SEMI-AUTO"
    page.mode_segment.buttons[0].click()
    assert pages == ["positions"] and page.mode_segment.group.checkedId() == 1
    assert page.go_live_count.text() == "6/8"
    states = [line.state for line in page.go_live_list.lines]
    assert states == ["done"] * 6 + ["open", "locked"]


def test_auto_unlocks_when_the_desk_allows_it(qtbot: QtBot) -> None:
    found = checklist(*([True] * 8), real=False)
    page = DashboardPage(DashboardContext(go_live_checklist=lambda: found))
    qtbot.addWidget(page)
    assert not page.mode_segment.locked
    assert page.go_live_count.text() == "8/8" and not page.go_live_label.isVisibleTo(page)


def test_persian_dashboard_runs_right_to_left(qtbot: QtBot) -> None:
    page = DashboardPage(None, persian=True)
    qtbot.addWidget(page)
    assert page.kpis["Equity"].caption.text() == "EQUITY"  # English in the design too
    assert page.signal_filter.itemText(0) == DASH_FA["All signals"]
    assert page.bar_labels["Trade size"].text() == DASH_FA["Trade size"]
    page.set_health("Cloud sync", "2 waiting", "warning")
    assert page.health["Cloud sync"].text == "2 \u062f\u0631 \u0635\u0641"  # as in the design
    assert page.health_tone == "warning"
    assert page.health_rows["Cloud sync"].led.tone == "warning"
    page.set_health("Update", "0.32.0 ready", "warning")
    assert page.health["Update"].text.startswith("0.32.0 ")
    page.set_health("Update", "Up to date", "profit")
    assert page.health["Update"].text == "LATEST"


def test_helpers() -> None:
    assert today_result(sample(), START + 3600 * 10) == (125.0, 5)
    assert limit_rows(RiskSnapshot()) == []
    rows = limit_rows(RiskSnapshot(usage=usage()))
    assert [row[0] for row in rows] == list(LIMIT_NAMES)
    assert rows[-1][1:] == (0.5, 1.0, "%")
    assert money(-8.2) == "\u2212$8.20" and money(5.0, "CHF") == "+5.00 CHF"
    assert meter_tone(0.5) == "ink" and meter_tone(0.8) == "warning" and meter_tone(1) == "loss"
    assert direction_words(60, Trend.UP) == ("\u2191", "Up", "profit")
    assert direction_words(30, Trend.UP)[1] == "Mildly up"
    assert direction_words(0, Trend.RANGE)[1] == "Flat"
    record = make_record()
    assert pipeline_steps(record) == [1, 1, 1, 1, 0]
    assert result_words(SignalState.FILTERED_OUT) == ("Filtered out", "loss")
    bands = session_bands(START)
    assert [band.name for band in bands] == ["TOKYO", "LONDON", "NEW YORK"]
    low, high = wilson_range(2, 5)
    assert round(low, 3) == 0.118 and round(high, 3) == 0.769
    assert all(math.isnan(v) for v in wilson_range(0, 0))
    rates = running_win_rate(sample())
    assert len(rates) == 5 and all(0 <= rate <= 1 for rate in rates)


def test_the_live_price_and_the_design_grid() -> None:
    buy = PositionView("live", 1, "EURUSD.m", "long", 0.1, 1.1, 1.09, 1.12, 1.0, "x", False)
    sell = PositionView("live", 2, "EURUSD", "short", 0.1, 1.1, 1.11, 1.09, 1.0, "x", False)
    quotes = cast(Mapping[str, Quote], {"EURUSD": SimpleNamespace(bid=1.1012, ask=1.1014)})
    assert last_price(buy, quotes) == 1.1012 and last_price(sell, quotes) == 1.1014
    assert math.isnan(last_price(buy, {}))
    columns = [Column("A", width=96), Column("B", share=1.0), Column("C", share=2.0)]
    assert grid_widths(columns, 436.0, 10) == [106, 113, 216]


def test_checklist_lines_lock_the_approval_until_every_check_passed() -> None:
    open_one = checklist(True, False, True, True, True, True, True)
    assert [state for _, state, _ in checklist_lines(open_one)][-2:] == ["done", "locked"]
    ready = checklist(*([True] * 7))
    assert checklist_lines(ready)[-1][1] == "open"
