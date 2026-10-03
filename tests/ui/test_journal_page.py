"""The Journal page (spec C12): notes and ratings saved, the P/L calendar, reports."""

from datetime import date
from pathlib import Path

from pytestqt.qtbot import QtBot

from app.journal.reports import Period, ReportRepository, build_report
from app.journal.store import JournalRepository
from app.ui.journal_page import JournalContext, JournalPage, month_grid, report_periods
from tests.unit.analytics_helpers import START, sample, trade
from tests.unit.storage_helpers import temporary_store


def test_notes_tags_rating_and_emotion_are_saved(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        journal = JournalRepository(store, lambda: "acc")
        reports = ReportRepository(store, tmp_path)
        made: list[Period] = []

        def make(period: Period) -> object:
            made.append(period)
            report = build_report(period, "acc", sample())
            return reports.save(report)

        trades = [*sample(), trade(9, 30.0, source="manual")]
        page = JournalPage(JournalContext(lambda: trades, journal, reports, make))
        qtbot.addWidget(page)
        assert page.table.rowCount() == 6
        page.table.selectRow(0)  # newest first: the manual trade
        assert page.current is not None and page.current.source == "manual"
        assert page.emotion.isEnabled()
        page.notes.setPlainText("waited for the retest")
        page.tag_edit.setText("A+, patience")
        page.rating.setValue(5)
        page.emotion.setCurrentText("calm")
        assert page.save()
        saved = journal.entry("t9")
        assert saved is not None and saved.tags == ("a+", "patience") and saved.rating == 5
        assert saved.emotion == "calm" and saved.narrative.startswith("Bought")
        page.refresh()
        page.tag_filter.setCurrentText("patience")
        assert page.table.rowCount() == 1
        page.table.selectRow(0)
        assert page.notes.toPlainText() == "waited for the retest"
        page.make_now("daily")
        assert made and page.report_list.count() == 1
        assert page.report_text.toPlainText().startswith("# Day")


def test_the_calendar_shows_each_day(qtbot: QtBot) -> None:
    with temporary_store() as store:
        context = JournalContext(sample, JournalRepository(store), ReportRepository(store))
        page = JournalPage(context)
        qtbot.addWidget(page)
        page.month = date(2026, 10, 1)
        page.show_month()
        assert page.month_label.text() == "October 2026"
        # 1 October 2026 is a Thursday: row 0, column 3
        cell = page.calendar.item(0, 3)
        assert cell is not None and "+125.00" in cell.text()
        assert "+125.00" in page.month_total.text()
        page.move_month(-1)
        assert page.month_label.text() == "September 2026"


def test_calendar_and_report_helpers() -> None:
    assert month_grid(2026, 10)[0] == [0, 0, 0, 1, 2, 3, 4]
    daily, weekly = report_periods(START + 2 * 86_400 + 3600, 0.0)  # Saturday 3 October
    assert daily.first == date(2026, 10, 2) and weekly.first == date(2026, 9, 21)
