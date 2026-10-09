"""The Dashboard (spec F3 page 1, UI v2 layout) reads snapshots and never changes anything."""

import math

from pytestqt.qtbot import QtBot

from app.analysis.structure import Trend
from app.domain.modes import OperatingMode
from app.domain.signals import SignalState
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.signal_pipeline import SignalsSnapshot
from app.risk.limits import RiskUsage
from app.risk.risk_manager import RiskSnapshot
from app.ui.dashboard_page import (
    DASH_FA,
    DashboardContext,
    DashboardPage,
    direction_words,
    limit_rows,
    money,
    pipeline_steps,
    result_words,
    today_result,
)
from app.ui.v2 import meter_tone, session_bands
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


def test_an_empty_dashboard_is_honest(qtbot: QtBot) -> None:
    page = DashboardPage(None)
    qtbot.addWidget(page)
    assert page.kpis["Balance"].value.text() == "\u2014"
    assert page.kpis["Balance"].sub.text() == "Not known yet"
    assert page.positions.total == 0 and page.positions.empty.isVisibleTo(page)
    assert page.equity_empty.isVisibleTo(page) and not page.equity_chart.isVisibleTo(page)


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
    assert page.kpis["Equity"].value.text() == "10,050.00"
    assert page.kpis["Balance"].sub.text() == "Demo account \u00b7 USD"
    assert page.kpis["Today"].value.text() == "+$125.00"
    assert page.kpis["Today"].value.property("tone") == "profit"
    assert page.kpis["Today"].sub.text() == "5 closed \u00b7 1 open"
    assert page.kpis["Last 30 days"].sub.text() == "5 trades \u00b7 win rate 40%"
    assert page.positions.total == 1 and page.signals.total == 1
    assert page.positions.text(0, 0) == "EURUSD" and page.positions.text(0, 7) == "+$12.50"
    # 75% used: amber, and said in words too (spec F1: never by color only).
    label = page.bar_labels["Daily loss"]
    assert label.accessibleName() == "Daily loss: 1.50% of 2.00% (near the limit)"
    assert page.bar_tags["Daily loss"].text == "near the limit"
    assert page.bars["Daily loss"].share == 0.75
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


def test_persian_dashboard_runs_right_to_left(qtbot: QtBot) -> None:
    page = DashboardPage(None, persian=True)
    qtbot.addWidget(page)
    assert page.kpis["Balance"].caption.text() == DASH_FA["Balance"]
    assert page.signal_filter.itemText(0) == DASH_FA["All signals"]
    page.set_health("Cloud sync", "2 waiting", "warning")
    assert page.health["Cloud sync"].text == "\u06f2 \u062f\u0631 \u0635\u0641"
    assert page.health_tone == "warning"
    page.set_health("Update", "0.32.0 ready", "warning")
    assert page.health["Update"].text.startswith("0.32.0 ")


def test_helpers() -> None:
    assert today_result(sample(), START + 3600 * 10) == (125.0, 5)
    assert limit_rows(RiskSnapshot()) == []
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
