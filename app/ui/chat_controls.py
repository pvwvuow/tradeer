"""The chat's small controls as icons, the way chat apps draw them (0.47.0).

The owner found the chat below the usual standard: "Copy" was a word where every chat app
has a small icon. Here are the icons (line icons on a 24 px grid, drawn with the theme's
colours, no image files) and the buttons made of them:

- `IconButton`: a 30 px icon button with a soft hover, a tooltip and an accessible name;
  checkable for the feedback thumbs; chevrons mirror right to left.
- `CopyIconButton`: copy, then a check mark and "Copied" for a moment.
- `RoundSendButton`: the round send button (an arrow up), a rounded square to stop while
  the AI works.
- `icon_pixmap`: an icon as a pixmap, for a text button that also carries an icon.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.ui.theme import DEFAULT, ThemeTokens

ICON_BUTTON = 30
ICON_SIZE = 18
SEND_SIZE = 34
COPIED_MS = 1500
STROKE = 1.8
MIRRORED = frozenset({"chevron_left", "chevron_right"})

CONTROL_FA: dict[str, str] = {
    "Copy": "کپی",
    "Copied": "کپی شد",
    "Copy message": "کپی پیام",
    "Copy answer": "کپی پاسخ",
    "Regenerate": "ساختن دوباره",
    "Good answer": "پاسخ خوب",
    "Bad answer": "پاسخ بد",
    "Send": "ارسال",
    "Stop": "توقف",
    "Jump to the latest message": "رفتن به آخرین پیام",
    "Earlier prompts": "پیشنهادهای قبلی",
    "More prompts": "پیشنهادهای بیشتر",
}


def control_words(fa: bool) -> Callable[[str], str]:
    def word(english: str) -> str:
        return CONTROL_FA.get(english, english) if fa else english

    return word


# The icons --------------------------------------------------------------------------------
def _copy(path: QPainterPath) -> None:
    path.addRoundedRect(QRectF(9, 9, 11, 11), 2.2, 2.2)
    path.moveTo(15, 9)
    path.lineTo(15, 6)
    path.quadTo(15, 4, 13, 4)
    path.lineTo(6, 4)
    path.quadTo(4, 4, 4, 6)
    path.lineTo(4, 13)
    path.quadTo(4, 15, 6, 15)
    path.lineTo(9, 15)


def _check(path: QPainterPath) -> None:
    path.moveTo(5, 12.5)
    path.lineTo(10, 17.5)
    path.lineTo(19, 7)


def _head(path: QPainterPath, end: QPointF, direction: float, size: float = 4.0) -> None:
    """An arrow head at `end`, pointing along `direction` (radians, y down)."""
    for turn in (math.radians(150), math.radians(-150)):
        angle = direction + turn
        path.moveTo(end)
        path.lineTo(end.x() + size * math.cos(angle), end.y() + size * math.sin(angle))


def _retry(path: QPainterPath) -> None:
    box = QRectF(5, 5, 14, 14)
    start, span = 70.0, 280.0  # Qt degrees: counterclockwise from three o'clock
    path.arcMoveTo(box, start)
    path.arcTo(box, start, span)
    end = math.radians(start + span)
    point = QPointF(12 + 7 * math.cos(end), 12 - 7 * math.sin(end))
    # moving counterclockwise on screen (y down): the tangent of (cos t, -sin t) is
    # (-sin t, -cos t)
    _head(path, point, math.atan2(-math.cos(end), -math.sin(end)))


def _thumb(path: QPainterPath) -> None:
    path.addRoundedRect(QRectF(3.5, 11, 3.5, 9), 1, 1)
    path.moveTo(7, 11)
    path.lineTo(10.5, 4.5)
    path.quadTo(13.2, 3.8, 13, 6.5)
    path.lineTo(12.6, 9.5)
    path.lineTo(17.8, 9.5)
    path.quadTo(20.3, 9.6, 19.8, 12)
    path.lineTo(18.6, 18)
    path.quadTo(18.2, 20, 16.2, 20)
    path.lineTo(7, 20)


def _arrow_up(path: QPainterPath) -> None:
    path.moveTo(12, 19)
    path.lineTo(12, 5)
    path.moveTo(6, 11)
    path.lineTo(12, 5)
    path.lineTo(18, 11)


def _chevron_left(path: QPainterPath) -> None:
    path.moveTo(14.5, 5.5)
    path.lineTo(8, 12)
    path.lineTo(14.5, 18.5)


def _flip_y(build: Callable[[QPainterPath], None]) -> Callable[[QPainterPath], None]:
    def flipped(path: QPainterPath) -> None:
        inner = QPainterPath()
        build(inner)
        path.addPath(QTransform(1, 0, 0, -1, 0, 24).map(inner))

    return flipped


def _flip_x(build: Callable[[QPainterPath], None]) -> Callable[[QPainterPath], None]:
    def flipped(path: QPainterPath) -> None:
        inner = QPainterPath()
        build(inner)
        path.addPath(QTransform(-1, 0, 0, 1, 24, 0).map(inner))

    return flipped


ICONS: dict[str, Callable[[QPainterPath], None]] = {
    "copy": _copy,
    "check": _check,
    "retry": _retry,
    "thumb_up": _thumb,
    "thumb_down": _flip_y(_thumb),
    "arrow_up": _arrow_up,
    "arrow_down": _flip_y(_arrow_up),
    "chevron_left": _chevron_left,
    "chevron_right": _flip_x(_chevron_left),
}


def icon_path(name: str, mirror: bool = False) -> QPainterPath:
    """The icon `name` on the 24 px grid (mirrored left to right when asked)."""
    build = ICONS.get(name)
    path = QPainterPath()
    if build is None:
        return path
    if mirror:
        _flip_x(build)(path)
    else:
        build(path)
    return path


def draw_chat_icon(
    painter: QPainter,
    name: str,
    box: QRectF,
    color: str,
    *,
    mirror: bool = False,
    stroke: float = STROKE,
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(box.topLeft())
    scale = box.width() / 24
    painter.scale(scale, scale)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(icon_path(name, mirror))
    painter.restore()


def icon_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    """The icon as a pixmap, sharp on high-DPI screens (for a text button's icon)."""
    app = QApplication.instance()
    ratio = 1.0
    if isinstance(app, QApplication):
        screen = app.primaryScreen()
        ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_chat_icon(painter, name, QRectF(0, 0, size, size), color)
    painter.end()
    return pixmap


# The buttons ------------------------------------------------------------------------------
class IconButton(QPushButton):
    """An icon button (30 px): a soft square under the mouse, the ink when checked."""

    def __init__(
        self,
        icon: str,
        name: str,
        *,
        size: int = ICON_BUTTON,
        icon_size: int = ICON_SIZE,
        round_: bool = False,
        outlined: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.icon_px = icon_size
        self.round = round_
        self.outlined = outlined
        self.tokens: ThemeTokens = DEFAULT
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.set_name(name)

    def set_name(self, name: str) -> None:
        self.setToolTip(name)
        self.setAccessibleName(name)

    def set_icon(self, icon: str) -> None:
        self.icon_name = icon
        self.update()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return self.size()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = box.height() / 2 if self.round else 8.0
        hovered = self.underMouse() and self.isEnabled()
        fill = ""
        if self.isDown():
            fill = t.accent_soft
        elif hovered or self.isChecked():
            fill = t.hover
        if self.outlined:
            painter.setPen(QPen(QColor(t.border_strong), 1))
            painter.setBrush(QColor(fill or t.surface))
            painter.drawRoundedRect(box, radius, radius)
        elif fill:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(box, radius, radius)
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.accent), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), radius, radius)
        if not self.isEnabled():
            color = t.border_strong
        elif hovered or self.isChecked():
            color = t.text
        else:
            color = t.text_secondary
        side = float(self.icon_px)
        icon = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        mirror = self.icon_name in MIRRORED and self.isRightToLeft()
        draw_chat_icon(painter, self.icon_name, icon, color, mirror=mirror)
        painter.end()


class CopyIconButton(IconButton):
    """Copy as an icon: a check mark and "Copied" for a moment after a click."""

    copied = Signal(str)

    def __init__(self, payload: Callable[[], str], fa: bool = False, name: str = "Copy") -> None:
        word = control_words(fa)
        super().__init__("copy", word(name))
        self.word = word
        self.name = name
        self.payload = payload
        self.last = ""
        self.clicked.connect(self.copy)

    def copy(self) -> str:
        self.last = self.payload()
        clipboard = QApplication.clipboard()
        if self.last and clipboard is not None:
            clipboard.setText(self.last)
            self.set_icon("check")
            self.set_name(self.word("Copied"))
            self.copied.emit(self.last)
            QTimer.singleShot(COPIED_MS, self._back)
        return self.last

    def _back(self) -> None:
        self.set_icon("copy")
        self.set_name(self.word(self.name))


class RoundSendButton(QPushButton):
    """Send (34 px, round): the ink with an arrow when there is text, quiet when empty; a
    rounded square to stop while the AI works."""

    def __init__(self) -> None:
        super().__init__()
        self.tokens: ThemeTokens = DEFAULT
        self.has_text = False
        self.busy = False
        self.setFixedSize(SEND_SIZE, SEND_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def set_look(self, has_text: bool, busy: bool) -> None:
        self.has_text = has_text
        self.busy = busy
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.busy or self.has_text
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.text if full else t.hover))
        painter.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        if self.busy:
            side = 11.0
            square = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
            painter.setBrush(QColor(t.bg))
            painter.drawRoundedRect(square, 2.5, 2.5)
        else:
            ink = t.bg if self.has_text else t.text_secondary
            side = 18.0
            box = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
            draw_chat_icon(painter, "arrow_up", box, ink, stroke=2.2)
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.text_secondary), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        painter.end()


def copy_icon(color: str) -> QIcon:
    return QIcon(icon_pixmap("copy", color))


def check_icon(color: str) -> QIcon:
    return QIcon(icon_pixmap("check", color))
