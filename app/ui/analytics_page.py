"""The Analytics page (spec C11, F3 page 5): statistics, breakdowns, charts, trader behavior,
risk of ruin with a Monte-Carlo projection and comparisons, for the trades the filters pick.

Everything is computed by `app.analytics` from the closed trades on this PC; the page only
draws it. Export writes CSV files (trades and the shown breakdown) and the shown chart as a
PNG into `profiles/<profile>/exports`.

Charts take their colors from the current theme, and wins and losses in the excursion chart
differ by shape too (up and down triangles), not by color alone (spec F1).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QPushButton,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.analytics import behavior, charts, compare
from app.analytics.breakdowns import KEYS, breakdown, label
from app.analytics.export import groups_csv, trades_csv, write_text
from app.analytics.projection import project
from app.analytics.stats import compute_stats, equity_curve, stat_rows
from app.analytics.trades import TradeFilter, TradeRecord, choices
from app.ui.pages import PAGE_MARGIN, styled_label
from app.ui.style import chart_pen, chart_tokens, soft_color
from app.ui.tables import fill_table, heat, make_table, number, signed

ALL = "All"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
COMPARISONS = ("Live vs paper", "By strategy", "By config", "Last 30 days vs the 30 before")
EXPORT_FOLDER = "exports"
GROUP_COLUMNS = ("Group", "Trades", "Win rate", "Net", "Expectancy R", "Profit factor")
EXCURSION_KEY = "\u25b2 winning trade     \u25bc losing trade"


def _quiet(level: str, message: str) -> None:
    return None


def _no_events(start: float, end: float) -> Sequence[behavior.NewsEvent]:
    return ()


def _no_runs() -> Sequence[tuple[str, Mapping[str, Any]]]:
    return ()


@dataclass
class AnalyticsContext:
    trades: Callable[[], Sequence[TradeRecord]]
    balance: Callable[[], float]  # the account balance now, NaN while unknown
    export_dir: Path
    currency: Callable[[], str] = lambda: ""
    events: Callable[[float, float], Sequence[behavior.NewsEvent]] = _no_events
    backtests: Callable[[], Sequence[tuple[str, Mapping[str, Any]]]] = field(default=_no_runs)
    log: Callable[[str, str], None] = _quiet


def start_balance(balance: float, trades: Sequence[TradeRecord]) -> float:
    """The balance before the trades shown: today's balance minus their net result."""
    if not math.isfinite(balance) or balance <= 0:
        return 0.0
    found = balance - sum(t.net_profit for t in trades)
    return found if found > 0 else 0.0


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _seconds(value: QDate) -> float:
    return datetime(value.year(), value.month(), value.day(), tzinfo=UTC).timestamp()


class AnalyticsPage(QWidget):
    def __init__(self, context: AnalyticsContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_analytics")
        self.context = context
        self.trades: list[TradeRecord] = []
        self.shown: list[TradeRecord] = []
        self.start = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Analytics", "title"))
        layout.addLayout(self._build_filters())
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("AnalyticsStatus")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("AnalyticsTabs")
        self._build_tabs()
        layout.addWidget(self.tabs, 1)
        enabled = context is not None
        for widget in (self.refresh_button, self.export_button, self.png_button):
            widget.setEnabled(enabled)
        if context is None:
            self.status.setText("Analytics needs the local database, which is not running.")
        else:
            self.refresh()

    # Layout ------------------------------------------------------------------------------
    def _build_filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        today = datetime.now(UTC).date()
        self.date_from = QDateEdit(_qdate(today - timedelta(days=365)))
        self.date_to = QDateEdit(_qdate(today))
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        self.symbol = QComboBox()
        self.strategy = QComboBox()
        self.mode = QComboBox()
        self.account = QComboBox()
        self.source = QComboBox()
        self.source.addItems([ALL, "bot", "manual"])
        for combo in (self.symbol, self.strategy, self.mode, self.account):
            combo.addItem(ALL)
        self.refresh_button = QPushButton("Update")
        self.refresh_button.setProperty("variant", "primary")
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button = QPushButton("Export CSV")
        self.export_button.clicked.connect(self.export_csv)
        self.png_button = QPushButton("Save chart PNG")
        self.png_button.clicked.connect(self.export_png)
        widgets: list[QWidget] = [
            styled_label("From", "muted"),
            self.date_from,
            styled_label("to", "muted"),
            self.date_to,
            self.account,
            self.symbol,
            self.strategy,
            self.mode,
            self.source,
            self.refresh_button,
            self.export_button,
            self.png_button,
        ]
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _build_tabs(self) -> None:
        self.summary = make_table(("Statistic", "Value"))
        self.summary.setObjectName("AnalyticsSummary")
        self.tabs.addTab(self.summary, "Summary")
        groups = QWidget()
        groups_layout = QVBoxLayout(groups)
        self.group_choice = QComboBox()
        self.group_choice.addItems(list(KEYS))
        self.group_choice.currentTextChanged.connect(lambda _: self._show_groups())
        self.groups = make_table(GROUP_COLUMNS)
        self.groups.setObjectName("AnalyticsGroups")
        groups_layout.addWidget(self.group_choice)
        groups_layout.addWidget(self.groups)
        self.tabs.addTab(groups, "Breakdowns")
        curves = QWidget()
        curves_layout = QVBoxLayout(curves)
        self.equity_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.equity_plot.setLabel("left", "Equity")
        self.drawdown_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.drawdown_plot.setLabel("left", "Drawdown %")
        self.drawdown_plot.setXLink(self.equity_plot)
        curves_layout.addWidget(self.equity_plot, 3)
        curves_layout.addWidget(self.drawdown_plot, 1)
        self.tabs.addTab(curves, "Equity")
        self.monthly = make_table(("Year", *MONTHS, "Year"))
        self.monthly.setObjectName("AnalyticsMonthly")
        self.tabs.addTab(self.monthly, "Monthly returns")
        spread = QWidget()
        spread_layout = QHBoxLayout(spread)
        self.r_plot = pg.PlotWidget()
        self.r_plot.setLabel("bottom", "R multiple")
        self.r_plot.setLabel("left", "Trades")
        self.excursion_plot = pg.PlotWidget()
        self.excursion_plot.setLabel("bottom", "Worst move (MAE, R)")
        self.excursion_plot.setLabel("left", "Best move (MFE, R)")
        self.excursion_plot.setTitle(EXCURSION_KEY)
        spread_layout.addWidget(self.r_plot)
        spread_layout.addWidget(self.excursion_plot)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(spread, 1)
        self.excursion_text = styled_label("", "muted", wrap=True)
        self.excursion_text.setObjectName("AnalyticsExcursions")
        box_layout.addWidget(self.excursion_text)
        self.tabs.addTab(box, "R and MFE/MAE")
        self.costs = styled_label("", "muted", wrap=True)
        self.costs.setObjectName("AnalyticsCosts")
        costs = QWidget()
        costs_layout = QVBoxLayout(costs)
        costs_layout.addWidget(self.costs)
        costs_layout.addStretch(1)
        self.tabs.addTab(costs, "Costs")
        self.behavior = make_table(("Check", "Flag", "Finding"))
        self.behavior.setObjectName("AnalyticsBehavior")
        self.tabs.addTab(self.behavior, "Behavior")
        future = QWidget()
        future_layout = QVBoxLayout(future)
        self.projection_text = styled_label("", "muted", wrap=True)
        self.projection_text.setObjectName("AnalyticsProjection")
        self.projection_plot = pg.PlotWidget()
        self.projection_plot.setLabel("bottom", "Trades from now")
        self.projection_plot.setLabel("left", "Balance")
        future_layout.addWidget(self.projection_text)
        future_layout.addWidget(self.projection_plot, 1)
        self.tabs.addTab(future, "Risk of ruin")
        versus = QWidget()
        versus_layout = QVBoxLayout(versus)
        self.compare_choice = QComboBox()
        self.compare_choice.addItems(list(COMPARISONS))
        self.compare_choice.currentTextChanged.connect(lambda _: self._show_compare())
        self.compare_table = make_table(("Statistic",))
        self.compare_table.setObjectName("AnalyticsCompare")
        versus_layout.addWidget(self.compare_choice)
        versus_layout.addWidget(self.compare_table)
        self.tabs.addTab(versus, "Compare")

    # Data --------------------------------------------------------------------------------
    def current_filter(self) -> TradeFilter:
        def pick(combo: QComboBox) -> str:
            text = combo.currentText()
            return "" if text in ("", ALL) else text

        end = _seconds(self.date_to.date()) + 86_400
        return TradeFilter(
            start=_seconds(self.date_from.date()),
            end=end,
            account=pick(self.account),
            symbol=pick(self.symbol),
            strategy=pick(self.strategy),
            mode=pick(self.mode),
            source=pick(self.source),
        )

    def refresh(self) -> None:
        context = self.context
        if context is None:
            return
        try:
            self.trades = list(context.trades())
        except Exception as error:
            self.status.setText(f"The trades could not be read: {type(error).__name__}: {error}")
            return
        self._fill_choices()
        self.shown = self.current_filter().apply(self.trades)
        self.start = start_balance(context.balance(), self.shown)
        currency = context.currency()
        stats = compute_stats(self.shown, self.start)
        fill_table(self.summary, stat_rows(stats, currency))
        notes = " ".join(stats.warnings)
        counts = f"{len(self.shown)} of {len(self.trades)} closed trades."
        self.status.setText(f"{counts} {notes}".strip())
        self._show_groups()
        self._show_curves()
        self._show_monthly()
        self._show_distribution()
        self.costs.setText("\n".join(charts.cost_analysis(self.shown).lines))
        self._show_behavior()
        self._show_projection(currency)
        self._show_compare()

    def _fill_choices(self) -> None:
        found = choices(self.trades)
        for combo, key in (
            (self.account, "account"),
            (self.symbol, "symbol"),
            (self.strategy, "strategy"),
            (self.mode, "mode"),
        ):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([ALL, *found[key]])
            combo.setCurrentText(current if current in found[key] else ALL)
            combo.blockSignals(False)

    def _show_groups(self) -> None:
        rows = [
            [
                label(item.key),
                str(item.trades),
                f"{item.win_rate * 100:.0f}%",
                signed(item.net_profit),
                number(item.expectancy_r, 3),
                number(item.profit_factor),
            ]
            for item in breakdown(self.shown, self.group_choice.currentText())
        ]
        fill_table(self.groups, rows)

    def _show_curves(self) -> None:
        self.equity_plot.clear()
        self.drawdown_plot.clear()
        curve = equity_curve(self.shown, self.start)
        if not len(curve.times):
            return
        tokens = chart_tokens()
        self.equity_plot.plot(curve.times, curve.equity, pen=chart_pen(tokens.accent, 2.0))
        if self.start > 0:
            self.drawdown_plot.plot(
                curve.times,
                curve.drawdown_percent,
                pen=chart_pen(tokens.loss),
                fillLevel=0,
                brush=soft_color(tokens.loss, 60),
            )

    def _show_monthly(self) -> None:
        returns = charts.monthly_returns(self.shown, self.start)
        years = sorted({key[:4] for key in returns})
        self.monthly.setRowCount(len(years))
        for row, year in enumerate(years):
            self.monthly.setItem(row, 0, QTableWidgetItem(year))
            total = 1.0
            for month in range(12):
                value = returns.get(f"{year}-{month + 1:02d}")
                item = QTableWidgetItem("" if value is None else f"{value:+.1f}%")
                item.setBackground(heat(value))
                self.monthly.setItem(row, month + 1, item)
                if value is not None and math.isfinite(value):
                    total *= 1.0 + value / 100.0
            year_item = QTableWidgetItem(f"{(total - 1.0) * 100:+.1f}%")
            year_item.setBackground(heat((total - 1.0) * 100.0))
            self.monthly.setItem(row, 13, year_item)

    def _show_distribution(self) -> None:
        self.r_plot.clear()
        self.excursion_plot.clear()
        width = 0.5
        tokens = chart_tokens()
        bins = charts.r_distribution(self.shown, width)
        if bins:
            xs = [low + width / 2 for low, _count in bins]
            heights = [count for _low, count in bins]
            colours = [tokens.profit if low >= 0 else tokens.loss for low, _count in bins]
            bars = pg.BarGraphItem(x=xs, height=heights, width=width * 0.9, brushes=colours)
            self.r_plot.addItem(bars)
        summary = charts.excursions(self.shown)
        if summary.points:
            wins = [p for p in summary.points if p.win]
            losses = [p for p in summary.points if not p.win]
            for points, colour, shape in ((wins, tokens.profit, "t1"), (losses, tokens.loss, "t")):
                if points:
                    self.excursion_plot.plot(
                        [p.mae_r for p in points],
                        [p.mfe_r for p in points],
                        pen=None,
                        symbol=shape,
                        symbolSize=8,
                        symbolBrush=colour,
                    )
        self.excursion_text.setText("\n".join(summary.lines))

    def _show_behavior(self) -> None:
        context = self.context
        events: Sequence[behavior.NewsEvent] = ()
        if context is not None and self.shown:
            first = min(t.open_time for t in self.shown) - 3600
            last = max(t.open_time for t in self.shown) + 3600
            events = context.events(first, last)
        findings = behavior.behavior_report(self.shown, events)
        rows = [[f.name, "\u26a0 check this" if f.flagged else "ok", f.detail] for f in findings]
        fill_table(self.behavior, rows)

    def _show_projection(self, currency: str) -> None:
        self.projection_plot.clear()
        found = project(self.shown, self.start)
        if found is None:
            self.projection_text.setText(
                "The projection needs the account balance and at least 20 closed trades.",
            )
            return
        ruin = found.ruin
        unit = f" {currency}" if currency else ""
        self.projection_text.setText(
            f"{found.note}\nRisk of ruin (a {ruin.ruin_percent:.0f}% drawdown): "
            f"{ruin.risk_of_ruin * 100:.1f}%. After {len(found.steps)} trades: median "
            f"{found.median[-1]:,.2f}{unit}, 90% of runs between {found.low[-1]:,.2f} and "
            f"{found.high[-1]:,.2f}{unit}.",
        )
        steps = list(found.steps)
        tokens = chart_tokens()
        self.projection_plot.plot(steps, list(found.median), pen=chart_pen(tokens.accent, 2.0))
        for values in (found.low, found.high):
            self.projection_plot.plot(steps, list(values), pen=chart_pen(tokens.text_secondary))

    def _show_compare(self) -> None:
        choice = self.compare_choice.currentText()
        if choice == "Live vs paper":
            columns = compare.split(self.shown, "mode", self.start)
            if self.context is not None:
                runs = self.context.backtests()[:2]
                columns += [compare.backtest_column(name, m) for name, m in runs]
        elif choice == "By strategy":
            columns = compare.split(self.shown, "strategy", self.start)
        elif choice == "By config":
            columns = compare.split(self.shown, "config", self.start)
        else:
            end = _seconds(self.date_to.date()) + 86_400
            columns = compare.periods(
                self.shown,
                (end - 60 * 86_400, end - 30 * 86_400),
                (end - 30 * 86_400, end),
                self.start,
            )
        headers = ["Statistic", *(c.name for c in columns), "Change (last vs first)"]
        self.compare_table.setColumnCount(len(headers))
        self.compare_table.setHorizontalHeaderLabels(headers)
        fill_table(self.compare_table, compare.rows(columns) if columns else [])

    # Export ------------------------------------------------------------------------------
    def _folder(self) -> Path | None:
        return self.context.export_dir / EXPORT_FOLDER if self.context is not None else None

    def export_csv(self) -> list[Path]:
        folder = self._folder()
        if folder is None:
            return []
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        by = self.group_choice.currentText()
        paths = [
            write_text(folder / f"trades_{stamp}.csv", trades_csv(self.shown)),
            write_text(
                folder / f"breakdown_{by.split(' ')[0].lower()}_{stamp}.csv",
                groups_csv(by, breakdown(self.shown, by)),
            ),
        ]
        self.status.setText(f"Saved {paths[0].name} and {paths[1].name} in {folder}")
        if self.context is not None:
            self.context.log("INFO", f"Analytics exported to {folder}")
        return paths

    def export_png(self) -> Path | None:
        folder = self._folder()
        if folder is None:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        widget = self.tabs.currentWidget()
        name = self.tabs.tabText(self.tabs.currentIndex()).split(" ")[0].lower()
        path = folder / f"chart_{name}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.png"
        if widget is None or not widget.grab().save(str(path)):
            self.status.setText("The chart could not be saved.")
            return None
        self.status.setText(f"Saved {path.name} in {folder}")
        return path
