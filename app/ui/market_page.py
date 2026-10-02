"""The Market page (spec F3): watchlist, analysis cards, MTF matrix, chart, correlation,
currency strength and the economic calendar.

The analysis runs in its own thread (`MarketWatch`); snapshots arrive here through a queued
Qt signal, so this page never waits for MT5. Cards change only when a bar has closed.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.analysis.bars import ANALYSIS_TIMEFRAMES
from app.analysis.card import NOT_A_SIGNAL
from app.analysis.structure import Trend
from app.analysis.symbol import SymbolAnalysis
from app.calendar.csv_import import CsvResult, decode, parse_calendar_csv
from app.calendar.exporter import CalendarFileWatcher, install_exporter
from app.calendar.models import CalendarEvent, Impact, upcoming
from app.calendar.store import CalendarStore, import_exporter_file
from app.core.watchlist import MAX_SYMBOLS, Watchlist, clean_symbols, load_watchlist, save_watchlist
from app.engine.market_watch import MarketSnapshot, MarketWatch
from app.observability.logger import audit
from app.ui.chart import CandleChart
from app.ui.pages import PAGE_MARGIN, styled_label
from app.ui.theme import ThemeTokens

CARD_COLUMNS = 3
CALENDAR_COLUMNS = [
    "When (local)",
    "Countdown",
    "Currency",
    "Impact",
    "Event",
    "Actual",
    "Forecast",
    "Previous",
]
ARROWS = {Trend.UP: "\u25b2", Trend.DOWN: "\u25bc", Trend.RANGE: "\u25c6"}
EXPORTER_HELP = (
    "MT5 calendar: press 'Install MT5 exporter', then in MT5 open MetaEditor (F4), open "
    "Services > CalendarExporter.mq5 and press Compile (F7). In MT5 Navigator > Services, "
    "right-click CalendarExporter > Add service and start it. The app reads its file every "
    "3 minutes. You can also add events by hand or import a CSV file."
)


@dataclass
class MarketContext:
    watch: MarketWatch
    profile_dir: Path
    calendar: CalendarStore | None
    calendar_file: CalendarFileWatcher
    data_path: Callable[[], str]


class _Bridge(QObject):
    snapshot = Signal(object)
    calendar = Signal(object)


def _item(text: str, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class SymbolCard(QFrame):
    def __init__(self, symbol: str) -> None:
        super().__init__()
        self.symbol = symbol
        self.setObjectName(f"card_{symbol}")
        self.setProperty("role", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.title = styled_label(symbol, "brand")
        self.verdict = styled_label("waiting", "badge")
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.verdict)
        layout.addLayout(top)
        self.headline = styled_label("Waiting for the first closed bar.", "muted", wrap=True)
        self.details = styled_label("", "muted", wrap=True)
        self.updated = styled_label("", "status")
        layout.addWidget(self.headline)
        layout.addWidget(self.details)
        layout.addWidget(self.updated)
        self.bar_time = 0

    def show_analysis(self, analysis: SymbolAnalysis) -> None:
        card = analysis.card
        self.bar_time = analysis.bar_time
        arrow = ARROWS[analysis.trend.direction]
        self.title.setText(f"{analysis.symbol}  {arrow} {card.bias:+.0f}")
        self.verdict.setText(card.verdict.value.upper())
        self.headline.setText(card.headline)
        self.details.setText("\n".join([*card.lines, NOT_A_SIGNAL]))
        closed = datetime.fromtimestamp(analysis.bar_time + 300).strftime("%H:%M")
        self.updated.setText(f"Updated at the {closed} M5 close \u00b7 {analysis.broker_symbol}")


class MarketPage(QWidget):
    def __init__(self, context: MarketContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_market")
        self.context = context
        self.bridge = _Bridge()
        self.bridge.snapshot.connect(self.show_snapshot, Qt.ConnectionType.QueuedConnection)
        self.bridge.calendar.connect(self._calendar_done, Qt.ConnectionType.QueuedConnection)
        self.cards: dict[str, SymbolCard] = {}
        self.last_snapshot: MarketSnapshot | None = None
        self._calendar_events: list[CalendarEvent] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(16)
        layout.addWidget(styled_label("Market", "title"))
        self.status = styled_label("Waiting for the MT5 connection.", "muted", wrap=True)
        layout.addWidget(self.status)
        layout.addLayout(self._build_watchlist())
        self.cards_grid = QGridLayout()
        self.cards_grid.setSpacing(12)
        layout.addLayout(self.cards_grid)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("MarketTabs")
        self.chart = CandleChart()
        chart_tab = QWidget()
        chart_layout = QVBoxLayout(chart_tab)
        self.chart_symbol = QComboBox()
        self.chart_symbol.setObjectName("ChartSymbol")
        self.chart_symbol.currentTextChanged.connect(self._chart_symbol_changed)
        chart_layout.addWidget(self.chart_symbol)
        chart_layout.addWidget(self.chart, 1)
        self.tabs.addTab(chart_tab, "Chart")
        self.matrix = _table(["Symbol", *ANALYSIS_TIMEFRAMES, "Bias"])
        self.tabs.addTab(self.matrix, "Trend matrix")
        self.correlation = _table(["Symbol"])
        self.tabs.addTab(self.correlation, "Correlation")
        self.strength = _table(["Rank", "Currency", "Strength (ATR)", "Pairs"])
        self.tabs.addTab(self.strength, "Currency strength")
        self.tabs.addTab(self._build_calendar(), "Calendar")
        layout.addWidget(self.tabs, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_countdowns)
        self._timer.start(30_000)
        watchlist = load_watchlist(context.profile_dir) if context is not None else Watchlist()
        self.watchlist_edit.setText(", ".join(watchlist.symbols))
        self._make_cards(watchlist.symbols)
        if context is not None:
            context.watch.add_listener(self.bridge.snapshot.emit)
            self.show_snapshot(context.watch.snapshot)
            self.reload_calendar()

    # Watchlist ---------------------------------------------------------------------------
    def _build_watchlist(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.watchlist_edit = QLineEdit()
        self.watchlist_edit.setObjectName("WatchlistEdit")
        self.watchlist_edit.setPlaceholderText(
            f"Up to {MAX_SYMBOLS} symbols, for example EURUSD, XAUUSD",
        )
        self.watchlist_button = QPushButton("Save watchlist")
        self.watchlist_button.clicked.connect(self.save_watchlist)
        row.addWidget(styled_label("Watchlist", "section"))
        row.addWidget(self.watchlist_edit, 1)
        row.addWidget(self.watchlist_button)
        return row

    def watchlist_symbols(self) -> list[str]:
        return clean_symbols(self.watchlist_edit.text().replace(";", ",").split(","))

    def save_watchlist(self) -> None:
        symbols = self.watchlist_symbols()
        if not symbols:
            self.status.setText("The watchlist needs at least one symbol.")
            return
        self.watchlist_edit.setText(", ".join(symbols))
        if self.context is not None:
            before = load_watchlist(self.context.profile_dir).symbols
            save_watchlist(self.context.profile_dir, Watchlist(symbols=symbols))
            audit("watchlist changed", before=before, after=symbols)
            self.context.watch.refresh()
        self._make_cards(symbols)
        self.status.setText("Watchlist saved. Cards fill on the next poll.")

    def _make_cards(self, symbols: list[str]) -> None:
        for card in self.cards.values():
            self.cards_grid.removeWidget(card)
            card.deleteLater()
        self.cards = {}
        for index, symbol in enumerate(symbols):
            card = SymbolCard(symbol)
            self.cards[symbol] = card
            self.cards_grid.addWidget(card, index // CARD_COLUMNS, index % CARD_COLUMNS)
        current = self.chart_symbol.currentText()
        self.chart_symbol.blockSignals(True)
        self.chart_symbol.clear()
        self.chart_symbol.addItems(symbols)
        if current in symbols:
            self.chart_symbol.setCurrentText(current)
        self.chart_symbol.blockSignals(False)

    # Snapshots ---------------------------------------------------------------------------
    def show_snapshot(self, snapshot: object) -> None:
        """Slot: a new analysis snapshot (from the analysis thread, queued)."""
        if not isinstance(snapshot, MarketSnapshot):
            return
        previous = self.last_snapshot
        self.last_snapshot = snapshot
        clock = f" Broker time: {snapshot.clock_text}." if snapshot.clock_text else ""
        self.status.setText(f"{snapshot.message}.{clock}")
        for name, analysis in snapshot.analyses.items():
            card = self.cards.get(name)
            if card is not None and card.bar_time != analysis.bar_time:
                card.show_analysis(analysis)
        changed = previous is None or any(
            previous.analyses.get(name) is not item for name, item in snapshot.analyses.items()
        )
        if changed:
            self._show_matrix(snapshot)
            self._chart_symbol_changed(self.chart_symbol.currentText())
        if previous is None or previous.correlation is not snapshot.correlation:
            self._show_correlation(snapshot)
        if previous is None or previous.strength is not snapshot.strength:
            self._show_strength(snapshot)

    def _chart_symbol_changed(self, symbol: str) -> None:
        snapshot = self.last_snapshot
        self.chart.set_analysis(snapshot.analyses.get(symbol) if snapshot is not None else None)

    def _show_matrix(self, snapshot: MarketSnapshot) -> None:
        names = list(snapshot.analyses)
        self.matrix.setRowCount(len(names))
        for row, name in enumerate(names):
            matrix = snapshot.analyses[name].trend
            self.matrix.setItem(row, 0, _item(name))
            for column, timeframe in enumerate(ANALYSIS_TIMEFRAMES, start=1):
                cell = matrix.get(timeframe)
                if cell is None or not cell.enough_data:
                    self.matrix.setItem(row, column, _item("n/a", "not enough history"))
                    continue
                text = f"{ARROWS[cell.direction]} {cell.score:+.0f}"
                self.matrix.setItem(row, column, _item(text, "\n".join(cell.reasons)))
            bias = f"{ARROWS[matrix.direction]} {matrix.bias:+.0f}"
            last = len(ANALYSIS_TIMEFRAMES) + 1
            self.matrix.setItem(row, last, _item(bias, "\n".join(matrix.reasons)))

    def _show_correlation(self, snapshot: MarketSnapshot) -> None:
        matrix = snapshot.correlation
        symbols = list(matrix.symbols) if matrix is not None else []
        self.correlation.clear()
        self.correlation.setColumnCount(len(symbols) + 1)
        self.correlation.setHorizontalHeaderLabels(["Symbol", *symbols])
        self.correlation.setRowCount(len(symbols))
        if matrix is None:
            return
        for row, name in enumerate(symbols):
            self.correlation.setItem(row, 0, _item(name))
            for column, other in enumerate(symbols, start=1):
                value = matrix.value(name, other)
                text = "n/a" if not math.isfinite(value) else f"{value:+.2f}"
                tooltip = f"{matrix.returns} hourly returns"
                self.correlation.setItem(row, column, _item(text, tooltip))

    def _show_strength(self, snapshot: MarketSnapshot) -> None:
        self.strength.setRowCount(len(snapshot.strength))
        for row, item in enumerate(snapshot.strength):
            self.strength.setItem(row, 0, _item(str(item.rank)))
            self.strength.setItem(row, 1, _item(item.currency))
            self.strength.setItem(row, 2, _item(f"{item.score:+.2f}"))
            self.strength.setItem(row, 3, _item(str(item.pairs)))

    # Calendar ----------------------------------------------------------------------------
    def _build_calendar(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(styled_label(EXPORTER_HELP, "muted", wrap=True))
        buttons = QHBoxLayout()
        self.install_button = QPushButton("Install MT5 exporter")
        self.install_button.clicked.connect(self.install_exporter)
        self.read_button = QPushButton("Read MT5 calendar now")
        self.read_button.clicked.connect(self.read_calendar_now)
        self.import_button = QPushButton("Import CSV...")
        self.import_button.clicked.connect(self.choose_csv)
        for button in (self.install_button, self.read_button, self.import_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        form = QHBoxLayout()
        self.event_time = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        self.event_time.setTimeSpec(Qt.TimeSpec.UTC)
        self.event_time.setDisplayFormat("yyyy-MM-dd HH:mm 'UTC'")
        self.event_currency = QLineEdit()
        self.event_currency.setPlaceholderText("USD")
        self.event_currency.setMaxLength(3)
        self.event_impact = QComboBox()
        self.event_impact.addItems([Impact.HIGH.value, Impact.MEDIUM.value, Impact.LOW.value])
        self.event_title = QLineEdit()
        self.event_title.setPlaceholderText("Event, for example CPI m/m")
        self.add_event_button = QPushButton("Add event")
        self.add_event_button.clicked.connect(self.add_manual_event)
        for widget in (self.event_time, self.event_currency, self.event_impact):
            form.addWidget(widget)
        form.addWidget(self.event_title, 1)
        form.addWidget(self.add_event_button)
        layout.addLayout(form)
        self.calendar_status = styled_label("", "muted", wrap=True)
        layout.addWidget(self.calendar_status)
        self.calendar_table = _table(CALENDAR_COLUMNS)
        layout.addWidget(self.calendar_table, 1)
        return tab

    def reload_calendar(self) -> None:
        context = self.context
        if context is None or context.calendar is None:
            self.calendar_status.setText("The local database is not open, so the calendar is off.")
            return
        now = time.time()
        found = context.calendar.recent_and_upcoming(now)
        self._calendar_events = upcoming(found, now, hours=7 * 24, min_impact=Impact.LOW)
        self._refresh_countdowns()

    def _refresh_countdowns(self) -> None:
        now = time.time()
        events = [event for event in self._calendar_events if event.time >= now - 15 * 60]
        self.calendar_table.setRowCount(len(events))
        for row, event in enumerate(events):
            when = datetime.fromtimestamp(event.time).strftime("%a %d %b %H:%M")
            values = [
                when,
                event.countdown(now),
                event.currency,
                event.impact.value,
                event.title,
                event.actual,
                event.forecast,
                event.previous,
            ]
            for column, value in enumerate(values):
                self.calendar_table.setItem(row, column, _item(value, f"source: {event.source}"))
        if not events and not self.calendar_status.text():
            self.calendar_status.setText(
                "No upcoming events. Install the MT5 exporter or import a CSV.",
            )

    def add_manual_event(self) -> None:
        context = self.context
        currency = self.event_currency.text().strip().upper()
        title = self.event_title.text().strip()
        if context is None or context.calendar is None or len(currency) != 3 or not title:
            self.calendar_status.setText("Enter a 3-letter currency and a title.")
            return
        moment = int(self.event_time.dateTime().toSecsSinceEpoch())
        event = CalendarEvent(moment, currency, Impact(self.event_impact.currentText()), title)
        context.calendar.save([event])
        details = {"time": event.when(), "currency": currency, "title": title}
        audit("calendar event added", after=details)
        self.event_title.clear()
        self.calendar_status.setText(f"Added: {event.currency} {event.title}, {event.when()}.")
        self._calendar_changed()

    def choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import calendar CSV",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.import_csv(Path(path))

    def import_csv(self, path: Path) -> None:
        context = self.context
        if context is None or context.calendar is None:
            return
        store = context.calendar

        def work() -> object:
            result: CsvResult = parse_calendar_csv(decode(path.read_bytes()), source="csv")
            saved = store.save(result.events)
            return f"{path.name}: {result.text()}, {saved} new or changed."

        self._background(work)

    def read_calendar_now(self) -> None:
        context = self.context
        if context is None or context.calendar is None:
            return
        watcher, calendar = context.calendar_file, context.calendar
        watcher.reset()
        self._background(lambda: import_exporter_file(watcher, calendar))

    def install_exporter(self) -> None:
        context = self.context
        if context is None:
            return
        try:
            target = install_exporter(context.data_path())
        except OSError as error:
            self.calendar_status.setText(f"Could not install the exporter: {error}")
            return
        audit("calendar exporter installed", after=str(target))
        self.calendar_status.setText(
            f"Copied to {target}. Now compile it in MetaEditor (F7) and start it in "
            "Navigator > Services.",
        )

    def _background(self, work: Callable[[], object]) -> None:
        def run() -> None:
            try:
                result: object = work()
            except Exception as error:
                result = f"Calendar import failed: {type(error).__name__}: {error}"
            self.bridge.calendar.emit(result)

        threading.Thread(target=run, name="calendar-import", daemon=True).start()

    def _calendar_done(self, result: object) -> None:
        self.calendar_status.setText(str(result))
        self._calendar_changed()

    def _calendar_changed(self) -> None:
        self.reload_calendar()
        if self.context is not None:
            self.context.watch.refresh()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.chart.apply_tokens(tokens)

    def next_news_text(self, now: float | None = None) -> str:
        """For the status bar: the next high-impact event of a watched currency."""
        moment = time.time() if now is None else now
        events = self.last_snapshot.events if self.last_snapshot is not None else ()
        symbols = list(self.cards)
        found = upcoming(events, moment, hours=48, min_impact=Impact.HIGH, symbols=symbols)
        future = [event for event in found if event.time >= moment]
        return f"Next news: {future[0].short(moment)}" if future else "Next news: none in 48h"
