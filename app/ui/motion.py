"""Small motion: a page fades in when the sidebar switches to it (7 October 2026).

The fade is short (140 ms) and the effect is removed when it ends, so pages draw exactly as
before. Pages with a chart skip it: pyqtgraph draws through its own view, which an opacity
effect would have to render into a picture on every frame.
"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QGraphicsView, QWidget

FADE_MS = 140


def can_fade(widget: QWidget) -> bool:
    """A visible page without charts (or anything that already has an effect)."""
    if not widget.isVisible() or widget.graphicsEffect() is not None:
        return False
    return not widget.findChildren(QGraphicsView)


def fade_in(widget: QWidget, duration: int = FADE_MS) -> QPropertyAnimation | None:
    """Fade `widget` from clear to solid; returns the running animation, or None."""
    if duration <= 0 or not can_fade(widget):
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(
        lambda: widget.setGraphicsEffect(cast(QGraphicsOpacityEffect, None)),
    )
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation
