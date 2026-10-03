"""The Dashboard (spec F3 page 1) reads snapshots and never changes anything."""

import math

from pytestqt.qtbot import QtBot

from app.domain.modes import OperatingMode
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.signal_pipeline import SignalsSnapshot
from app.risk.limits import RiskUsage
from app.risk.risk_manager import RiskSnapshot
from app.ui.dashboard_page import DashboardContext, DashboardPage, limit_rows, today_result
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
    assert page.kpis["Balance"].value.text() == "n/a"
    assert page.positions.rowCount() == 0


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
    page.refresh(now=START + 6 * 3600)
    assert page.kpis["Equity"].value.text() == "10,050.00 USD"
    assert page.kpis["Today"].value.text() == "+125.00 (5 closed)"
    assert page.kpis["Last 30 days"].value.text().startswith("+125.00, win rate 40%")
    assert page.positions.rowCount() == 1 and page.signals.rowCount() == 1
    assert page.bar_labels["Daily loss"].text() == "Daily loss: 1.50% of 2.00%"
    assert page.bars["Daily loss"].value() == 75


def test_helpers() -> None:
    assert today_result(sample(), START + 3600 * 10) == (125.0, 5)
    assert limit_rows(RiskSnapshot()) == []
