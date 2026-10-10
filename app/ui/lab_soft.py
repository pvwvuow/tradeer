"""The softer AI Lab (docs/AI_LAB_V3.md section 3, 0.44.2).

The owner found the first AI Lab too sharp and dry. This keeps the No Curve v2 words, layout
and colours, and softens the shapes: 14 px corners on cards, 10 px on buttons and fields, a
round question bubble and pill suggestions, a little more air, and a faint shadow under the
cards. Colour still only where it means something (profit, loss, a warning).

- `soft_qss`: the rules, after `lab_qss` and `chat_qss` (Qt merges them per property).
- `fade_in`: a new message, card or answer fades in (220 ms; none with reduced motion), then
  a card gets its shadow (`shadow`).
- `AiMark`: the small mark in front of every AI answer.

Only the AI Lab changes; the other pages keep the owner's sharp corners.
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget

from app.ui.shell import Painted
from app.ui.theme import ThemeTokens, px

CARD_RADIUS = 14
BUTTON_RADIUS = 10
BUBBLE_RADIUS = 18
FADE_MS = 220
SHADOW_BLUR = 24
SHADOW_ALPHA = 0.07
TILE_WIDTH = 372  # two tiles side by side in the 760 px column
MARK_SIZE = 22


def soft_qss(tokens: ThemeTokens) -> str:
    """The softer shapes of the AI Lab, on top of its own rules."""
    t = tokens
    card, button = CARD_RADIUS, BUTTON_RADIUS
    return f"""
QFrame[lab="card"], QFrame[lab="verdict"], QFrame[lab="aside"], QFrame[lab="strip"] {{
    border-radius: {card}px;
}}
QFrame[lab="composer"] {{
    border-radius: 16px;
}}
QLabel[lab="bubble"] {{
    border-radius: {BUBBLE_RADIUS}px;
    padding: 12px 18px;
}}
QPushButton[lab="sg"] {{
    border-radius: 16px;
    padding: 8px 14px;
}}
QPushButton[lab="tile"] {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: {card}px;
    padding: 16px 18px;
    min-height: 0px;
    font-size: {px(14):g}pt;
    font-weight: 500;
    text-align: left;
}}
QPushButton[lab="tile"]:hover {{
    background-color: {t.hover};
    border-color: {t.border_strong};
}}
QPushButton[lab="tile"]:disabled {{
    color: {t.text_secondary};
}}
QPushButton[lab="primary"], QPushButton[lab="ghost"], QPushButton[lab="arrow"],
QPushButton[lab="item"], QFrame[lab="item"] {{
    border-radius: {button}px;
}}
QPushButton[lab="link"] {{
    border-radius: 8px;
}}
QLineEdit[lab="in"], QSpinBox[lab="in"], QComboBox[lab="in"], QDateEdit[lab="in"],
QLineEdit[lab="search"] {{
    border-radius: {button}px;
}}
QProgressBar[lab="progress"], QProgressBar[lab="progress"]::chunk {{
    border-radius: 3px;
}}
QLabel#AiChatSteps {{
    border-left: 2px solid {t.border};
    padding: 2px 0px 2px 14px;
}}
"""


def shadow(widget: QWidget, tokens: ThemeTokens) -> QGraphicsDropShadowEffect:
    """A faint shadow under a card."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(SHADOW_BLUR)
    effect.setOffset(0, 4)
    ink = QColor(tokens.text)
    ink.setAlphaF(SHADOW_ALPHA)
    effect.setColor(ink)
    widget.setGraphicsEffect(effect)
    return effect


def is_card(widget: QWidget) -> bool:
    return bool(widget.property("lab") == "card")


def settle(widget: QWidget, tokens: ThemeTokens) -> None:
    """After the fade: a card gets its shadow, anything else is drawn plainly again."""
    with contextlib.suppress(RuntimeError):  # the widget may be gone
        if is_card(widget):
            shadow(widget, tokens)
            return
        effect = widget.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)
            effect.setEnabled(False)


def fade_in(
    widget: QWidget,
    tokens: ThemeTokens,
    motion: bool = True,
) -> QPropertyAnimation | None:
    """Fade a new widget in (a widget with its own effect keeps it)."""
    if widget.graphicsEffect() is not None:
        return None
    if not motion:
        settle(widget, tokens)
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(FADE_MS)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(lambda: QTimer.singleShot(0, lambda: settle(widget, tokens)))
    animation.start()
    return animation


def _spark(size: float) -> QPainterPath:
    """A four-pointed spark in a square of `size`."""
    middle = size / 2
    reach = size * 0.34
    waist = size * 0.08
    path = QPainterPath()
    path.moveTo(QPointF(middle, middle - reach))
    path.quadTo(QPointF(middle + waist, middle - waist), QPointF(middle + reach, middle))
    path.quadTo(QPointF(middle + waist, middle + waist), QPointF(middle, middle + reach))
    path.quadTo(QPointF(middle - waist, middle + waist), QPointF(middle - reach, middle))
    path.quadTo(QPointF(middle - waist, middle - waist), QPointF(middle, middle - reach))
    path.closeSubpath()
    return path


class AiMark(Painted):
    """The AI's mark in front of an answer: a soft ink square with a spark."""

    def __init__(self, size: int = MARK_SIZE) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.setObjectName("AiMark")
        self.setAccessibleName("AI")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect())
        painter.setPen(QColor(t.text))
        painter.setBrush(QColor(t.text))
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        painter.setPen(QColor(t.bg))
        painter.setBrush(QColor(t.bg))
        painter.drawPath(_spark(box.width()))
        painter.end()
