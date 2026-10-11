"""The money given to the Telegram channels on the Dashboard and the Risk page (phase 22g)."""

from PySide6.QtWidgets import QLabel, QWidget
from pytestqt.qtbot import QtBot

from app.channels.overview import ChannelLine, Overview
from app.ui.channel_budgets import (
    BUDGETS_FA,
    DashboardBudgets,
    RiskBudgets,
    budget_rows,
    insert_after,
    risk_rows,
    signed,
)
from app.ui.dashboard_page import DashboardContext, DashboardPage
from app.ui.risk_page import RiskPage

GOLD = ChannelLine("Gold Room", True, 100.0, -12.0, -6.0, 88.0, 1.76, 2.0, "daily stop")
PAPER = ChannelLine("FX Room", False, 50.0, 0.0, 0.0, 50.0, 1.0, 2.0)


def overview() -> Overview:
    return Overview((GOLD, PAPER))


def broken() -> Overview:
    raise RuntimeError("boom")


def row_texts(block: QWidget) -> list[str]:
    labels = block.findChildren(QLabel)
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


def test_the_dashboard_block_lists_the_channels(qtbot: QtBot) -> None:
    block = DashboardBudgets(overview, lambda: "USD")
    qtbot.addWidget(block)
    assert not block.isHidden()
    texts = row_texts(block)
    assert "All channels" in texts and "Gold Room" in texts and "FX Room" in texts
    assert "138.00 / 150.00 \u00b7 \u22126.00 USD" in texts
    assert "88.00 / 100.00 \u00b7 \u22126.00 USD \u00b7 daily stop" in texts
    assert "50.00 / 50.00 \u00b7 0.00 USD" in texts
    assert block.section.title.text() == "Telegram channels"


def test_the_dashboard_block_hides_without_a_budget(qtbot: QtBot) -> None:
    none = DashboardBudgets(lambda: Overview())
    qtbot.addWidget(none)
    assert none.isHidden()
    failing = DashboardBudgets(broken)
    qtbot.addWidget(failing)
    assert failing.isHidden() and failing.refresh() == 0


def test_the_dashboard_block_in_persian(qtbot: QtBot) -> None:
    block = DashboardBudgets(overview, lambda: "USD", persian=True)
    qtbot.addWidget(block)
    assert block.section.title.text() == BUDGETS_FA["Telegram channels"]
    texts = row_texts(block)
    assert f"88.00 / 100.00 \u00b7 \u22126.00 USD \u00b7 {BUDGETS_FA['daily stop']}" in texts
    assert BUDGETS_FA["All channels"] in texts and "Gold Room" in texts


def test_the_block_sits_between_the_limits_and_the_health(qtbot: QtBot) -> None:
    page = DashboardPage(DashboardContext())
    qtbot.addWidget(page)
    block = DashboardBudgets(overview)
    qtbot.addWidget(block)
    assert not insert_after(page, "NoSuchWidget", block)
    assert insert_after(page, "LimitTradesize", block)
    meter = page.findChild(QWidget, "LimitTradesize")
    health = page.findChild(QWidget, "HealthMT5connection")
    assert meter is not None and health is not None
    holder = meter.parentWidget()
    layout = holder.layout() if holder is not None else None
    assert layout is not None
    here = layout.indexOf(block)
    assert here > 0
    before = layout.itemAt(here - 1).layout()
    after = layout.itemAt(here + 1).layout()
    assert before is not None and before.indexOf(meter) >= 0
    assert after is not None and after.indexOf(health) >= 0


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
    failing = RiskBudgets(broken)
    qtbot.addWidget(failing)
    assert failing.isHidden()


def test_the_risk_page_gets_the_table_under_the_limit_usage(qtbot: QtBot) -> None:
    page = RiskPage(None)
    qtbot.addWidget(page)
    block = RiskBudgets(overview)
    qtbot.addWidget(block)
    assert insert_after(page, "RiskUsage", block)
    usage = page.findChild(QWidget, "RiskUsage")
    assert usage is not None
    holder = usage.parentWidget()
    layout = holder.layout() if holder is not None else None
    assert layout is not None
    assert layout.indexOf(block) == layout.indexOf(usage) + 1
