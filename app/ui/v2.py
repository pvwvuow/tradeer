"""The building blocks of the UI v2 pages, drawn like the owner's design (docs/UI_V2_EXACT.md).

Every page of the design is made of the same few parts, so they live here once:

- `Section`: a bold section title, an optional caption, a hairline and the actions at the end.
- `KpiStrip`: one outlined box of figures split by hairlines, each with a sparkline.
- `Meter`: the slim limit bar (ink, amber from 75%, coral at the limit).
- `EquityChart`: the equity line over three guide lines and the hatched drawdown band below.
- `Pipeline`: the five decision circles of a signal (passed, failed, not reached).
- `SessionsBar`: Tokyo, London and New York on a 24 hour UTC scale with a line at now.
- `DesignTable`: the design's table (capital mono headers, 44 px rows, hairlines) with tags
  and widgets in cells, ten rows a page.
- `Tag`, `Banner`, `InfoRow`: the small pieces.

All of them paint with the theme's tokens (`apply_tokens`), so the light theme works too.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.analysis.sessions import Session, session_windows
from app.ui.shell import CAPTION_PT, SMALL_PT, Painted, caption_font, tone_color
from app.ui.theme import DARK, NUMBER_FONT, ThemeTokens, number_family

ROW_HEIGHT = 44
HEADER_HEIGHT = 34
PAGE_SIZE = 10
WARN_SHARE = 0.75
CHECK = "\u2713"
END = Qt.AlignmentFlag.AlignTrailing
START = Qt.AlignmentFlag.AlignLeading
CROSS = "\u2715"
SESSION_NAMES: dict[Session, str] = {
    Session.ASIA: "TOKYO",
    Session.LONDON: "LONDON",
    Session.NEW_YORK: "NEW YORK",
}


def v2_qss(tokens: ThemeTokens) -> str:
    """The stylesheet of the v2 pieces, added after the frame's rules."""
    t = tokens
    return f"""
QLabel[v2="sec"] {{
    font-size: 10.5pt;
    font-weight: 600;
}}
QLabel[v2="cap"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
    font-size: {CAPTION_PT:g}pt;
}}
QLabel[v2="muted"] {{
    color: {t.text_secondary};
    font-size: 9pt;
}}
QLabel[v2="note"] {{
    color: {t.text_secondary};
    font-size: 9.4pt;
}}
QLabel[v2="kpi_caption"] {{
    color: {t.text_secondary};
    font-size: 9.4pt;
}}
QLabel[v2="kpi_sub"] {{
    color: {t.text_secondary};
    font-size: 9pt;
}}
QLabel[v2="kpi_value"] {{
    color: {t.text};
    font-family: {number_family()};
    font-size: 17pt;
    font-weight: 600;
}}
QLabel[v2="kpi_value"][tone="profit"] {{
    color: {t.profit};
}}
QLabel[v2="kpi_value"][tone="loss"] {{
    color: {t.loss};
}}
QLabel[v2="row"] {{
    font-size: 9.75pt;
}}
QLabel[v2="row_num"] {{
    font-family: {number_family()};
    font-size: 9.75pt;
}}
QLabel[v2="row_num"][tone="profit"], QLabel[v2="row"][tone="profit"] {{
    color: {t.profit};
}}
QLabel[v2="row_num"][tone="loss"], QLabel[v2="row"][tone="loss"] {{
    color: {t.loss};
}}
QLabel[v2="row_num"][tone="muted"] {{
    color: {t.text_secondary};
}}
QFrame[v2="kpis"] {{
    background-color: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
}}
QFrame[v2="kpis"] QLabel, QFrame[v2="kpis"] QWidget {{
    background: transparent;
}}
QFrame[v2="banner"] {{
    background-color: transparent;
    border: 1px solid {t.border_strong};
    border-radius: 8px;
}}
QFrame[v2="banner"][tone="loss"] {{
    background-color: {t.loss_soft};
    border-color: {t.loss};
}}
QFrame[v2="banner"][tone="warning"] {{
    background-color: {t.warning_soft};
    border-color: {t.warning};
}}
QFrame[v2="banner"] QLabel {{
    background: transparent;
    font-size: 9.75pt;
}}
QFrame[v2="empty"] {{
    background-color: transparent;
    border: 1px dashed {t.border_strong};
    border-radius: 8px;
}}
QFrame[v2="empty"] QLabel {{
    color: {t.text_secondary};
    font-size: 10pt;
}}
QFrame[v2="hair"] {{
    background-color: {t.border};
    border: none;
}}
QPushButton[v2="ghost"] {{
    background-color: transparent;
    color: {t.text};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0px 12px;
    min-height: 28px;
    max-height: 30px;
    font-size: 9.75pt;
    font-weight: 500;
}}
QPushButton[v2="ghost"]:hover {{
    border-color: {t.border_strong};
    background-color: transparent;
}}
QPushButton[v2="outline"] {{
    background-color: transparent;
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    padding: 0px 12px;
    min-height: 26px;
    max-height: 28px;
    font-size: 9.75pt;
    font-weight: 500;
}}
QPushButton[v2="outline"]:hover {{
    background-color: {t.hover};
    border-color: {t.text_secondary};
}}
QComboBox[v2="select"] {{
    min-height: 18px;
    max-height: 30px;
    padding: 2px 10px;
    font-size: 9.4pt;
    background-color: {t.bg};
    border: 1px solid {t.border_strong};
}}
QTableWidget[v2="table"] {{
    background-color: transparent;
    alternate-background-color: transparent;
    border: none;
    gridline-color: transparent;
    font-size: 9.75pt;
    selection-background-color: {t.hover};
    selection-color: {t.text};
}}
QTableWidget[v2="table"]::item {{
    border: none;
    border-top: 1px solid {t.border};
    padding: 0px 10px;
}}
QTableWidget[v2="table"]::item:hover {{
    background-color: {t.hover};
}}
QTableWidget[v2="table"] QHeaderView::section {{
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-bottom: 1px solid {t.border_strong};
    padding: 0px 10px;
    font-family: {number_family()};
    font-size: 8.25pt;
    font-weight: 400;
}}
QTableWidget[v2="table"] QWidget {{
    background: transparent;
}}
"""


def set_tone(label: QWidget, tone: str) -> None:
    """Change the `tone` property and re-apply the stylesheet when it changed."""
    if label.property("tone") != tone:
        label.setProperty("tone", tone)
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.update()


def v2_label(text: str, role: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("v2", role)
    label.setWordWrap(wrap)
    return label


def v2_button(text: str, variant: str = "ghost") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("v2", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def hairline(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("v2", "hair")
    if vertical:
        line.setFixedWidth(1)
    else:
        line.setFixedHeight(1)
    return line


class Tag(Painted):
    """The design's `tag`: a small outlined label (filled ink for `ink`)."""

    def __init__(self, text: str = "", tone: str = "neutral", mono: bool = True) -> None:
        super().__init__()
        self.text = text
        self.tone = tone
        self.mono = mono
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set(self, text: str, tone: str | None = None) -> None:
        self.text = text
        if tone is not None:
            self.tone = tone
        self.updateGeometry()
        self.update()

    def _font(self) -> QFont:
        if self.mono:
            return caption_font(SMALL_PT)
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        return font

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        metrics = QFontMetrics(self._font())
        return QSize(metrics.horizontalAdvance(self.text) + 16, 20)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0.5, (self.height() - 20) / 2 + 0.5, self.width() - 1, 19)
        ink = self.tone == "ink"
        color = QColor(t.text_secondary if self.tone == "neutral" else tone_color(t, self.tone))
        if ink:
            painter.setBrush(QColor(t.accent))
            painter.setPen(QPen(QColor(t.accent), 1))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            edge = t.border_strong if self.tone == "neutral" else tone_color(t, self.tone)
            painter.setPen(QPen(QColor(edge), 1))
        painter.drawRoundedRect(box, 3, 3)
        painter.setFont(self._font())
        painter.setPen(QColor(t.accent_text) if ink else color)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text)
        painter.end()


class Section(QWidget):
    """A section title row: title, caption, a hairline to the end, then the actions."""

    def __init__(self, title: str, caption: QWidget | None = None) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.title = v2_label(title, "sec")
        row.addWidget(self.title)
        if caption is not None:
            row.addWidget(caption)
        line = hairline()
        line.setMinimumWidth(20)
        row.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        row.addLayout(self.actions)
        self.setMinimumHeight(30)

    def add(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class Spark(Painted):
    """A small line of recent values, in the tone's color."""

    def __init__(self, width: int = 64, height: int = 28) -> None:
        super().__init__()
        self.values: list[float] = []
        self.tone = "neutral"
        self.setFixedSize(width, height)

    def set_values(self, values: Sequence[float], tone: str = "neutral") -> None:
        self.values = [v for v in values if math.isfinite(v)]
        self.tone = tone
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        if len(self.values) < 2:
            return
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        low, high = min(self.values), max(self.values)
        span = high - low or 1.0
        w, h = self.width(), self.height()
        path = QPainterPath()
        for index, value in enumerate(self.values):
            x = index / (len(self.values) - 1) * w
            point = QPointF(x, h - 2 - (value - low) / span * (h - 4))
            if index:
                path.lineTo(point)
            else:
                path.moveTo(point)
        color = t.text if self.tone == "neutral" else tone_color(t, self.tone)
        painter.setPen(QPen(QColor(color), 1.4))
        painter.drawPath(path)
        painter.end()


class KpiCell(QWidget):
    """One figure of the strip: caption, value (with a sparkline) and a small line below."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        self.caption = v2_label(caption, "kpi_caption")
        layout.addWidget(self.caption)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.value = v2_label("\u2014", "kpi_value")
        self.value.setProperty("tone", "")
        self.value.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.spark = Spark()
        self.spark.setVisible(False)
        row.addWidget(self.value)
        row.addStretch(1)
        row.addWidget(self.spark, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(row)
        self.sub = v2_label("", "kpi_sub")
        layout.addWidget(self.sub)

    def set_text(self, text: str, tone: float | None = None) -> None:
        """The value; a signed `tone` colors it (the sign keeps the meaning without color)."""
        name = ""
        if tone is not None and math.isfinite(tone) and tone != 0:
            name = "profit" if tone > 0 else "loss"
        self.value.setText(text)
        set_tone(self.value, name)

    def set_spark(self, values: Sequence[float], tone: str = "neutral") -> None:
        self.spark.set_values(values, tone)
        self.spark.setVisible(len(self.spark.values) >= 2)


class KpiStrip(QFrame):
    """The design's `kpis`: one outlined box, figures split by hairlines."""

    def __init__(self, captions: Sequence[str]) -> None:
        super().__init__()
        self.setProperty("v2", "kpis")
        row = QHBoxLayout(self)
        row.setContentsMargins(1, 1, 1, 1)
        row.setSpacing(0)
        self.cells: list[KpiCell] = []
        for index, caption in enumerate(captions):
            if index:
                row.addWidget(hairline(vertical=True))
            cell = KpiCell(caption)
            self.cells.append(cell)
            row.addWidget(cell, 1)


def meter_tone(share: float) -> str:
    """Ink while calm, amber from 75% used, coral at the limit."""
    if share >= 1.0:
        return "loss"
    if share >= WARN_SHARE:
        return "warning"
    return "ink"


class Meter(Painted):
    """The design's limit bar: a hairline box filled from the reading side."""

    def __init__(self) -> None:
        super().__init__()
        self.share = 0.0
        self.setFixedHeight(8)

    def set_share(self, share: float) -> None:
        self.share = max(0.0, min(1.0, share)) if math.isfinite(share) else 0.0
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        box = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(t.border), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 2, 2)
        tone = meter_tone(self.share)
        width = (self.width() - 2) * self.share
        if width > 0:
            rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            left = self.width() - 1 - width if rtl else 1.0
            fill = QColor(tone_color(t, tone))
            painter.fillRect(QRectF(left, 1, width, self.height() - 2), fill)
        painter.end()


def drawdowns(values: Sequence[float]) -> list[float]:
    """How far each point is below the highest point before it (0 or negative)."""
    peak = -math.inf
    found: list[float] = []
    for value in values:
        peak = max(peak, value)
        found.append(value - peak)
    return found


def hatch_brush(color: str) -> QBrush:
    """Diagonal stripes in `color`, like the design's hatched losses."""
    brush = QBrush(QColor(color), Qt.BrushStyle.BDiagPattern)
    return brush


class EquityChart(Painted):
    """The equity line with three guide lines; the drawdown band below, hatched in coral."""

    def __init__(self, height: int = 200, band: int = 64) -> None:
        super().__init__()
        self.values: list[float] = []
        self.chart_height = height
        self.band = band
        self.setMinimumHeight(height + band + 24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: Sequence[float]) -> None:
        self.values = [v for v in values if math.isfinite(v)]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.chart_height)
        painter.setPen(QPen(QColor(t.border), 1))
        for share in (0.25, 0.5, 0.75):
            painter.drawLine(QPointF(0, h * share), QPointF(w, h * share))
        values = self.values
        top = h + 6
        cap = caption_font()
        painter.setFont(cap)
        metrics = QFontMetrics(cap)
        painter.setPen(QColor(t.text_secondary))
        label_x = w - metrics.horizontalAdvance("DRAWDOWN") if self._rtl() else 0.0
        painter.drawText(QPointF(label_x, top + metrics.ascent()), "DRAWDOWN")
        band_top = top + metrics.height() + 2
        painter.setPen(QPen(QColor(t.border_strong), 1))
        painter.drawLine(QPointF(0, band_top), QPointF(w, band_top))
        if len(values) < 2:
            painter.end()
            return
        low, high = min(values), max(values)
        span = high - low or 1.0

        def x(index: int) -> float:
            return index / (len(values) - 1) * w

        line = QPainterPath()
        for index, value in enumerate(values):
            point = QPointF(x(index), 8 + (1 - (value - low) / span) * (h - 16))
            if index:
                line.lineTo(point)
            else:
                line.moveTo(point)
        painter.setPen(QPen(QColor(t.text), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)
        falls = drawdowns(values)
        deepest = min(falls) or -1.0
        band = QPainterPath(QPointF(0, band_top))
        for index, fall in enumerate(falls):
            band.lineTo(QPointF(x(index), band_top + fall / deepest * (self.band - 6)))
        band.lineTo(QPointF(w, band_top))
        band.closeSubpath()
        painter.setBrush(hatch_brush(t.loss))
        painter.setPen(QPen(QColor(t.loss), 1))
        painter.drawPath(band)
        painter.end()

    def _rtl(self) -> bool:
        return self.layoutDirection() == Qt.LayoutDirection.RightToLeft


class Pipeline(Painted):
    """A signal's five decision steps: a tick (passed), a cross (stopped here) or dashed."""

    def __init__(self, steps: Sequence[int] = (), tips: Sequence[str] = ()) -> None:
        super().__init__()
        self.steps = list(steps)
        self.setToolTip("\n".join(tips))
        self.setFixedHeight(24)
        self.setMinimumWidth(5 * 22 + 4 * 22)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        middle = self.height() / 2
        for index, step in enumerate(self.steps):
            offset = index * 44
            left = self.width() - offset - 22 if rtl else float(offset)
            if index:
                start = left + 22 if rtl else left - 22
                dashed = step == 0
                pen = QPen(QColor(t.border_strong), 1)
                if dashed:
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(start, middle), QPointF(start + 22, middle))
            circle = QRectF(left + 0.5, middle - 10.5, 21, 21)
            pen = QPen(QColor(t.loss if step < 0 else t.border_strong), 1)
            if step == 0:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            if step < 0:
                painter.setBrush(QColor(t.loss_soft))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle)
            if step:
                painter.setPen(QColor(t.loss if step < 0 else t.text))
                font = QFont(self.font())
                font.setPointSizeF(8.5)
                painter.setFont(font)
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, CHECK if step > 0 else CROSS)
        painter.end()


@dataclass(frozen=True)
class SessionBand:
    name: str
    start_hour: float
    end_hour: float
    active: bool


def session_bands(now: float) -> list[SessionBand]:
    """Today's three sessions in UTC hours (summer time included) and which are open now."""
    day = datetime.fromtimestamp(now, UTC).date()
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
    found: list[SessionBand] = []
    for window in session_windows(day):
        start = (window.start - midnight) / 3600
        end = min(24.0, (window.end - midnight) / 3600)
        name = SESSION_NAMES[window.session]
        found.append(SessionBand(name, start, end, window.contains(now)))
    return found


class SessionsBar(Painted):
    """The three sessions as bars on a 0 to 24 UTC scale; open ones inverted, now in coral."""

    def __init__(self) -> None:
        super().__init__()
        self.bands: list[SessionBand] = []
        self.now_hour = math.nan
        self.setFixedHeight(80)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

    def set_time(self, now: float) -> None:
        self.bands = session_bands(now)
        moment = datetime.fromtimestamp(now, UTC)
        self.now_hour = moment.hour + moment.minute / 60
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        small = QFont(NUMBER_FONT)
        small.setPointSizeF(7.5)
        painter.setFont(small)
        for row, band in enumerate(self.bands):
            left = band.start_hour / 24 * w
            width = (band.end_hour - band.start_hour) / 24 * w
            box = QRectF(left + 0.5, row * 20 + 0.5, width - 1, 16)
            painter.setPen(QPen(QColor(t.border_strong), 1))
            if band.active:
                painter.setBrush(QColor(t.accent))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 2, 2)
            painter.setPen(QColor(t.accent_text if band.active else t.text_secondary))
            painter.drawText(box.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, band.name)
        if math.isfinite(self.now_hour):
            x = self.now_hour / 24 * w
            painter.setPen(QPen(QColor(t.loss), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height() - 14))
        scale = QFont(NUMBER_FONT)
        scale.setPointSizeF(7.9)
        painter.setFont(scale)
        metrics = QFontMetrics(scale)
        painter.setPen(QColor(t.text_secondary))
        for hour in (0, 6, 12, 18, 24):
            text = f"{hour:02d}"
            x = hour / 24 * (w - metrics.horizontalAdvance(text))
            painter.drawText(QPointF(x, self.height() - 2), text)
        painter.end()


class InfoRow(QWidget):
    """A line of a side list: the name at the start, a value or tag at the end, a hairline."""

    def __init__(self, name: str, value: QWidget) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(hairline())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.name = v2_label(name, "row")
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(value)
        self.value = value
        outer.addLayout(row)
        self.setFixedHeight(34)


class Banner(QFrame):
    """The design's `banner`: a line of text in an outlined box with an action at the end."""

    clicked = Signal()

    def __init__(self, text: str, action: str = "", tone: str = "") -> None:
        super().__init__()
        self.setProperty("v2", "banner")
        self.setProperty("tone", tone)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        self.text = v2_label(text, "row", wrap=True)
        row.addWidget(self.text, 1)
        self.button = v2_button(action, "outline")
        self.button.setVisible(bool(action))
        self.button.clicked.connect(self.clicked.emit)
        row.addWidget(self.button)

    def set_text(self, text: str, action: str | None = None) -> None:
        self.text.setText(text)
        if action is not None:
            self.button.setText(action)
            self.button.setVisible(bool(action))


def empty_box(text: str) -> QFrame:
    """The design's `empty`: a dashed box that says why nothing is shown."""
    frame = QFrame()
    frame.setProperty("v2", "empty")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 30, 20, 30)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setObjectName("EmptyText")
    layout.addWidget(label)
    return frame


@dataclass(frozen=True)
class Column:
    title: str
    end: bool = False  # aligned to the far side (numbers in the design)
    stretch: bool = False


@dataclass(frozen=True)
class Cell:
    """One table cell: text (`mono` for figures), or a tag, or a widget made per draw."""

    text: str = ""
    mono: bool = False
    bold: bool = False
    tone: str = ""  # profit, loss, muted
    tag: str = ""  # a tag's tone ("neutral", "warning", ...): the text is drawn as a tag
    widget: Callable[[], QWidget] | None = field(default=None, compare=False)
    key: str = ""  # stands for the widget when rows are compared

    def same(self, other: Cell) -> bool:
        return (self.text, self.mono, self.bold, self.tone, self.tag, self.key) == (
            other.text,
            other.mono,
            other.bold,
            other.tone,
            other.tag,
            other.key,
        )


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    tip: str = ""
    key: str = ""


def _same_rows(first: Sequence[Row], second: Sequence[Row]) -> bool:
    if len(first) != len(second):
        return False
    for a, b in zip(first, second, strict=True):
        if a.tip != b.tip or len(a.cells) != len(b.cells):
            return False
        if not all(x.same(y) for x, y in zip(a.cells, b.cells, strict=True)):
            return False
    return True


class DesignTable(QWidget):
    """The design's table, ten rows a page, with the empty text in a dashed box."""

    row_clicked = Signal(str)

    def __init__(
        self,
        columns: Sequence[Column],
        empty: str = "",
        page_size: int = PAGE_SIZE,
    ) -> None:
        super().__init__()
        self.empty_text = empty
        self.columns = list(columns)
        self.page_size = max(1, page_size)
        self.page = 0
        self.rows: list[Row] = []
        self._shown: list[Row] = []
        self.tokens: ThemeTokens = DARK
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.table = QTableWidget(0, len(self.columns))
        self.table.setProperty("v2", "table")
        self.table.setHorizontalHeaderLabels([c.title for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setWordWrap(False)
        self.table.setMouseTracking(True)
        header = self.table.horizontalHeader()
        header.setFixedHeight(HEADER_HEIGHT)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        stretch = next((i for i, c in enumerate(self.columns) if c.stretch), len(self.columns) - 1)
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        for index, column in enumerate(self.columns):
            item = self.table.horizontalHeaderItem(index)
            if item is not None:
                align = END if column.end else START
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        self.table.cellClicked.connect(self._clicked)
        layout.addWidget(self.table)
        self.empty = empty_box(empty)
        self.empty.setVisible(False)
        layout.addWidget(self.empty)
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self.info = v2_label("", "cap")
        self.previous_button = v2_button("\u2039", "ghost")
        self.previous_button.setAccessibleName("Previous page")
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button = v2_button("\u203a", "ghost")
        self.next_button.setAccessibleName("Next page")
        self.next_button.clicked.connect(self.next_page)
        pager.addWidget(self.info)
        pager.addStretch(1)
        pager.addWidget(self.previous_button)
        pager.addWidget(self.next_button)
        self.pager = QWidget()
        self.pager.setLayout(pager)
        layout.addWidget(self.pager)
        self._draw()

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.page_size))

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self._shown = []
        self._draw()

    def set_rows(self, rows: Sequence[Row]) -> None:
        self.rows = list(rows)
        self.page = min(self.page, self.pages - 1)
        self._draw()

    def show_page(self, page: int) -> None:
        self.page = max(0, min(page, self.pages - 1))
        self._draw()

    def next_page(self) -> None:
        self.show_page(self.page + 1)

    def previous_page(self) -> None:
        self.show_page(self.page - 1)

    def visible(self) -> list[Row]:
        start = self.page * self.page_size
        return self.rows[start : start + self.page_size]

    def text(self, row: int, column: int) -> str:
        """The text of a visible cell (tests and screen readers)."""
        rows = self.visible()
        if row >= len(rows) or column >= len(rows[row].cells):
            return ""
        return rows[row].cells[column].text

    def _clicked(self, row: int, _column: int) -> None:
        rows = self.visible()
        if row < len(rows):
            self.row_clicked.emit(rows[row].key)

    def _draw(self) -> None:
        rows = self.visible()
        if not _same_rows(rows, self._shown):
            self._shown = rows
            self._fill(rows)
        empty = self.total == 0
        self.table.setVisible(not empty)
        self.empty.setVisible(empty and bool(self.empty_text))
        many = self.pages > 1
        self.pager.setVisible(many)
        start = self.page * self.page_size + 1
        end = min(self.total, start + self.page_size - 1)
        self.info.setText(f"{start}\u2013{end} / {self.total}")
        self.previous_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page < self.pages - 1)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        height = HEADER_HEIGHT + max(1, len(rows)) * ROW_HEIGHT + 4
        self.table.setFixedHeight(height)

    def _fill(self, rows: Sequence[Row]) -> None:
        table = self.table
        table.clearContents()
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, column in enumerate(self.columns):
                cell = row.cells[c] if c < len(row.cells) else Cell()
                widget = self._widget(cell, column)
                if widget is not None:
                    table.setCellWidget(r, c, widget)
                    item = QTableWidgetItem("")
                else:
                    item = QTableWidgetItem(cell.text)
                    font = QFont(NUMBER_FONT) if cell.mono else QFont(table.font())
                    font.setPointSizeF(9.75)
                    if cell.bold:
                        font.setWeight(QFont.Weight.DemiBold)
                    item.setFont(font)
                    color = self._color(cell.tone)
                    if color:
                        item.setForeground(QColor(color))
                align = END if column.end else START
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(row.tip or cell.text)
                table.setItem(r, c, item)

    def _color(self, tone: str) -> str:
        t = self.tokens
        return {"profit": t.profit, "loss": t.loss, "muted": t.text_secondary}.get(tone, "")

    def _widget(self, cell: Cell, column: Column) -> QWidget | None:
        inner: QWidget | None = None
        if cell.widget is not None:
            inner = cell.widget()
        elif cell.tag:
            inner = Tag(cell.text, cell.tag, mono=cell.mono)
        if inner is None:
            return None
        apply = getattr(inner, "apply_tokens", None)
        if callable(apply):
            apply(self.tokens)
        holder = QWidget()
        box = QHBoxLayout(holder)
        box.setContentsMargins(10, 0, 10, 0)
        if column.end:
            box.addStretch(1)
        box.addWidget(inner)
        if not column.end:
            box.addStretch(1)
        return holder
