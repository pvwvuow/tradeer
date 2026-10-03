"""The Journal page (spec C12, F3 page 6): every closed trade with its story, your notes,
tags, a 1 to 5 rating and (manual trades) an emotion; the P/L calendar; and the daily and
weekly reports, which you can also make now."""

from __future__ import annotations

import calendar
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.analytics.charts import pl_calendar
from app.analytics.trades import TradeRecord
from app.journal.narrative import narrative
from app.journal.reports import Period, ReportRepository, day_period, week_period
from app.journal.store import EMOTIONS, JournalEntry, JournalError, JournalRepository, parse_tags
from app.ui.pages import PAGE_MARGIN, styled_label
from app.ui.tables import fill_table, heat, make_table, number, signed

ALL_TAGS = "All tags"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
COLUMNS = ("Closed (UTC)", "Symbol", "Side", "Net", "R", "Strategy", "Rating", "Tags")


@dataclass
class JournalContext:
    trades: Callable[[], Sequence[TradeRecord]]
    journal: JournalRepository
    reports: ReportRepository
    make_report: Callable[[Period], object] | None = None
    offset: Callable[[], float] = lambda: 0.0  # broker UTC offset in seconds


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M")


def month_grid(year: int, month: int) -> list[list[int]]:
    """Weeks of day numbers (0 = outside the month), Monday first."""
    return calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)


def report_periods(now: float, offset: float) -> tuple[Period, Period]:
    """Yesterday (broker day) and last week, for the "make now" buttons."""
    today = datetime.fromtimestamp(now + offset, UTC).date()
    yesterday = today - timedelta(days=1)
    monday = today - timedelta(days=today.weekday() + 7)
    return day_period(yesterday, offset), week_period(monday, offset)


class JournalPage(QWidget):
    def __init__(self, context: JournalContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_journal")
        self.context = context
        self.trades: list[TradeRecord] = []
        self.shown: list[TradeRecord] = []
        self.tags: dict[str, tuple[str, ...]] = {}
        self.current: TradeRecord | None = None
        self.reports: list[dict[str, object]] = []
        today = datetime.now(UTC).date()
        self.month = date(today.year, today.month, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Journal", "title"))
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("JournalStatus")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("JournalTabs")
        self.tabs.addTab(self._build_trades(), "Trades")
        self.tabs.addTab(self._build_calendar(), "P/L calendar")
        self.tabs.addTab(self._build_reports(), "Reports")
        layout.addWidget(self.tabs, 1)
        if context is None:
            self.status.setText("The journal needs the local database, which is not running.")
            buttons = (self.save_button, self.refresh_button, self.daily_button, self.weekly_button)
            for widget in buttons:
                widget.setEnabled(False)
        else:
            self.daily_button.setEnabled(context.make_report is not None)
            self.weekly_button.setEnabled(context.make_report is not None)
            self.refresh()

    # Layout ------------------------------------------------------------------------------
    def _build_trades(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.tag_filter = QComboBox()
        self.tag_filter.addItem(ALL_TAGS)
        self.tag_filter.currentTextChanged.connect(lambda _: self.apply())
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.tag_filter)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        splitter = QSplitter()
        self.table = make_table(COLUMNS, select=True)
        self.table.setObjectName("JournalTrades")
        self.table.itemSelectionChanged.connect(self.show_selected)
        splitter.addWidget(self.table)
        editor = QWidget()
        form = QVBoxLayout(editor)
        self.story = QPlainTextEdit()
        self.story.setReadOnly(True)
        self.story.setObjectName("JournalStory")
        self.story.setPlaceholderText("Select a trade.")
        self.notes = QPlainTextEdit()
        self.notes.setObjectName("JournalNotes")
        self.notes.setPlaceholderText("Your notes: what you saw, what you would do again.")
        self.tag_edit = QLineEdit()
        self.tag_edit.setObjectName("JournalTags")
        self.tag_edit.setPlaceholderText("Tags, separated by commas (for example: a+ setup, news)")
        self.rating = QSpinBox()
        self.rating.setRange(0, 5)
        self.rating.setSpecialValueText("no rating")
        self.rating.setSuffix(" / 5")
        self.emotion = QComboBox()
        self.emotion.addItems([e or "no emotion" for e in EMOTIONS])
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "accent")
        self.save_button.clicked.connect(self.save)
        self.save_button.setEnabled(False)
        details = QHBoxLayout()
        details.addWidget(styled_label("Rating", "muted"))
        details.addWidget(self.rating)
        details.addWidget(styled_label("Emotion", "muted"))
        details.addWidget(self.emotion)
        details.addStretch(1)
        details.addWidget(self.save_button)
        form.addWidget(self.story, 2)
        form.addWidget(self.notes, 1)
        form.addWidget(self.tag_edit)
        form.addLayout(details)
        splitter.addWidget(editor)
        layout.addWidget(splitter, 1)
        return box

    def _build_calendar(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.previous_button = QPushButton("\u2190")
        self.previous_button.clicked.connect(lambda: self.move_month(-1))
        self.next_button = QPushButton("\u2192")
        self.next_button.clicked.connect(lambda: self.move_month(1))
        self.month_label = styled_label("", "heading")
        self.month_label.setObjectName("JournalMonth")
        row.addWidget(self.previous_button)
        row.addWidget(self.month_label)
        row.addWidget(self.next_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.calendar = make_table(WEEKDAYS)
        self.calendar.setObjectName("JournalCalendar")
        layout.addWidget(self.calendar, 1)
        self.month_total = styled_label("", "muted")
        self.month_total.setObjectName("JournalMonthTotal")
        layout.addWidget(self.month_total)
        return box

    def _build_reports(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.daily_button = QPushButton("Make yesterday's report")
        self.daily_button.clicked.connect(lambda: self.make_now("daily"))
        self.weekly_button = QPushButton("Make last week's report")
        self.weekly_button.clicked.connect(lambda: self.make_now("weekly"))
        row.addWidget(self.daily_button)
        row.addWidget(self.weekly_button)
        row.addStretch(1)
        layout.addLayout(row)
        splitter = QSplitter()
        self.report_list = QListWidget()
        self.report_list.setObjectName("JournalReports")
        self.report_list.currentRowChanged.connect(self.show_report)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setObjectName("JournalReportText")
        self.report_text.setPlaceholderText("Reports are made after each trading day and week.")
        splitter.addWidget(self.report_list)
        splitter.addWidget(self.report_text)
        layout.addWidget(splitter, 1)
        return box

    # Trades ------------------------------------------------------------------------------
    def refresh(self) -> None:
        context = self.context
        if context is None:
            return
        try:
            self.trades = sorted(context.trades(), key=lambda t: t.close_time, reverse=True)
            self.tags = context.journal.tagged()
        except Exception as error:
            self.status.setText(f"The journal could not be read: {type(error).__name__}: {error}")
            return
        names = sorted({tag for tags in self.tags.values() for tag in tags})
        current = self.tag_filter.currentText()
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItems([ALL_TAGS, *names])
        self.tag_filter.setCurrentText(current if current in names else ALL_TAGS)
        self.tag_filter.blockSignals(False)
        self.apply()
        self.show_month()
        self.refresh_reports()

    def apply(self) -> None:
        wanted = self.tag_filter.currentText()
        self.shown = [
            t for t in self.trades if wanted in ("", ALL_TAGS) or wanted in self.tags.get(t.id, ())
        ]
        rows: list[list[str]] = []
        for trade in self.shown:
            entry_tags = self.tags.get(trade.id, ())
            rows.append(
                [
                    _utc(trade.close_time),
                    trade.symbol,
                    "Buy" if trade.direction == "buy" else "Sell",
                    signed(trade.net_profit),
                    number(trade.r_multiple),
                    trade.strategy,
                    "",
                    ", ".join(entry_tags),
                ],
            )
        fill_table(self.table, rows)
        self.status.setText(f"{len(self.shown)} closed trade(s) in the journal.")
        self.current = None
        self.save_button.setEnabled(False)

    def show_selected(self) -> None:
        context = self.context
        rows = self.table.selectionModel().selectedRows()
        if context is None or not rows:
            return
        index = rows[0].row()
        if not 0 <= index < len(self.shown):
            return
        trade = self.shown[index]
        self.current = trade
        entry = context.journal.entry(trade.id) or JournalEntry(trade.id)
        events = context.journal.events(trade.id)
        self.story.setPlainText(narrative(trade, events))
        self.notes.setPlainText(entry.notes)
        self.tag_edit.setText(", ".join(entry.tags))
        self.rating.setValue(entry.rating or 0)
        manual = trade.source != "bot"
        self.emotion.setEnabled(manual)
        known = entry.emotion in EMOTIONS
        self.emotion.setCurrentIndex(EMOTIONS.index(entry.emotion) if known else 0)
        self.emotion.setToolTip("" if manual else "Emotions are for your own (manual) trades.")
        self.save_button.setEnabled(True)
        rating_item = QTableWidgetItem(f"{entry.rating}/5" if entry.rating else "")
        self.table.setItem(index, 6, rating_item)

    def save(self) -> bool:
        context = self.context
        trade = self.current
        if context is None or trade is None:
            return False
        old = context.journal.entry(trade.id) or JournalEntry(trade.id)
        emotion = EMOTIONS[self.emotion.currentIndex()] if trade.source != "bot" else ""
        entry = JournalEntry(
            trade_id=trade.id,
            narrative=self.story.toPlainText(),
            notes=self.notes.toPlainText().strip(),
            tags=parse_tags(self.tag_edit.text()),
            rating=self.rating.value() or None,
            emotion=emotion,
            snapshots=old.snapshots,
        )
        try:
            changed = context.journal.save(entry)
        except JournalError as error:
            self.status.setText(f"Not saved: {error}")
            return False
        self.tags[trade.id] = entry.tags
        self.status.setText("Saved." if changed else "Nothing changed.")
        return changed

    # Calendar ----------------------------------------------------------------------------
    def move_month(self, step: int) -> None:
        month = self.month.month - 1 + step
        self.month = date(self.month.year + month // 12, month % 12 + 1, 1)
        self.show_month()

    def show_month(self) -> None:
        days = pl_calendar(self.trades)
        year, month = self.month.year, self.month.month
        self.month_label.setText(self.month.strftime("%B %Y"))
        weeks = month_grid(year, month)
        prefix = f"{year}-{month:02d}-"
        sizes = [abs(d.net_profit) for k, d in days.items() if k.startswith(prefix)]
        biggest = max(sizes, default=1.0)
        self.calendar.setRowCount(len(weeks))
        total = 0.0
        count = 0
        for row, week in enumerate(weeks):
            for column, day in enumerate(week):
                if day == 0:
                    self.calendar.setItem(row, column, QTableWidgetItem(""))
                    continue
                found = days.get(f"{year}-{month:02d}-{day:02d}")
                text = str(day)
                value = None
                if found is not None:
                    value = found.net_profit
                    total += found.net_profit
                    count += found.trades
                    text = f"{day}\n{found.net_profit:+,.2f}\n{found.trades} trade(s)"
                item = QTableWidgetItem(text)
                item.setBackground(heat(value, scale=max(biggest, 0.01)))
                self.calendar.setItem(row, column, item)
        self.calendar.resizeRowsToContents()
        self.month_total.setText(f"Month: {total:+,.2f} from {count} closed trade(s) (UTC days)")

    # Reports -----------------------------------------------------------------------------
    def refresh_reports(self) -> None:
        context = self.context
        if context is None:
            return
        self.reports = list(context.reports.recent())
        self.report_list.blockSignals(True)
        self.report_list.clear()
        for report in self.reports:
            net = report.get("net_profit")
            result = f" {float(net):+,.2f}" if isinstance(net, int | float) else ""
            self.report_list.addItem(f"{report.get('label', report.get('report_date'))}{result}")
        self.report_list.blockSignals(False)
        if self.reports:
            self.report_list.setCurrentRow(0)
            self.show_report(0)

    def show_report(self, row: int) -> None:
        if 0 <= row < len(self.reports):
            self.report_text.setPlainText(str(self.reports[row].get("text", "")))

    def make_now(self, kind: str) -> None:
        context = self.context
        if context is None or context.make_report is None:
            return
        daily, weekly = report_periods(datetime.now(UTC).timestamp(), context.offset())
        period = daily if kind == "daily" else weekly
        try:
            context.make_report(period)
        except Exception as error:
            self.status.setText(f"The report could not be made: {type(error).__name__}: {error}")
            return
        self.status.setText(f"{period.label} report saved.")
        self.refresh_reports()
