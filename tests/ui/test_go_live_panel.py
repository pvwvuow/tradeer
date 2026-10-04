"""The Go-Live checklist on the Strategies page, Auto in the Positions page mode switch and
the readiness line on the Dashboard (spec C9, F3)."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.execution_settings import ExecutionSettingsSource
from app.domain.modes import OperatingMode
from app.engine.go_live_gate import OVERRIDE_PHRASE
from app.ui.dashboard_page import DashboardContext, DashboardPage
from app.ui.positions_page import PositionsPage, TradingContext, mode_change_word
from app.ui.strategies_page import StrategiesPage
from tests.unit.execution_helpers import rig
from tests.unit.risk_helpers import connected
from tests.unit.storage_helpers import temporary_store
from tests.unit.test_go_live_desk import make_desk


def test_the_checklist_approves_only_with_the_override_and_can_be_revoked(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    desk, audits = make_desk(tmp_path, ready=False)
    page = StrategiesPage(desk.strategies)
    qtbot.addWidget(page)
    panel = page.attach_go_live(desk)
    assert page.go_live_panel is panel
    assert "Walk-forward backtest" in panel.checks.text()
    assert panel.approval.text() == "Not approved on this account."
    panel.typed = lambda title, text: "yes"
    assert not panel.ask_approve() and OVERRIDE_PHRASE in panel.status.text()
    panel.typed = lambda title, text: OVERRIDE_PHRASE
    assert panel.ask_approve() and "(override)" in panel.approval.text()
    panel.confirm = lambda title, text: True
    assert panel.revoke() and panel.approval.text() == "Not approved on this account."
    assert panel.review_risk()
    actions = [item[0] for item in audits]
    assert actions == [
        "go-live override",
        "go-live approval removed",
        "go-live risk settings reviewed",
    ]


def test_a_ready_strategy_is_approved_with_a_plain_confirmation(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    desk, _ = make_desk(tmp_path)
    desk.review_risk()
    page = StrategiesPage(desk.strategies)
    qtbot.addWidget(page)
    panel = page.attach_go_live(desk)
    panel.confirm = lambda title, text: False
    assert not panel.ask_approve()
    panel.confirm = lambda title, text: True
    assert panel.ask_approve() and "all checks passed" in panel.approval.text()


def test_auto_on_the_positions_page_needs_the_gate_and_the_typed_word(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    assert mode_change_word(OperatingMode.AUTO, False) == "AUTO"
    fake = connected()
    with temporary_store() as store, rig(fake, store) as r:
        source = ExecutionSettingsSource(tmp_path)
        block = ["trend_pullback has no Go-Live approval on this REAL account"]
        context = TradingContext(r.engine, source, lambda: True, auto_check=lambda: block[0])
        page = PositionsPage(context)
        qtbot.addWidget(page)
        auto = page.mode.findData(OperatingMode.AUTO.value)
        assert auto >= 0
        page.mode.setCurrentIndex(auto)
        page.typed = lambda title, text: "AUTO"
        assert not page.ask_mode_change() and source.mode is OperatingMode.PAPER
        assert "no Go-Live approval" in page.status.text()
        block[0] = ""
        page.mode.setCurrentIndex(auto)
        page.typed = lambda title, text: "auto"
        assert not page.ask_mode_change() and source.mode is OperatingMode.PAPER
        page.mode.setCurrentIndex(auto)
        page.typed = lambda title, text: "AUTO"
        assert page.ask_mode_change() and source.mode is OperatingMode.AUTO


def test_the_dashboard_shows_the_go_live_readiness(qtbot: QtBot) -> None:
    page = DashboardPage(DashboardContext(go_live=lambda: "Go-Live (demo account). ready"))
    qtbot.addWidget(page)
    page.timer.stop()
    assert page.go_live_label.text() == "Go-Live (demo account). ready"
