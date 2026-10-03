"""The Risk page with a real RiskManager on a FakeMT5 (spec F3 page 11)."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.risk.risk_manager import RiskSnapshot
from app.risk.settings import RiskSettingsSource
from app.ui.risk_page import RiskContext, RiskPage
from tests.unit.risk_helpers import connected, risk_manager
from tests.unit.signal_helpers import MORNING


def test_without_a_manager_the_page_is_an_honest_shell(qtbot: QtBot) -> None:
    page = RiskPage(None)
    qtbot.addWidget(page)
    assert "Not connected" in page.state_label.text()
    assert not page.save_button.isEnabled() and not page.stop_button.isEnabled()
    assert page.usage.rowCount() == 0


def test_usage_profiles_save_and_typed_re_enable(qtbot: QtBot, tmp_path: Path) -> None:
    fake = connected()
    source = RiskSettingsSource(tmp_path)
    with risk_manager(fake) as manager:
        manager.refresh(float(MORNING), force=True)
        page = RiskPage(RiskContext(manager, source))
        qtbot.addWidget(page)
        assert page.usage.rowCount() == 6
        assert page.state_label.text().startswith("Trading allowed")
        assert not page.enable_button.isEnabled()
        page.profile.setCurrentIndex(page.profile.findData("conservative"))
        page.apply_profile()
        assert page.save()
        assert source.config.profile == "conservative"
        assert source.settings().risk_per_trade_percent == 0.25
        page.stop_entries()
        manager.refresh(float(MORNING) + 1)
        snapshot = manager.snapshot
        assert isinstance(snapshot, RiskSnapshot) and snapshot.halted == "manual"
        page.show_snapshot(snapshot)
        assert page.enable_button.isEnabled()
        assert not page.enable("enable please")
        assert page.enable("ENABLE")
        manager.refresh(float(MORNING) + 2)
        assert manager.snapshot.halted == ""
