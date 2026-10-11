"""The money given to the Telegram channels on the Dashboard and the Risk page (phase 22g)."""

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from app.channels.overview import ChannelLine, Overview
from app.ui.channel_budgets import RiskBudgets, budget_rows, risk_rows, signed
from app.ui.dashboard_page import DASH_FA, DashboardContext, DashboardPage

GOLD = ChannelLine("Gold Room", True, 100.0, -12.0, -6.0, 88.0, 1.76, 2.0, "daily stop")
PAPER = ChannelLine("FX Room", False, 50.0, 0.0, 0.0, 50.0, 1.0, 2.0)


def overview() -> Overview:
    return Overview((GOLD, PAPER))


def row_texts(page: DashboardPage) -> list[str]:
    labels = page.channel_budgets.findChildren(QLabel)
    return [label.text() for label in labels if label.property("v2") == "row"]


def test_the_rows_and_the_signs() -> None:
    assert signed(4.2, "USD") == "+4.20 USD" and signed(-6.0, "USD") == "\u22126.00 USD"
    assert signed(0.0) == "0.00"
    rows = budget_rows(overview())
    assert [row.name for row in rows] == ["All channels", "Gold Room", "FX Room"]
    assert (rows[0].equity, rows[0].budget, rows[0].today) == (138.0, 150.0, -6.0)
    assert "Live: budget 100.00, closed -12.00" in rows[1].tip
    assert [row.name for row in budget_rows(Overview((GOLD,)))] == ["Gold Room"]
    table = risk_rows(overview(), "USD")
    assert table[0] == (
        "Gold Room",
        "Live",
        "100.00",
        "88.00",
        "\u22126.00 USD",
        "1.76 (2%)",
        "daily stop",
    )
    assert table[1][1] == "Paper trial" and table[1][6] == "-"


def test_the_dashboard_shows_the_channels_under_the_limits(qtbot: QtBot) -> None:
    page = DashboardPage(DashboardContext(channels=overview))
    qtbot.addWidget(page)
    assert not page.channel_budgets.isHidden()
    texts = row_texts(page)
    assert "All channels" in texts and "Gold Room" in texts and "FX Room" in texts
    assert "138.00 / 150.00 \u00b7 \u22126.00 USD" in texts
    assert "88.00 / 100.00 \u00b7 \u22126.00 USD \u00b7 daily stop" in texts
    assert "50.00 / 50.00 \u00b7 0.00 USD" in texts


def test_the_dashboard_hides_the_block_without_a_budget(qtbot: QtBot) -> None:
    none = DashboardPage(DashboardContext(channels=lambda: Overview()))
    qtbot.addWidget(none)
    assert none.channel_budgets.isHidden()
    unset = DashboardPage(DashboardContext())
    qtbot.addWidget(unset)
    assert unset.channel_budgets.isHidden()

    def broken() -> Overview:
        raise RuntimeError("boom")

    failing = DashboardPage(DashboardContext(channels=broken))
    qtbot.addWidget(failing)
    assert failing.channel_budgets.isHidden()


def test_the_dashboard_block_in_persian(qtbot: QtBot) -> None:
    page = DashboardPage(DashboardContext(channels=overview), persian=True)
    qtbot.addWidget(page)
    assert page.channel_budgets.section.title.text() == DASH_FA["Telegram channels"]
    assert f"88.00 / 100.00 \u00b7 \u22126.00 USD \u00b7 {DASH_FA['daily stop']}" in row_texts(page)


def test_the_risk_page_table(qtbot: QtBot) -> None:
    block = RiskBudgets(overview, lambda: "USD")
    qtbot.addWidget(block)
    assert not block.isHidden() and block.table.rowCount() == 2
    first = block.table.item(0, 0)
    assert first is not None and first.text() == "Gold Room"
    today = block.table.item(0, 4)
    assert today is not None and today.text() == "\u22126.00 USD"
    stop = block.table.item(0, 6)
    assert stop is not None and stop.text() == "daily stop"
    mode = block.table.item(1, 1)
    assert mode is not None and mode.text() == "Paper trial"
    empty = RiskBudgets(lambda: Overview())
    qtbot.addWidget(empty)
    assert empty.isHidden() and empty.table.rowCount() == 0

    def broken() -> Overview:
        raise RuntimeError("boom")

    failing = RiskBudgets(broken)
    qtbot.addWidget(failing)
    assert failing.isHidden()
