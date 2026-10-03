"""The Analytics page and the trade history (spec C11, F3 pages 4 and 5)."""

import math
from pathlib import Path

from PySide6.QtCore import QDate
from pytestqt.qtbot import QtBot

from app.analytics.trades import TradeRecord
from app.ui.analytics_page import AnalyticsContext, AnalyticsPage, start_balance
from app.ui.trade_history import TradeHistory, TradeHistoryWidget
from tests.unit.analytics_helpers import sample, trade


def trades() -> list[TradeRecord]:
    found = sample()
    found += [trade(10 + i, 20.0 if i % 3 else -10.0, mode="paper") for i in range(25)]
    return found


def page_with(qtbot: QtBot, tmp_path: Path) -> AnalyticsPage:
    context = AnalyticsContext(trades=trades, balance=lambda: 10_000.0, export_dir=tmp_path)
    page = AnalyticsPage(context)
    qtbot.addWidget(page)
    page.date_from.setDate(QDate(2026, 9, 1))
    page.date_to.setDate(QDate(2026, 10, 31))
    page.refresh()
    return page


def test_without_a_database_the_page_says_so(qtbot: QtBot) -> None:
    page = AnalyticsPage(None)
    qtbot.addWidget(page)
    assert "not running" in page.status.text()
    assert not page.export_button.isEnabled()


def test_statistics_breakdowns_and_comparisons(qtbot: QtBot, tmp_path: Path) -> None:
    page = page_with(qtbot, tmp_path)
    assert len(page.shown) == 30
    first = page.summary.item(0, 1)
    assert first is not None and first.text().startswith("30 (")
    page.group_choice.setCurrentText("Mode")
    assert page.groups.rowCount() == 2
    page.compare_choice.setCurrentText("Live vs paper")
    assert page.compare_table.columnCount() == 4  # statistic, live, paper, change
    page.mode.setCurrentText("paper")
    page.refresh()
    assert len(page.shown) == 25
    assert page.behavior.rowCount() >= 1
    assert "Risk of ruin" in page.projection_text.text()


def test_export_writes_csv_files(qtbot: QtBot, tmp_path: Path) -> None:
    page = page_with(qtbot, tmp_path)
    paths = page.export_csv()
    assert len(paths) == 2 and all(path.exists() for path in paths)
    assert paths[0].read_text().count("\n") == 31  # header + 30 trades


def test_the_start_balance_is_today_minus_the_shown_result() -> None:
    assert start_balance(1125.0, sample()) == 1000.0
    assert start_balance(math.nan, sample()) == 0.0


def test_history_filters_and_shows_the_detail(qtbot: QtBot) -> None:
    history = TradeHistory(trades=trades, detail=lambda t: f"detail of {t.id}")
    widget = TradeHistoryWidget(history)
    qtbot.addWidget(widget)
    assert widget.table.rowCount() == 30
    widget.mode.setCurrentText("live")
    assert widget.table.rowCount() == 5
    widget.table.selectRow(0)
    assert widget.detail.toPlainText().startswith("detail of t")
    empty = TradeHistoryWidget(None)
    qtbot.addWidget(empty)
    assert "not running" in empty.count.text()
