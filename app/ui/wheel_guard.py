"""The mouse wheel scrolls the page, never the value under it (8 October 2026).

Qt changes a spin box, a drop-down, a slider or the tab of a tab bar when the wheel turns
over it, even while the user only scrolls the page past it: a risk or strategy setting could
change by accident. With the guard a value changes by the wheel only after the user clicked
into the field (it has the keyboard focus); otherwise the wheel scrolls the page around it.
A chart inside a scrolling page scrolls the page too, and zooms with Ctrl held.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QSlider,
    QTabBar,
    QWidget,
)

GUARDED = (QAbstractSpinBox, QComboBox, QSlider, QTabBar)
_INSTALLED: dict[str, QObject] = {}


def guarded(widget: QObject) -> bool:
    return isinstance(widget, GUARDED)


def calm(widget: QWidget) -> None:
    """No keyboard focus from the wheel (the focus comes from a click or Tab)."""
    if widget.focusPolicy() == Qt.FocusPolicy.WheelFocus:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def calm_tree(root: QWidget) -> int:
    """`calm` every guarded widget under `root`; returns how many there are."""
    widgets = [w for w in (root, *root.findChildren(QWidget)) if guarded(w)]
    for widget in widgets:
        calm(widget)
    return len(widgets)


def scroll_parent(widget: QWidget) -> QAbstractScrollArea | None:
    """The nearest scrolling area around `widget` (charts do not count)."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea) and not isinstance(parent, pg.PlotWidget):
            return parent
        parent = parent.parentWidget()
    return None


def plot_of(widget: QWidget) -> QWidget | None:
    """The chart whose drawing area `widget` is, or None."""
    parent = widget.parentWidget()
    if isinstance(parent, pg.PlotWidget) and parent.viewport() is widget:
        found: QWidget = parent
        return found
    return None


def _forward(area: QAbstractScrollArea | None, event: QEvent) -> bool:
    if area is not None:
        QCoreApplication.sendEvent(area.viewport(), event)
    return True


class WheelGuard(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWidget):
            return False
        kind = event.type()
        if kind == QEvent.Type.Polish and guarded(watched):
            calm(watched)
            return False
        if kind != QEvent.Type.Wheel or not isinstance(event, QWheelEvent):
            return False
        if guarded(watched):
            if watched.hasFocus() and not isinstance(watched, QTabBar):
                return False
            return _forward(scroll_parent(watched), event)
        plot = plot_of(watched)
        if plot is None or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        area = scroll_parent(plot)
        if area is None or area.verticalScrollBar().maximum() <= 0:
            return False
        return _forward(area, event)


def install_wheel_guard() -> QObject | None:
    """Watch every widget of the application (once); None without an application."""
    application = QCoreApplication.instance()
    if application is None:
        return None
    found = _INSTALLED.get("guard")
    if found is None:
        found = WheelGuard(application)
        application.installEventFilter(found)
        _INSTALLED["guard"] = found
    return found
