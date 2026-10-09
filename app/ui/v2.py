"""The building blocks of the No Curve v2 Dashboard, drawn like the owner's design.

Every block of the design is made of the same few parts, so they live here once:

- `Section`: the design's `sec`, a bold title, its extras, then a hairline to the end.
- `KpiStrip`: one outlined box of four figures split by hairlines, each with a sparkline.
- `Meter`: the limit bar on a 10% scale, a 5 px fill and the limit mark at the end.
- `EquityChart`: the equity line over three guide lines and the drawdown band below.
- `Pipeline`: the five decision steps of a signal (passed, stopped here, not reached).
- `SessionsBar`: Tokyo, London and New York on a 24 hour UTC scale with a line at now.
- `DesignTable`: the design's rows (a grid of fixed and shared widths, 52 or 56 px rows,
  hairlines) with tags and widgets in cells, ten rows a page.
- `StatusRow`, `Checklist`, `Icon`, `IconText`, `Tag`, `Banner`, `InfoRow`: the small pieces.

All of them paint with the theme's tokens (`apply_tokens`), so both themes work. Numbers,
symbols, charts and time scales stay left to right in Persian, as in the design.
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
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
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
from app.ui.shell import CAPTION_PT, SMALL_PT, Painted, Segmented, caption_font, tone_color
from app.ui.theme import DARK, NUMBER_FONT, ThemeTokens, mix, number_family

ROW_HEIGHT = 44
HEADER_HEIGHT = 34
PAGE_SIZE = 10
WARN_SHARE = 0.75
SECTION_GAP = 16  # `.sec{margin-bottom:16px}`
STEP_SIZE = 22  # a decision circle
PIPELINE_WIDTH = 330
SESSIONS_HEIGHT = 78
CHECK = "\u2713"
END = Qt.AlignmentFlag.AlignTrailing
START = Qt.AlignmentFlag.AlignLeading
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
LTR = Qt.LayoutDirection.LeftToRight
RTL = Qt.LayoutDirection.RightToLeft
CROSS = "\u2715"
SESSION_NAMES: dict[Session, str] = {
    Session.ASIA: "TOKYO",
    Session.LONDON: "LONDON",
    Session.NEW_YORK: "NEW YORK",
}


def v2_qss(tokens: ThemeTokens) -> str:
    """The stylesheet of the v2 pieces, added after the frame's rules."""
    t = tokens
    locked = mix(t.text_secondary, t.bg, 0.45)  # the design's `opacity:.55`
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
QLabel[v2="kpi_sub"] {{
    color: {t.text_secondary};
    font-size: 9pt;
}}
QLabel[v2="kpi_value"] {{
    color: {t.text};
    font-family: {number_family()};
    font-size: 20.25pt;
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
QLabel[v2="row_muted"] {{
    color: {t.text_secondary};
    font-size: 9.75pt;
}}
QLabel[v2="row_num"] {{
    font-family: {number_family()};
    font-size: 9.75pt;
}}
QLabel[v2="row_num"][bold="true"] {{
    font-weight: 600;
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
QFrame#Segmented[mono="true"] QPushButton {{
    font-family: {number_family()};
    font-size: 9pt;
    min-height: 30px;
    max-height: 30px;
}}
QFrame#Segmented[small="true"] QPushButton {{
    font-family: {number_family()};
    font-size: {CAPTION_PT:g}pt;
    min-height: 24px;
    max-height: 24px;
}}
QFrame#Segmented QPushButton[locked="true"] {{
    color: {locked};
}}
QFrame#Segmented QPushButton[locked="true"]:checked {{
    color: {t.accent_text};
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
    padding: 0px 5px;
}}
QTableWidget[v2="table"]::item:hover {{
    background-color: {t.hover};
}}
QTableWidget[v2="table"] QHeaderView::section {{
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-bottom: 1px solid {t.border_strong};
    padding: 0px 5px;
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


def keep_room(widget: QWidget) -> None:
    """A hidden widget keeps its room (the KPI arrow and sparkline)."""
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


# Icons -------------------------------------------------------------------------------------
# The design's line icons on a 24 by 24 grid (round caps and joins), as painter paths.
def _lines(*points: tuple[tuple[float, float], ...]) -> QPainterPath:
    path = QPainterPath()
    for line in points:
        path.moveTo(*line[0])
        for point in line[1:]:
            path.lineTo(*point)
    return path


def _lock() -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(5, 11, 14, 9), 2, 2)
    path.moveTo(8, 11)
    path.lineTo(8, 8)
    path.arcTo(QRectF(8, 4, 8, 8), 180, -180)
    path.lineTo(16, 11)
    return path


ICONS: dict[str, Callable[[], QPainterPath]] = {
    "up": lambda: _lines(((12, 19), (12, 5)), ((6, 11), (12, 5), (18, 11))),
    "down": lambda: _lines(((12, 5), (12, 19)), ((6, 13), (12, 19), (18, 13))),
    "check": lambda: _lines(((5, 12.5), (9.5, 17), (19, 7))),
    "cross": lambda: _lines(((6, 6), (18, 18)), ((18, 6), (6, 18))),
    "lock": _lock,
}
ICON_STROKES = {"up": 2.4, "down": 2.4, "check": 2.6, "cross": 2.6, "lock": 2.0}


def draw_icon(
    painter: QPainter,
    name: str,
    box: QRectF,
    color: str,
    stroke: float | None = None,
) -> None:
    """Paint the design's icon `name` into `box` (a square) in `color`."""
    make = ICONS.get(name)
    if make is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(box.topLeft())
    scale = box.width() / 24
    painter.scale(scale, scale)
    pen = QPen(QColor(color), stroke if stroke is not None else ICON_STROKES.get(name, 2.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(make())
    painter.restore()


def icon_pixmap(name: str, size: int, color: str) -> QPixmap:
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_icon(painter, name, QRectF(0, 0, size, size), color)
    painter.end()
    return pixmap


def tone_ink(tokens: ThemeTokens, tone: str) -> str:
    """The text color of a tone: profit, loss, warning, muted; else the ink."""
    if tone == "muted":
        return tokens.text_secondary
    if tone in ("profit", "loss", "warning"):
        return tone_color(tokens, tone)
    return tokens.text


class Icon(Painted):
    """One of the design's line icons, `size` px, in a tone's color."""

    def __init__(self, name: str, size: int = 13, tone: str = "") -> None:
        super().__init__()
        self.name = name
        self.tone = tone
        self.setFixedSize(size, size)

    def set(self, name: str, tone: str) -> None:
        self.name = name
        self.tone = tone
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        box = QRectF(0, 0, self.width(), self.height())
        draw_icon(painter, self.name, box, tone_ink(self.tokens, self.tone))
        painter.end()


class IconText(QWidget):
    """A cell of icon and text: "\u2191 Buy", "+$12.40" with its arrow, a stop with its lock."""

    def __init__(
        self,
        icon: str,
        text: str,
        *,
        tone: str = "",
        mono: bool = False,
        bold: bool = False,
        icon_after: bool = False,
        icon_tone: str | None = None,
    ) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6 if not mono or icon_after else 5)
        size = 12 if icon == "lock" else 13
        self.icon = Icon(icon, size, tone if icon_tone is None else icon_tone)
        self.label = v2_label(text, "row_num" if mono else "row")
        self.label.setProperty("tone", tone)
        self.label.setProperty("bold", "true" if bold else "false")
        if mono:
            self.setLayoutDirection(LTR)
        if icon_after:
            row.addWidget(self.label)
            row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self.label)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.icon.apply_tokens(tokens)


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
        font.setPointSizeF(SMALL_PT)
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
    """The design's `sec`: the bold title, its extras, a hairline to the end, 16 px below.

    With a `link` the title row opens another page (the design draws no button there).
    """

    clicked = Signal()

    def __init__(self, title: str, caption: QWidget | None = None, link: str = "") -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, SECTION_GAP)
        row.setSpacing(10)
        self.title = v2_label(title, "sec")
        row.addWidget(self.title)
        if caption is not None:
            row.addWidget(caption)
        self.tools = QHBoxLayout()  # not `actions`: QWidget.actions() is a Qt method
        self.tools.setSpacing(6)
        row.addLayout(self.tools)
        line = hairline()
        line.setMinimumWidth(20)
        row.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)
        self.link = link
        if link:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(link)

    def add(self, widget: QWidget) -> None:
        self.tools.addWidget(widget)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt name)
        if self.link and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ModeSegment(Segmented):
    """The trading mode: PAPER, SEMI-AUTO, AUTO (a lock while it may not be used), ANALYSIS."""

    def __init__(self, labels: Sequence[str], locked_index: int) -> None:
        super().__init__(labels)
        self.setProperty("mono", "true")
        self.locked_index = locked_index
        self.locked = True
        self.tokens: ThemeTokens = DARK
        for button in self.buttons:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_locked(True)

    def set_locked(self, locked: bool) -> None:
        button = self.buttons[self.locked_index]
        if self.locked != locked or button.property("locked") is None:
            self.locked = locked
            button.setProperty("locked", "true" if locked else "false")
            style = button.style()
            style.unpolish(button)
            style.polish(button)
        self._paint_icon()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self._paint_icon()

    def _paint_icon(self) -> None:
        button = self.buttons[self.locked_index]
        if not self.locked:
            button.setIcon(QIcon())
            return
        t = self.tokens
        color = t.accent_text if button.isChecked() else mix(t.text_secondary, t.bg, 0.45)
        button.setIcon(QIcon(icon_pixmap("lock", 12, color)))
        button.setIconSize(QSize(12, 12))


class Spark(Painted):
    """A small line of recent values (the design's 110 by 30, 1.5 px), in the tone's color."""

    def __init__(self, width: int = 110, height: int = 30) -> None:
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
        w, h = self.width() - 4, self.height() - 4
        path = QPainterPath()
        for index, value in enumerate(self.values):
            x = 2 + index / (len(self.values) - 1) * w
            point = QPointF(x, 2 + (1 - (value - low) / span) * h)
            if index:
                path.lineTo(point)
            else:
                path.moveTo(point)
        color = t.text if self.tone == "neutral" else tone_color(t, self.tone)
        pen = QPen(QColor(color), 1.5)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class KpiCell(QWidget):
    """One figure of the strip: the cap caption, the value with its arrow and sparkline, and
    a small line below (padding 14/18, a 27 px value)."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(0)
        self.caption = v2_label(caption, "cap")
        layout.addWidget(self.caption)
        layout.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        figure = QWidget()
        figure.setLayoutDirection(LTR)
        box = QHBoxLayout(figure)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        self.arrow = Icon("up", 16)
        keep_room(self.arrow)
        self.arrow.setVisible(False)
        self.value = v2_label("\u2014", "kpi_value")
        self.value.setProperty("tone", "")
        box.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(self.value)
        self.spark = Spark()
        keep_room(self.spark)
        self.spark.setVisible(False)
        row.addWidget(figure, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        row.addWidget(self.spark, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(row)
        layout.addSpacing(4)
        self.sub = v2_label("", "kpi_sub")
        layout.addWidget(self.sub)

    def set_text(
        self,
        text: str,
        tone: float | None = None,
        *,
        color: str | None = None,
        arrow: bool = False,
    ) -> None:
        """The value; a signed `tone` colors it (the sign keeps the meaning without color).

        `color` names the tone outright ("loss" for a drawdown); `arrow` draws the design's
        up or down arrow before a colored value.
        """
        name = ""
        if tone is not None and math.isfinite(tone) and tone != 0:
            name = "profit" if tone > 0 else "loss"
        if color is not None:
            name = color
        self.value.setText(text)
        set_tone(self.value, name)
        self.arrow.set("down" if name == "loss" else "up", name)
        self.arrow.setVisible(arrow and bool(name))

    def set_spark(self, values: Sequence[float], tone: str = "neutral") -> None:
        self.spark.set_values(values, tone)
        self.spark.setVisible(len(self.spark.values) >= 2)


class KpiStrip(QFrame):
    """The design's figures: one outlined box, figures split by hairlines."""

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
    """The design's limit bar: a scale line every 10%, a strong base line, a 5 px fill from
    the left and a 2 px red mark at the limit (left to right in Persian too)."""

    def __init__(self) -> None:
        super().__init__()
        self.share = 0.0
        self.setFixedHeight(13)
        self.setLayoutDirection(LTR)

    def set_share(self, share: float) -> None:
        self.share = max(0.0, min(1.0, share)) if math.isfinite(share) else 0.0
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        w = float(self.width())
        inner = float(self.height() - 1)
        strong = QColor(t.border_strong)
        for step in range(10):
            painter.fillRect(QRectF(math.floor(w * step / 10), 0, 1, inner), strong)
        painter.fillRect(QRectF(0, inner, w, 1), strong)
        width = w * self.share
        if width > 0:
            fill = QColor(tone_color(t, meter_tone(self.share)))
            painter.fillRect(QRectF(0, inner - 5, width, 5), fill)
        painter.fillRect(QRectF(w - 2, 0, 2, inner), QColor(t.loss))
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
    """The equity line over three guide lines (170 px), the DRAWDOWN caption and the 64 px
    band below it: the fall from the peak, filled in coral at 22%."""

    def __init__(self, height: int = 170, band: int = 64) -> None:
        super().__init__()
        self.values: list[float] = []
        self.chart_height = height
        self.band = band
        self.caption_at_end = False  # Persian: the caption sits on the right, like the design
        metrics = QFontMetrics(caption_font())
        self.setMinimumHeight(height + 6 + metrics.height() + 2 + band)
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
            y = round(h * share) + 0.5
            painter.drawLine(QPointF(0, y), QPointF(w, y))
        cap = caption_font()
        painter.setFont(cap)
        metrics = QFontMetrics(cap)
        painter.setPen(QColor(t.text_secondary))
        top = h + 6
        label_x = w - metrics.horizontalAdvance("DRAWDOWN") if self.caption_at_end else 0.0
        painter.drawText(QPointF(label_x, top + metrics.ascent()), "DRAWDOWN")
        band_top = top + metrics.height() + 2
        painter.setPen(QPen(QColor(t.border_strong), 1))
        painter.drawLine(QPointF(0, band_top + 2.5), QPointF(w, band_top + 2.5))
        values = self.values
        if len(values) < 2:
            painter.end()
            return
        low, high = min(values), max(values)
        span = high - low or 1.0

        def x(index: int, pad: float) -> float:
            return pad + index / (len(values) - 1) * (w - 2 * pad)

        line = QPainterPath()
        last = QPointF()
        for index, value in enumerate(values):
            last = QPointF(x(index, 8), 20 + (1 - (value - low) / span) * (h - 40))
            if index:
                line.lineTo(last)
            else:
                line.moveTo(last)
        pen = QPen(QColor(t.text), 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.text))
        painter.drawEllipse(last, 3.5, 3.5)
        falls = drawdowns(values)
        deepest = min(falls) or -1.0
        zero = band_top + 6
        depth = self.band - 14
        edge = QPainterPath()
        for index, fall in enumerate(falls):
            point = QPointF(x(index, 4), zero + fall / deepest * depth)
            if index:
                edge.lineTo(point)
            else:
                edge.moveTo(point)
        area = QPainterPath(edge)
        area.lineTo(QPointF(w - 4, band_top))
        area.lineTo(QPointF(4, band_top))
        area.closeSubpath()
        fill = QColor(t.loss)
        fill.setAlphaF(0.22)
        painter.setBrush(fill)
        painter.drawPath(area)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(t.loss), 1.4))
        painter.drawPath(edge)
        painter.end()


class Pipeline(Painted):
    """A signal's five decision steps (330 px): a green tick (passed), a red cross on coral
    (stopped here) or a dashed circle (not reached), each named below; the line into a
    reached step is green, into one not reached dashed."""

    def __init__(
        self,
        steps: Sequence[int] = (),
        tips: Sequence[str] = (),
        labels: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self.steps = list(steps)
        self.labels = list(labels) if labels else [""] * len(self.steps)
        self.setToolTip("\n".join(tips))
        self.setFixedSize(PIPELINE_WIDTH, 42)

    def _label_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(CAPTION_PT)
        return font

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self._label_font()
        metrics = QFontMetrics(font)
        count = len(self.steps)
        if not count:
            painter.end()
            return
        widths = [
            max(float(STEP_SIZE), float(metrics.horizontalAdvance(label)))
            for label in self.labels[:count]
        ]
        widths += [float(STEP_SIZE)] * (count - len(widths))
        w = float(self.width())
        gaps = 6.0 * (2 * count - 2)
        link = max(8.0, (w - sum(widths) - gaps) / max(1, count - 1))
        rtl = self.layoutDirection() == RTL

        def place(left: float, width: float) -> float:
            return w - left - width if rtl else left

        middle = STEP_SIZE / 2
        left = 0.0
        painter.setFont(font)
        for index, step in enumerate(self.steps):
            if index:
                left += 6
                pen = QPen(QColor(t.profit if step else t.border_strong), 1)
                if not step:
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                start = place(left, link)
                painter.drawLine(QPointF(start, middle + 0.5), QPointF(start + link, middle + 0.5))
                left += link + 6
            width = widths[index]
            column = place(left, width)
            circle = QRectF(column + (width - STEP_SIZE) / 2 + 0.5, 0.5, 21, 21)
            if step > 0:
                painter.setPen(QPen(QColor(t.profit), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            elif step < 0:
                painter.setPen(QPen(QColor(t.loss), 1))
                painter.setBrush(QColor(t.loss_soft))
            else:
                pen = QPen(QColor(t.border_strong), 1)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle)
            if step:
                mark = QRectF(circle.center().x() - 6, circle.center().y() - 6, 12, 12)
                name = "check" if step > 0 else "cross"
                draw_icon(painter, name, mark, t.profit if step > 0 else t.loss)
            if index < len(self.labels):
                painter.setPen(QColor(t.text_secondary))
                text = QRectF(column, STEP_SIZE + 3, width, metrics.height())
                painter.drawText(text, Qt.AlignmentFlag.AlignCenter, self.labels[index])
            left += width
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
    """The three sessions on a 0 to 24 UTC scale (78 px): open ones in ink, a line at now and
    the hours every three below, over a strong base line."""

    def __init__(self) -> None:
        super().__init__()
        self.bands: list[SessionBand] = []
        self.now_hour = math.nan
        self.setFixedHeight(SESSIONS_HEIGHT + 1)
        self.setLayoutDirection(LTR)

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
        small.setPointSizeF(7.5)  # 10 px
        painter.setFont(small)
        for row, band in enumerate(self.bands):
            left = band.start_hour / 24 * w
            width = (band.end_hour - band.start_hour) / 24 * w
            box = QRectF(left + 0.5, 2 + row * 19 + 0.5, max(1.0, width - 1), 14)
            if band.active:
                painter.setPen(QPen(QColor(t.accent), 1))
                painter.setBrush(QColor(t.accent))
            else:
                painter.setPen(QPen(QColor(t.border_strong), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor(t.accent_text if band.active else t.text_secondary))
            text_box = box.adjusted(6, 0, -2, 0)
            painter.drawText(text_box, Qt.AlignmentFlag.AlignVCenter | LEFT, band.name)
        if math.isfinite(self.now_hour):
            x = self.now_hour / 24 * w
            painter.fillRect(QRectF(x, 0, 1, SESSIONS_HEIGHT - 14), QColor(t.accent))
        scale = caption_font()
        painter.setFont(scale)
        metrics = QFontMetrics(scale)
        painter.setPen(QColor(t.text_secondary))
        for hour in range(0, 25, 3):
            text = f"{hour:02d}"
            size = metrics.horizontalAdvance(text)
            x = min(max(0.0, hour / 24 * w - size / 2), w - size)
            painter.drawText(QPointF(x, SESSIONS_HEIGHT - metrics.descent()), text)
        painter.fillRect(QRectF(0, SESSIONS_HEIGHT, w, 1), QColor(t.border_strong))
        painter.end()


class Led(Painted):
    """The design's `led`: a 9 px light, filled for OK and errors, a ring for a warning."""

    def __init__(self, tone: str = "neutral") -> None:
        super().__init__()
        self.tone = tone
        self.setFixedSize(9, 9)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.tone in ("profit", "loss"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tone_color(t, self.tone)))
            painter.drawEllipse(QRectF(0, 0, 9, 9))
        else:
            color = t.warning if self.tone == "warning" else t.border_strong
            painter.setPen(QPen(QColor(color), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(1, 1, 7, 7))
        painter.end()


class StatusRow(QWidget):
    """A line of System health: the light, the name and the status tag, a hairline above."""

    def __init__(self, name: str, tag: Tag) -> None:
        super().__init__()
        self.tokens: ThemeTokens = DARK
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 9, 0, 9)
        row.setSpacing(10)
        self.led = Led(tag.tone)
        self.name = v2_label(name, "row")
        row.addWidget(self.led, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.name, 1)
        row.addWidget(tag)
        self.tag = tag
        self.setFixedHeight(39)

    def set_tone(self, tone: str) -> None:
        self.led.set_tone(tone)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.fillRect(QRectF(0, 0, self.width(), 1), QColor(self.tokens.border))
        painter.end()


@dataclass(frozen=True)
class CheckLine:
    text: str
    state: str  # done, open, locked
    tip: str = ""


class Checklist(Painted):
    """The design's Go-Live list: a green dot with a tick for a step done, an ink ring for a
    step still open, a muted lock for the approval that waits; a line joins the dots."""

    ROW = 32

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[CheckLine] = []
        self.setFixedHeight(self.ROW)

    def set_lines(self, lines: Sequence[CheckLine]) -> None:
        self.lines = list(lines)
        self.setFixedHeight(max(1, len(self.lines)) * self.ROW)
        tips = [f"{line.text}: {line.tip}" if line.tip else line.text for line in self.lines]
        self.setToolTip("\n\n".join(tips))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        rtl = self.layoutDirection() == RTL
        dot_x = w - 14 if rtl else 0.0
        font = QFont(self.font())
        font.setPointSizeF(9.75)
        painter.setFont(font)
        count = len(self.lines)
        for index in range(count - 1):
            top = index * self.ROW
            painter.fillRect(
                QRectF(dot_x + 6, top + 22, 1, self.ROW - 12),
                QColor(t.border_strong),
            )
        for index, line in enumerate(self.lines):
            top = index * self.ROW
            dot = QRectF(dot_x, top + 9, 14, 14)
            if line.state == "done":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.profit))
                painter.drawEllipse(dot)
                mark = QRectF(dot.x() + 2, dot.y() + 2, 10, 10)
                draw_icon(painter, "check", mark, t.bg, 3.2)
            elif line.state == "open":
                painter.setPen(QPen(QColor(t.text), 2))
                painter.setBrush(QColor(t.bg))
                painter.drawEllipse(dot.adjusted(1, 1, -1, -1))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.bg))
                painter.drawRect(dot)
                draw_icon(painter, "lock", dot, t.text_secondary, 1.8)
            painter.setPen(QColor(t.text_secondary if line.state == "locked" else t.text))
            text = QRectF(0, top, w - 26, self.ROW) if rtl else QRectF(26, top, w - 26, self.ROW)
            align = Qt.AlignmentFlag.AlignVCenter
            align |= Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
            painter.drawText(text, align | Qt.AlignmentFlag.AlignAbsolute, line.text)
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
    end: bool = False  # aligned to the far side (the result tag in the design)
    stretch: bool = False
    width: int = 0  # a fixed width in px (the design's grid)
    share: float = 0.0  # a share of the room left (the design's `fr`)


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


def grid_widths(columns: Sequence[Column], room: float, gap: int) -> list[int]:
    """The design's CSS grid: fixed columns keep their px, `fr` columns share the rest; each
    column also takes its gap (cells pad 5 px a side)."""
    fixed = sum(c.width for c in columns if not c.share) + gap * len(columns)
    shares = sum(c.share for c in columns if c.share)
    left = max(0.0, room - fixed)
    found: list[int] = []
    for column in columns:
        if column.share and shares:
            found.append(max(40, int(left * column.share / shares)) + gap)
        else:
            found.append(column.width + gap)
    return found


class DesignTable(QWidget):
    """The design's rows, ten a page, with the empty text in a dashed box.

    With column widths it is the design's grid: numbers and the capital headers are left
    aligned (they are left to right in Persian too), words follow the reading side.
    """

    row_clicked = Signal(str)

    def __init__(
        self,
        columns: Sequence[Column],
        empty: str = "",
        page_size: int = PAGE_SIZE,
        *,
        row_height: int = ROW_HEIGHT,
        header: bool = True,
        gap: int = 10,
    ) -> None:
        super().__init__()
        self.empty_text = empty
        self.columns = list(columns)
        self.page_size = max(1, page_size)
        self.row_height = row_height
        self.header_height = HEADER_HEIGHT if header else 0
        self.gap = gap
        self.grid = any(c.width or c.share for c in self.columns)
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
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setWordWrap(False)
        self.table.setMouseTracking(True)
        head = self.table.horizontalHeader()
        head.setVisible(header)
        head.setFixedHeight(HEADER_HEIGHT)
        head.setHighlightSections(False)
        if self.grid:
            head.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            head.setMinimumSectionSize(10)
        else:
            head.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            last = len(self.columns) - 1
            stretch = next((i for i, c in enumerate(self.columns) if c.stretch), last)
            head.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        for index, column in enumerate(self.columns):
            item = self.table.horizontalHeaderItem(index)
            if item is not None:
                align = LEFT if self.grid else END if column.end else START
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

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if not self.grid:
            return
        room = float(max(0, self.table.viewport().width()))
        for index, width in enumerate(grid_widths(self.columns, room, self.gap)):
            self.table.setColumnWidth(index, width)

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
        self.table.verticalHeader().setDefaultSectionSize(self.row_height)
        height = self.header_height + max(1, len(rows)) * self.row_height + 4
        self.table.setFixedHeight(height)
        self._fit()

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
                item.setTextAlignment(self._align(cell, column) | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(row.tip or cell.text)
                table.setItem(r, c, item)

    def _align(self, cell: Cell, column: Column) -> Qt.AlignmentFlag:
        if self.grid:
            if column.end:
                return END
            return LEFT if cell.mono else START
        return END if column.end else START

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
        pad = 5 if self.grid else 10
        box.setContentsMargins(pad, 0, pad, 0)
        if self.grid and cell.mono:
            holder.setLayoutDirection(LTR)  # a figure starts on the left, as in the design
        if column.end:
            box.addStretch(1)
        box.addWidget(inner)
        if not column.end:
            box.addStretch(1)
        return holder
