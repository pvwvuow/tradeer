"""Interactive candle chart (spec F3) on pyqtgraph: timeframe switch, zoom and pan, crosshair,
EMA overlays, key levels, swing structure and session shading.

The x axis counts bars (no gaps for nights and weekends); its labels show broker server time,
as MT5 does. Only the bars kept by the analysis are drawn (at most 600 per timeframe), and
the y axis fits the visible bars. Since 7 October 2026 the prices sit on the right, like
trading platforms. Since 8 October 2026 the live bid (with its price in a tag) and the ask
are drawn from the quote the market watch reads every 2 seconds, as MT5 draws them; before
the first quote a dotted line marks the last close.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.analysis import indicators
from app.analysis.bars import ANALYSIS_TIMEFRAMES, Bars
from app.analysis.sessions import Session, session_windows
from app.analysis.structure import SwingKind, analyze_structure
from app.analysis.symbol import SymbolAnalysis
from app.ui.theme import DARK, ThemeTokens

VISIBLE_BARS = 150
EMA_PERIODS = (20, 50, 200)
MAX_LEVELS = 8
SESSION_DAYS = 10
INTRADAY = ("M5", "M15", "H1")
PRICE_AXIS = "right"
SESSION_COLORS: dict[Session, tuple[int, int, int]] = {
    Session.ASIA: (91, 140, 255),
    Session.LONDON: (34, 197, 94),
    Session.NEW_YORK: (245, 158, 11),
}


def time_ticks(bars: Bars, left: float, right: float, count: int = 6) -> list[tuple[int, str]]:
    """About `count` evenly spaced labels (broker time, as MT5) for the visible bar indexes."""
    if not len(bars):
        return []
    first = max(int(np.ceil(left)), 0)
    last = min(int(np.floor(right)), len(bars) - 1)
    if last < first:
        return []
    step = max((last - first) // max(count - 1, 1), 1)
    pattern = "%d %b %Y" if bars.timeframe == "D1" else "%d %b %H:%M"
    return [
        (index, bar_moment(bars, index).strftime(pattern))
        for index in range(first, last + 1, step)
    ]


def bar_moment(bars: Bars, index: int) -> datetime:
    """A bar's open in broker server time, as MT5 labels its charts (8 October 2026: local
    time made the candles look shifted against MT5). Local time when there is no server time."""
    if len(bars.server_time) == len(bars):
        return datetime.fromtimestamp(int(bars.server_time[index]), UTC)
    return datetime.fromtimestamp(int(bars.time[index]))


class CandleChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CandleChart")
        self._analysis: SymbolAnalysis | None = None
        self._bars: Bars | None = None
        self._timeframe = "H1"
        self._tokens: ThemeTokens = DARK
        self._items: list[Any] = []
        self._price_items: list[Any] = []
        self._quote: tuple[float, float, int] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.timeframe_buttons: dict[str, QPushButton] = {}
        for timeframe in ANALYSIS_TIMEFRAMES:
            button = QPushButton(timeframe)
            button.setObjectName(f"chart_tf_{timeframe}")
            button.setCheckable(True)
            button.setChecked(timeframe == self._timeframe)
            button.clicked.connect(partial(self.set_timeframe, timeframe))
            self._group.addButton(button)
            self.timeframe_buttons[timeframe] = button
            toolbar.addWidget(button)
        toolbar.addSpacing(16)
        self.show_ema = QCheckBox("EMA 20/50/200")
        self.show_levels = QCheckBox("Levels")
        self.show_structure = QCheckBox("Swings")
        self.show_sessions = QCheckBox("Sessions")
        for box in (self.show_ema, self.show_levels, self.show_structure, self.show_sessions):
            box.setChecked(True)
            box.toggled.connect(self.redraw)
            toolbar.addWidget(box)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.info = QLabel("No bars yet. Connect to MT5 and the chart fills on the next bar.")
        self.info.setProperty("role", "muted")
        layout.addWidget(self.info)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setMinimumHeight(320)
        self.plot = self.plot_widget.getPlotItem()
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showAxis(PRICE_AXIS)
        self.plot.hideAxis("left")
        self.view = self.plot.getViewBox()
        self.view.setAutoVisible(y=True)
        self._v_line = pg.InfiniteLine(angle=90, movable=False)
        self._h_line = pg.InfiniteLine(angle=0, movable=False)
        self.plot.addItem(self._v_line, ignoreBounds=True)
        self.plot.addItem(self._h_line, ignoreBounds=True)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)
        self.view.sigXRangeChanged.connect(self._on_range)
        layout.addWidget(self.plot_widget, 1)
        self.apply_tokens(DARK)

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def item_count(self) -> int:
        return len(self._items)

    def set_analysis(self, analysis: SymbolAnalysis | None) -> None:
        if analysis is None or self._analysis is None or analysis.symbol != self._analysis.symbol:
            self._quote = None  # another symbol: its price comes with the next quote
        self._analysis = analysis
        self.redraw()

    def set_quote(self, bid: float | None, ask: float | None, server_time: int = 0) -> None:
        """The live price of the shown symbol (every market watch poll): moves the bid and ask
        lines without drawing the candles again. None clears them."""
        valid = bid is not None and ask is not None and 0 < bid <= ask
        quote = (float(bid or 0.0), float(ask or 0.0), int(server_time)) if valid else None
        if quote == self._quote:
            return
        self._quote = quote
        if self._bars is not None and self._analysis is not None:
            self._draw_last_price(self._bars, self._analysis.digits)
            self._show_info()

    def set_timeframe(self, timeframe: str) -> None:
        self._timeframe = timeframe
        self.timeframe_buttons[timeframe].setChecked(True)
        self.redraw()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.plot_widget.setBackground(tokens.card)
        for name in (PRICE_AXIS, "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(pg.mkPen(tokens.border))
            axis.setTextPen(pg.mkPen(tokens.text_secondary))
        cross = pg.mkPen(tokens.text_secondary, width=1, style=Qt.PenStyle.DotLine)
        self._v_line.setPen(cross)
        self._h_line.setPen(cross)
        self.redraw()

    def redraw(self, *_args: object) -> None:
        for item in self._items:
            self.plot.removeItem(item)
        self._items = []
        self._price_items = []
        analysis = self._analysis
        bars = analysis.bars.get(self._timeframe) if analysis is not None else None
        self._bars = bars if bars is not None and len(bars) else None
        if analysis is None or self._bars is None:
            self.info.setText("No closed bars for this timeframe yet.")
            return
        bars = self._bars
        self._draw_candles(bars)
        if self.show_ema.isChecked():
            self._draw_emas(bars)
        if self.show_sessions.isChecked() and bars.timeframe in INTRADAY:
            self._draw_sessions(bars)
        if self.show_levels.isChecked():
            self._draw_levels(analysis)
        if self.show_structure.isChecked():
            self._draw_swings(analysis, bars)
        self._draw_last_price(bars, analysis.digits)
        size = len(bars)
        self.plot.setXRange(max(size - VISIBLE_BARS, 0) - 0.5, size + 1.5, padding=0)
        self._update_ticks()
        self._show_info()

    def _show_info(self) -> None:
        analysis, bars = self._analysis, self._bars
        if analysis is None or bars is None:
            return
        text = f"{analysis.symbol} {bars.timeframe}: {len(bars)} closed bars"
        if self._quote is not None:
            bid, ask, server_time = self._quote
            digits = analysis.digits
            text += f", live bid {bid:,.{digits}f} ask {ask:,.{digits}f}"
            if server_time:
                moment = datetime.fromtimestamp(server_time, UTC).strftime("%H:%M:%S")
                text += f" at {moment} broker time"
        self.info.setText(f"{text}. Drag to pan, wheel to zoom.")

    # Drawing -----------------------------------------------------------------------------
    def _add(self, item: Any, ignore_bounds: bool = False) -> None:
        self.plot.addItem(item, ignoreBounds=ignore_bounds)
        self._items.append(item)

    def _draw_candles(self, bars: Bars) -> None:
        x = np.arange(len(bars), dtype=np.float64)
        up = bars.close >= bars.open
        floor = float(np.nanmax(bars.high) - np.nanmin(bars.low)) * 1e-4 or 1e-9
        height = np.maximum(np.abs(bars.close - bars.open), floor)
        colors = [self._tokens.profit if rising else self._tokens.loss for rising in up]
        brushes = [pg.mkBrush(color) for color in colors]
        pens = [pg.mkPen(color) for color in colors]
        bodies = pg.BarGraphItem(
            x=x,
            height=height,
            width=0.6,
            y0=np.minimum(bars.open, bars.close),
            brushes=brushes,
            pens=pens,
        )
        self._add(bodies)
        for mask, color in ((up, self._tokens.profit), (~up, self._tokens.loss)):
            if not np.any(mask):
                continue
            xs = np.repeat(x[mask], 2)
            ys = np.column_stack([bars.low[mask], bars.high[mask]]).ravel()
            self._add(pg.PlotDataItem(xs, ys, connect="pairs", pen=pg.mkPen(color, width=1)))

    def _draw_last_price(self, bars: Bars, digits: int) -> None:
        """The live bid in a tag and the ask as a dotted line, like MT5; before the first
        quote a dotted line at the last close. Only these lines are drawn again."""
        for item in self._price_items:
            self.plot.removeItem(item)
            if item in self._items:
                self._items.remove(item)
        self._price_items = []
        close = float(bars.close[-1])
        if self._quote is None:
            rising = bool(bars.close[-1] >= bars.open[-1])
            self._price_line(close, rising, f"{close:,.{digits}f}", Qt.PenStyle.DotLine)
            return
        bid, ask, _ = self._quote
        rising = bid >= close
        self._price_line(bid, rising, f"{bid:,.{digits}f}", Qt.PenStyle.SolidLine)
        line = pg.InfiniteLine(
            pos=ask,
            angle=0,
            movable=False,
            pen=pg.mkPen(self._tokens.loss, width=1, style=Qt.PenStyle.DotLine),
        )
        self._add(line, ignore_bounds=True)
        self._price_items.append(line)

    def _price_line(self, price: float, rising: bool, label: str, style: Qt.PenStyle) -> None:
        color = self._tokens.profit if rising else self._tokens.loss
        line = pg.InfiniteLine(
            pos=price,
            angle=0,
            movable=False,
            pen=pg.mkPen(color, width=1, style=style),
            label=label,
            labelOpts={"position": 0.97, "color": self._tokens.accent_text, "fill": color},
        )
        self._add(line, ignore_bounds=True)
        self._price_items.append(line)

    def _draw_emas(self, bars: Bars) -> None:
        x = np.arange(len(bars), dtype=np.float64)
        colors = (self._tokens.accent, self._tokens.warning, self._tokens.text_secondary)
        for period, color in zip(EMA_PERIODS, colors, strict=True):
            line = indicators.ema(bars.close, period)
            if np.any(np.isfinite(line)):
                pen = pg.mkPen(color, width=1.5)
                self._add(pg.PlotDataItem(x, line, connect="finite", pen=pen, name=f"EMA {period}"))

    def _draw_levels(self, analysis: SymbolAnalysis) -> None:
        for level in analysis.levels.levels[:MAX_LEVELS]:
            color = self._tokens.loss if level.side == "resistance" else self._tokens.profit
            line = pg.InfiniteLine(
                pos=level.price,
                angle=0,
                movable=False,
                pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine),
                label=f"{level.kind} {level.price:,.{analysis.digits}f}",
                labelOpts={"position": 0.08, "color": self._tokens.text_secondary},
            )
            self._add(line, ignore_bounds=True)

    def _draw_swings(self, analysis: SymbolAnalysis, bars: Bars) -> None:
        structure = analysis.structures.get(bars.timeframe) or analyze_structure(bars)
        swings = structure.swings[-40:]
        span = float(np.nanmax(bars.high) - np.nanmin(bars.low)) * 0.01
        for kind, symbol, sign in ((SwingKind.HIGH, "t", 1.0), (SwingKind.LOW, "t1", -1.0)):
            chosen = [swing for swing in swings if swing.kind is kind]
            if not chosen:
                continue
            marker = pg.ScatterPlotItem(
                x=[float(swing.index) for swing in chosen],
                y=[swing.price + sign * span for swing in chosen],
                symbol=symbol,
                size=8,
                pen=None,
                brush=pg.mkBrush(self._tokens.text_secondary),
            )
            self._add(marker)
            for swing in chosen[-8:]:
                if swing.label:
                    anchor = (0.5, 1.0 if sign > 0 else 0.0)
                    color = self._tokens.text_secondary
                    text = pg.TextItem(swing.label, color=color, anchor=anchor)
                    text.setPos(float(swing.index), swing.price + sign * span * 3)
                    self._add(text, ignore_bounds=True)

    def _draw_sessions(self, bars: Bars) -> None:
        last_day = datetime.fromtimestamp(int(bars.time[-1])).date()
        for back in range(SESSION_DAYS):
            day = last_day - timedelta(days=back)
            for window in session_windows(day):
                if window.end <= bars.time[0] or window.start > bars.time[-1]:
                    continue
                first = int(np.searchsorted(bars.time, window.start, side="left"))
                last = int(np.searchsorted(bars.time, window.end, side="left"))
                if last <= first:
                    continue
                red, green, blue = SESSION_COLORS[window.session]
                region = pg.LinearRegionItem(
                    values=(first - 0.5, last - 0.5),
                    orientation="vertical",
                    movable=False,
                    brush=pg.mkBrush(red, green, blue, 18),
                    pen=pg.mkPen(None),
                )
                region.setZValue(-10)
                self._add(region, ignore_bounds=True)

    # Interaction -------------------------------------------------------------------------
    def _on_range(self, *_args: object) -> None:
        self._update_ticks()

    def _update_ticks(self) -> None:
        if self._bars is None:
            return
        left, right = self.view.viewRange()[0]
        self.plot.getAxis("bottom").setTicks([time_ticks(self._bars, left, right), []])

    def _on_mouse(self, position: QPointF) -> None:
        bars, analysis = self._bars, self._analysis
        if bars is None or analysis is None or not self.plot.sceneBoundingRect().contains(position):
            return
        point = self.view.mapSceneToView(position)
        index = min(max(int(round(point.x())), 0), len(bars) - 1)
        self._v_line.setPos(index)
        self._h_line.setPos(point.y())
        digits = analysis.digits
        moment = bar_moment(bars, index).strftime("%a %d %b %H:%M")
        self.info.setText(
            f"{moment}  O {bars.open[index]:,.{digits}f}  H {bars.high[index]:,.{digits}f}  "
            f"L {bars.low[index]:,.{digits}f}  C {bars.close[index]:,.{digits}f}  "
            f"\u00b7 cursor {point.y():,.{digits}f}",
        )
