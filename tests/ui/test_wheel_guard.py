"""The wheel scrolls the page past a setting instead of changing it (8 October 2026)."""

from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from app.ui.wheel_guard import calm, calm_tree, install_wheel_guard


def wheel(down: bool = True) -> QWheelEvent:
    step = -120 if down else 120
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, step),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def page(qtbot: QtBot) -> tuple[QScrollArea, QSpinBox, QComboBox]:
    area = QScrollArea()
    qtbot.addWidget(area)
    body = QWidget()
    body.setMinimumHeight(3000)
    layout = QVBoxLayout(body)
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    combo = QComboBox()
    combo.addItems(["one", "two", "three"])
    layout.addWidget(spin)
    layout.addWidget(combo)
    layout.addStretch(1)
    area.setWidget(body)
    area.setWidgetResizable(True)
    area.resize(300, 300)
    area.show()
    return area, spin, combo


def test_the_wheel_over_a_setting_scrolls_the_page(qtbot: QtBot) -> None:
    assert install_wheel_guard() is install_wheel_guard()
    area, spin, combo = page(qtbot)
    assert area.verticalScrollBar().maximum() > 0
    QCoreApplication.sendEvent(spin, wheel())
    QCoreApplication.sendEvent(combo, wheel())
    assert spin.value() == 50 and combo.currentIndex() == 0
    assert area.verticalScrollBar().value() > 0


def test_settings_take_no_focus_from_the_wheel(qtbot: QtBot) -> None:
    spin = QSpinBox()
    qtbot.addWidget(spin)
    spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
    calm(spin)
    assert spin.focusPolicy() == Qt.FocusPolicy.StrongFocus
    root = QWidget()
    qtbot.addWidget(root)
    QComboBox(root).setFocusPolicy(Qt.FocusPolicy.WheelFocus)
    assert calm_tree(root) == 1
    assert root.findChildren(QComboBox)[0].focusPolicy() == Qt.FocusPolicy.StrongFocus
