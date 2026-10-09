"""The AI Lab's native charts (docs/NOCURVE_V2.md 20e4), painted with the theme's tokens.

- `LineChart`: one or more lines (solid or dashed), three gridlines, the range at the start
  edge; the equity card (current vs AI) and the Experiments panel.
- `BarChart`: bars from a zero line, green above and red below (or one quiet ink), with an
  optional label under each; the R distribution, the walk-forward windows and the requests
  of the last 7 days.
- `FanChart`: the Monte Carlo paths, the 5 to 95 percent band, the median and the real order.
- `HeatMap`: a grid of two parameters, coloured from loss to profit, the value in each cell.
- `CandleChart`: the last bars of a symbol with the entries (a triangle up for a buy, down for
  a sell; green when the trade won).

Hovering a chart shows the value under the pointer. Every chart draws only what it is given
and says "No data" when it is empty. Charts read left to right in Persian too, as in the
design.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QToolTip, QWidget

from app.analytics.ai_visuals import Candle, Marker
from app.ui.shell import Painted
from app.ui.theme import NUMBER_FONT, ThemeTokens, mix, px

LTR = Qt.LayoutDirection.LeftToRight
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
PAD_LEFT = 46
PAD_RIGHT = 10
PAD_TOP = 10
PAD_BOTTOM = 22
NO_DATA = "No data"


@dataclass(frozen=True)
class Line:
    values: tuple[float, ...]
    name: str = ""
    tone: str = "ink"  # ink, muted, profit, loss, warning
    dashed: bool = False


def tone_of(tokens: ThemeTokens, tone: str) -> str:
    return {
        "ink": tokens.text,
        "muted": tokens.text_secondary,
        "profit": tokens.profit,
        "loss": tokens.loss,
        "warning": tokens.warning,
    }.get(tone, tokens.text)


def number_text(value: float, digits: int = 2) -> str:
    if not math.isfinite(value):
        return "n/a"
    if abs(value) >= 10_000:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _small_font(widget: QWidget, size: float = 10.0) -> QFont:
    font = QFont(widget.font())
    font.setFamily(NUMBER_FONT)
    font.setPointSizeF(px(size))
    return font


class Chart(Painted):
    """The plot area, the gridlines, the range labels and the hover tooltip."""

    def __init__(self, height: int = 180, name: str = "") -> None:
        super().__init__()
        self.setLayoutDirection(LTR)
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.unit = ""
        if name:
            self.setAccessibleName(name)

    def plot(self) -> QRectF:
        return QRectF(
            PAD_LEFT,
            PAD_TOP,
            max(self.width() - PAD_LEFT - PAD_RIGHT, 1),
            max(self.height() - PAD_TOP - PAD_BOTTOM, 1),
        )

    def grid(self, painter: QPainter, low: float, high: float) -> None:
        t = self.tokens
        area = self.plot()
        painter.setPen(QPen(QColor(t.border), 1))
        for share in (0.0, 0.5, 1.0):
            y = area.top() + area.height() * share
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setFont(_small_font(self))
        painter.setPen(QColor(t.text_secondary))
        middle = (high + low) / 2
        for value, y in ((high, area.top()), (middle, area.center().y()), (low, area.bottom())):
            box = QRectF(0, y - 8, PAD_LEFT - 6, 16)
            painter.drawText(box, RIGHT, number_text(value) + self.unit)

    def empty(self, painter: QPainter, text: str = NO_DATA) -> None:
        painter.setPen(QColor(self.tokens.text_secondary))
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, text)

    def hover_text(self, x: float, y: float) -> str:
        return ""

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt name)
        text = self.hover_text(event.position().x(), event.position().y())
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)


def _range(values: Sequence[float], zero: bool = False) -> tuple[float, float]:
    finite = [value for value in values if math.isfinite(value)]
    if zero:
        finite.append(0.0)
    if not finite:
        return 0.0, 1.0
    low, high = min(finite), max(finite)
    if math.isclose(low, high):
        pad = abs(low) * 0.05 or 1.0
        return low - pad, high + pad
    pad = (high - low) * 0.06
    return low - pad, high + pad


class LineChart(Chart):
    def __init__(self, height: int = 180, name: str = "") -> None:
        super().__init__(height, name)
        self.lines: list[Line] = []
        self.zero = False

    def set_lines(self, lines: Sequence[Line], zero: bool = False, unit: str = "") -> None:
        self.lines = [line for line in lines if line.values]
        self.zero = zero
        self.unit = unit
        self.update()

    def _bounds(self) -> tuple[float, float]:
        return _range([value for line in self.lines for value in line.values], self.zero)

    def _point(self, index: int, count: int, value: float, low: float, high: float) -> QPointF:
        area = self.plot()
        x = area.left() + (area.width() * index / (count - 1) if count > 1 else area.width() / 2)
        y = area.bottom() - (value - low) / (high - low) * area.height()
        return QPointF(x, y)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.lines:
            self.empty(painter)
            painter.end()
            return
        low, high = self._bounds()
        self.grid(painter, low, high)
        if self.zero and low < 0 < high:
            y = self._point(0, 2, 0.0, low, high).y()
            painter.setPen(QPen(QColor(self.tokens.border_strong), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(self.plot().left(), y), QPointF(self.plot().right(), y))
        for line in self.lines:
            path = QPainterPath()
            count = len(line.values)
            for index, value in enumerate(line.values):
                point = self._point(index, count, value, low, high)
                if index:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
            pen = QPen(QColor(tone_of(self.tokens, line.tone)), 1.8)
            if line.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.end()

    def hover_text(self, x: float, y: float) -> str:
        area = self.plot()
        if not self.lines or not area.contains(QPointF(x, y)):
            return ""
        parts: list[str] = []
        for line in self.lines:
            count = len(line.values)
            index = round((x - area.left()) / area.width() * (count - 1)) if count > 1 else 0
            index = min(max(index, 0), count - 1)
            label = f"{line.name}: " if line.name else ""
            parts.append(f"{label}{number_text(line.values[index])}{self.unit} (#{index})")
        return "\n".join(parts)


class BarChart(Chart):
    def __init__(self, height: int = 160, name: str = "") -> None:
        super().__init__(height, name)
        self.values: list[float] = []
        self.labels: list[str] = []
        self.tips: list[str] = []
        self.signed = True

    def set_bars(
        self,
        values: Sequence[float],
        labels: Sequence[str] = (),
        *,
        signed: bool = True,
        tips: Sequence[str] = (),
        unit: str = "",
    ) -> None:
        self.values = [float(value) for value in values]
        self.labels = list(labels)
        self.tips = list(tips)
        self.signed = signed
        self.unit = unit
        self.update()

    def _slots(self) -> list[QRectF]:
        area = self.plot()
        count = len(self.values)
        if not count:
            return []
        width = area.width() / count
        return [
            QRectF(area.left() + index * width, area.top(), width, area.height())
            for index in range(count)
        ]

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.values:
            self.empty(painter)
            painter.end()
            return
        low, high = _range(self.values, zero=True)
        if not self.signed:
            low = 0.0
        self.grid(painter, low, high)
        area = self.plot()
        zero_y = area.bottom() - (0.0 - low) / (high - low) * area.height()
        painter.setFont(_small_font(self))
        for index, (slot, value) in enumerate(zip(self._slots(), self.values, strict=True)):
            top = area.bottom() - (value - low) / (high - low) * area.height()
            gap = min(6.0, slot.width() * 0.2)
            width = max(slot.width() - gap, 1.0)
            bar = QRectF(slot.left() + gap / 2, min(top, zero_y), width, abs(zero_y - top))
            if self.signed:
                color = t.profit if value >= 0 else t.loss
            else:
                color = t.text
            painter.fillRect(bar, QColor(color))
            if index < len(self.labels):
                painter.setPen(QColor(t.text_secondary))
                box = QRectF(slot.left(), area.bottom() + 3, slot.width(), PAD_BOTTOM - 4)
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.labels[index])
        painter.end()

    def hover_text(self, x: float, y: float) -> str:
        for index, slot in enumerate(self._slots()):
            if slot.left() <= x < slot.right():
                if index < len(self.tips) and self.tips[index]:
                    return self.tips[index]
                label = self.labels[index] if index < len(self.labels) else f"#{index + 1}"
                return f"{label}: {number_text(self.values[index])}{self.unit}"
        return ""


class FanChart(Chart):
    def __init__(self, height: int = 200, name: str = "") -> None:
        super().__init__(height, name)
        self.unit = "%"
        self.paths: list[tuple[float, ...]] = []
        self.low: tuple[float, ...] = ()
        self.middle: tuple[float, ...] = ()
        self.high: tuple[float, ...] = ()
        self.real: tuple[float, ...] = ()

    def set_fan(
        self,
        paths: Sequence[tuple[float, ...]],
        low: tuple[float, ...],
        middle: tuple[float, ...],
        high: tuple[float, ...],
        real: tuple[float, ...],
    ) -> None:
        self.paths = list(paths)
        self.low, self.middle, self.high, self.real = low, middle, high, real
        self.update()

    def _xy(self, index: int, count: int, value: float, low: float, high: float) -> QPointF:
        area = self.plot()
        x = area.left() + area.width() * index / max(count - 1, 1)
        y = area.bottom() - (value - low) / (high - low) * area.height()
        return QPointF(x, y)

    def _line(self, values: Sequence[float], low: float, high: float) -> QPainterPath:
        path = QPainterPath()
        for index, value in enumerate(values):
            point = self._xy(index, len(values), value, low, high)
            if index:
                path.lineTo(point)
            else:
                path.moveTo(point)
        return path

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.paths:
            self.empty(painter)
            painter.end()
            return
        low, high = _range([value for path in self.paths for value in path], zero=True)
        self.grid(painter, low, high)
        if self.low and self.high:
            band = QPolygonF()
            for index, value in enumerate(self.high):
                band.append(self._xy(index, len(self.high), value, low, high))
            for index in range(len(self.low) - 1, -1, -1):
                band.append(self._xy(index, len(self.low), self.low[index], low, high))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(mix(t.surface, t.text, 0.08)))
            painter.drawPolygon(band)
        faint = QColor(t.text_secondary)
        faint.setAlphaF(0.25)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(faint, 1))
        for path in self.paths:
            painter.drawPath(self._line(path, low, high))
        painter.setPen(QPen(QColor(t.text_secondary), 1.6, Qt.PenStyle.DashLine))
        painter.drawPath(self._line(self.middle, low, high))
        painter.setPen(QPen(QColor(t.text), 2))
        painter.drawPath(self._line(self.real, low, high))
        painter.end()

    def hover_text(self, x: float, y: float) -> str:
        area = self.plot()
        if not self.middle or not area.contains(QPointF(x, y)):
            return ""
        count = len(self.middle)
        index = min(max(round((x - area.left()) / area.width() * (count - 1)), 0), count - 1)
        return (
            f"trade {index}: real {self.real[index]:+.2f}%, median {self.middle[index]:+.2f}%, "
            f"5-95% {self.low[index]:+.2f}..{self.high[index]:+.2f}%"
        )


class HeatMap(Chart):
    def __init__(self, height: int = 220, name: str = "") -> None:
        super().__init__(height, name)
        self.values: list[list[float | None]] = []
        self.x_labels: list[str] = []
        self.y_labels: list[str] = []
        self.x_name = ""
        self.y_name = ""
        self.best: tuple[int, int] | None = None

    def set_grid(
        self,
        values: Sequence[Sequence[float | None]],
        x_labels: Sequence[str],
        y_labels: Sequence[str],
        x_name: str,
        y_name: str,
        best: tuple[int, int] | None = None,
    ) -> None:
        self.values = [list(row) for row in values]
        self.x_labels = list(x_labels)
        self.y_labels = list(y_labels)
        self.x_name, self.y_name = x_name, y_name
        self.best = best
        self.update()

    def _cells(self) -> list[tuple[int, int, QRectF]]:
        area = self.plot()
        rows = len(self.values)
        columns = max((len(row) for row in self.values), default=0)
        if not rows or not columns:
            return []
        width = area.width() / columns
        height = area.height() / rows
        found: list[tuple[int, int, QRectF]] = []
        for y in range(rows):
            for x in range(columns):
                top = area.top() + (rows - 1 - y) * height  # the first value row at the bottom
                found.append((y, x, QRectF(area.left() + x * width, top, width, height)))
        return found

    def _color(self, value: float | None, scale: float) -> QColor:
        t = self.tokens
        if value is None:
            return QColor(t.hover)
        share = min(abs(value) / scale, 1.0) if scale > 0 else 0.0
        target = t.profit if value >= 0 else t.loss
        return QColor(mix(t.surface, target, 0.15 + 0.6 * share))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cells = self._cells()
        if not cells:
            self.empty(painter)
            painter.end()
            return
        finite = [abs(v) for row in self.values for v in row if v is not None and math.isfinite(v)]
        scale = max(finite, default=0.0)
        painter.setFont(_small_font(self, 11))
        for y, x, box in cells:
            value = self.values[y][x] if x < len(self.values[y]) else None
            painter.fillRect(box.adjusted(1, 1, -1, -1), self._color(value, scale))
            if self.best == (y, x):
                painter.setPen(QPen(QColor(t.text), 2))
                painter.drawRect(box.adjusted(2, 2, -2, -2))
            painter.setPen(QColor(t.text))
            text = "n/a" if value is None else f"{value:+.2f}"
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        area = self.plot()
        painter.setFont(_small_font(self))
        painter.setPen(QColor(t.text_secondary))
        columns = max(len(row) for row in self.values)
        width = area.width() / columns
        for x, label in enumerate(self.x_labels[:columns]):
            box = QRectF(area.left() + x * width, area.bottom() + 3, width, PAD_BOTTOM - 4)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        rows = len(self.values)
        height = area.height() / rows
        for y, label in enumerate(self.y_labels[:rows]):
            top = area.top() + (rows - 1 - y) * height
            painter.drawText(QRectF(0, top, PAD_LEFT - 6, height), RIGHT, label)
        painter.end()

    def hover_text(self, x: float, y: float) -> str:
        for row, column, box in self._cells():
            if box.contains(QPointF(x, y)):
                value = self.values[row][column] if column < len(self.values[row]) else None
                shown = "too few trades" if value is None else f"{value:+.3f}"
                x_label = self.x_labels[column] if column < len(self.x_labels) else "?"
                y_label = self.y_labels[row] if row < len(self.y_labels) else "?"
                return f"{self.x_name}={x_label}, {self.y_name}={y_label}: {shown}"
        return ""


class CandleChart(Chart):
    def __init__(self, height: int = 220, name: str = "") -> None:
        super().__init__(height, name)
        self.bars: list[Candle] = []
        self.marks: list[Marker] = []
        self.digits = 5

    def set_candles(self, bars: Sequence[Candle], marks: Sequence[Marker], digits: int = 5) -> None:
        self.bars = list(bars)
        self.marks = list(marks)
        self.digits = digits
        self.update()

    def _bounds(self) -> tuple[float, float]:
        values = [bar.low for bar in self.bars] + [bar.high for bar in self.bars]
        return _range(values)

    def _slot(self, index: int) -> QRectF:
        area = self.plot()
        width = area.width() / max(len(self.bars), 1)
        return QRectF(area.left() + index * width, area.top(), width, area.height())

    def _y(self, value: float, low: float, high: float) -> float:
        area = self.plot()
        return area.bottom() - (value - low) / (high - low) * area.height()

    def grid(self, painter: QPainter, low: float, high: float) -> None:
        t = self.tokens
        area = self.plot()
        painter.setPen(QPen(QColor(t.border), 1))
        for share in (0.0, 0.5, 1.0):
            y = area.top() + area.height() * share
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setFont(_small_font(self))
        painter.setPen(QColor(t.text_secondary))
        for value, y in ((high, area.top()), (low, area.bottom())):
            box = QRectF(0, y - 8, PAD_LEFT - 4, 16)
            painter.drawText(box, RIGHT, f"{value:.{self.digits}f}")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.bars:
            self.empty(painter)
            painter.end()
            return
        low, high = self._bounds()
        self.grid(painter, low, high)
        for index, bar in enumerate(self.bars):
            slot = self._slot(index)
            color = QColor(t.profit if bar.close >= bar.open else t.loss)
            middle = slot.center().x()
            painter.setPen(QPen(color, 1))
            wick = (self._y(bar.high, low, high), self._y(bar.low, low, high))
            painter.drawLine(QPointF(middle, wick[0]), QPointF(middle, wick[1]))
            top = self._y(max(bar.open, bar.close), low, high)
            bottom = self._y(min(bar.open, bar.close), low, high)
            width = max(slot.width() * 0.6, 1.0)
            painter.fillRect(QRectF(middle - width / 2, top, width, max(bottom - top, 1.0)), color)
        for mark in self.marks:
            if not 0 <= mark.index < len(self.bars):
                continue
            slot = self._slot(mark.index)
            x = slot.center().x()
            y = self._y(mark.price, low, high)
            size = 6.0
            tip = QPolygonF()
            if mark.buy:
                tip.append(QPointF(x, y + 2))
                tip.append(QPointF(x - size, y + 2 + size * 1.4))
                tip.append(QPointF(x + size, y + 2 + size * 1.4))
            else:
                tip.append(QPointF(x, y - 2))
                tip.append(QPointF(x - size, y - 2 - size * 1.4))
                tip.append(QPointF(x + size, y - 2 - size * 1.4))
            painter.setPen(QPen(QColor(t.text), 1))
            painter.setBrush(QColor(t.profit if mark.win else t.loss))
            painter.drawPolygon(tip)
        painter.end()

    def hover_text(self, x: float, y: float) -> str:
        for index, bar in enumerate(self.bars):
            slot = self._slot(index)
            if slot.left() <= x < slot.right():
                digits = self.digits
                return (
                    f"O {bar.open:.{digits}f}  H {bar.high:.{digits}f}  "
                    f"L {bar.low:.{digits}f}  C {bar.close:.{digits}f}"
                )
        return ""


class Meter(Painted):
    """The design's thin meter (6 px): the used share in ink over the quiet track."""

    def __init__(self) -> None:
        super().__init__()
        self.setLayoutDirection(LTR)
        self.setFixedHeight(6)
        self.share = 0.0

    def set_share(self, share: float) -> None:
        self.share = min(max(share, 0.0), 1.0) if math.isfinite(share) else 0.0
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.hover))
        box = QRectF(self.rect())
        painter.drawRoundedRect(box, 3, 3)
        if self.share > 0:
            painter.setBrush(QColor(t.text))
            used = QRectF(0, 0, max(box.width() * self.share, 3), box.height())
            painter.drawRoundedRect(used, 3, 3)
        painter.end()
