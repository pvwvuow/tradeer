"""Qt helpers for the 0.13 design system: glyph icons, status chips and themed charts.

Icons come from the icon font every Windows 10 and 11 PC already has (Segoe Fluent Icons or
Segoe MDL2 Assets), so the app ships no image files. Where neither font exists (Linux CI)
the icons are simply left out instead of drawing empty boxes.

Phase 16b (spec F1): icons are drawn at the screen's pixel ratio (sharp at 150-300% scaling),
charts take their colors from the current theme (`chart_tokens`), and buttons without a
readable text get a name for screen readers (`name_controls`).

7 October 2026: charts draw smooth (antialiased) lines without pyqtgraph's small "A" button,
and buttons, drop-downs and tabs show a hand cursor (`hand_cursors`), like modern apps.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QGraphicsView,
    QLabel,
    QTabBar,
    QTableView,
    QWidget,
)

from app.ui.theme import CHIP_TONES, DARK, ThemeTokens

ICON_FAMILIES: tuple[str, ...] = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
ICON_SIZE = 18
UI_FAMILIES: tuple[str, ...] = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans")
PIXEL_RATIO = 2.0  # the least icons are drawn at; higher on screens scaled above 200%
# What a screen reader should say for a button whose text is only an arrow or a symbol.
GLYPH_NAMES: dict[str, str] = {
    "\u2190": "Previous",
    "\u2192": "Next",
    "\u25b2": "Up",
    "\u25bc": "Down",
    "\u2715": "Close",
}
# Buttons Qt adds by itself, by object name (the corner of a table selects every row).
BUILT_IN_NAMES: dict[str, str] = {"qt_tableview_cornerbutton": "Select all"}
_CHART: dict[str, ThemeTokens] = {"tokens": DARK}


class Glyph:
    """Segoe MDL2 code points (the same in Segoe Fluent Icons)."""

    HOME = "\ue80f"
    DASHBOARD = "\ue8a9"
    MARKET = "\ueafc"
    SIGNALS = "\ue945"
    POSITIONS = "\ue8fd"
    ANALYTICS = "\ue9d2"
    JOURNAL = "\ue70b"
    BACKTEST = "\ue81c"
    MODEL = "\ue99a"
    AI_LAB = "\uea80"
    STRATEGIES = "\ue9e9"
    RISK = "\uea18"
    LOGS = "\ue9f9"
    HEALTH = "\ue95e"
    SETTINGS = "\ue713"
    SEARCH = "\ue721"
    THEME = "\ue793"
    SWITCH = "\ue8ab"
    STOP = "\ue71a"
    UPDATE = "\ue896"
    INFO = "\ue946"
    BACK = "\ue72b"


PAGE_GLYPHS: dict[str, str] = {
    "home": Glyph.HOME,
    "dashboard": Glyph.DASHBOARD,
    "market": Glyph.MARKET,
    "signals": Glyph.SIGNALS,
    "positions": Glyph.POSITIONS,
    "analytics": Glyph.ANALYTICS,
    "journal": Glyph.JOURNAL,
    "backtest": Glyph.BACKTEST,
    "model": Glyph.MODEL,
    "ai_lab": Glyph.AI_LAB,
    "strategies": Glyph.STRATEGIES,
    "risk": Glyph.RISK,
    "logs": Glyph.LOGS,
    "health": Glyph.HEALTH,
    "settings": Glyph.SETTINGS,
}


@cache
def icon_family() -> str:
    """The first installed icon font, or "" when none is installed."""
    installed = set(QFontDatabase.families())
    return next((family for family in ICON_FAMILIES if family in installed), "")


def icons_available() -> bool:
    return bool(icon_family())


def ui_font(base: QFont) -> QFont:
    """The window font: Segoe UI Variable on Windows 11, Segoe UI on 10, Inter elsewhere."""
    font = QFont(base)
    font.setFamilies(list(UI_FAMILIES))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def pixel_ratio() -> float:
    """The pixel ratio icons are drawn at: the sharpest screen's, at least `PIXEL_RATIO`."""
    application = QGuiApplication.instance()
    ratio = application.devicePixelRatio() if isinstance(application, QGuiApplication) else 1.0
    return max(PIXEL_RATIO, float(ratio))


def glyph_pixmap(glyph: str, color: str, size: int = ICON_SIZE) -> QPixmap:
    ratio = pixel_ratio()
    side = round(size * ratio)
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    family = icon_family()
    if family:
        font = QFont(family)
        font.setPixelSize(round(side * 0.82))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def glyph_icon(glyph: str, color: str, checked: str | None = None) -> QIcon:
    """An icon drawn from the icon font; `checked` colors the checked (selected) state."""
    if not glyph or not icons_available():
        return QIcon()
    icon = QIcon()
    icon.addPixmap(glyph_pixmap(glyph, color), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(glyph_pixmap(glyph, checked or color), QIcon.Mode.Normal, QIcon.State.On)
    return icon


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def chip(text: str = "", tone: str = "neutral") -> QLabel:
    label = QLabel(text)
    label.setProperty("chip", tone)
    return label


def set_chip(label: QLabel, text: str, tone: str) -> None:
    """Change a chip's text and tone (neutral, accent, profit, loss or warning)."""
    if tone not in CHIP_TONES:
        raise ValueError(f"Unknown chip tone {tone!r}")
    label.setText(text)
    if label.property("chip") != tone:
        label.setProperty("chip", tone)
        repolish(label)


def chart_tokens() -> ThemeTokens:
    """The theme charts draw their lines with; `style_plots` sets it on every theme change."""
    return _CHART["tokens"]


def chart_pen(color: str, width: float = 1.0) -> Any:
    return pg.mkPen(color, width=width)


def soft_color(color: str, alpha: int) -> QColor:
    """`color` with an alpha, for area fills under a line."""
    found = QColor(color)
    found.setAlpha(alpha)
    return found


def style_plots(root: QWidget, tokens: ThemeTokens) -> int:
    """Give every pyqtgraph chart under `root` the theme's card color and axis colors.

    pyqtgraph draws black charts by default, which looked broken in the light theme. The
    tokens are also remembered for the lines the pages draw later (`chart_tokens`).
    Returns how many charts were styled.
    """
    _CHART["tokens"] = tokens
    pg.setConfigOptions(antialias=True)
    plots = [view for view in root.findChildren(QGraphicsView) if isinstance(view, pg.PlotWidget)]
    for plot in plots:
        _style_plot(plot, tokens)
    return len(plots)


def _style_plot(plot: Any, tokens: ThemeTokens) -> None:
    plot.setBackground(tokens.card)
    item = plot.getPlotItem()
    item.hideButtons()
    for name in ("left", "bottom", "right", "top"):
        axis = item.getAxis(name)
        axis.setPen(pg.mkPen(tokens.border_strong))
        axis.setTextPen(pg.mkPen(tokens.text_secondary))


TABLE_ROW_HEIGHT = 30


def style_tables(root: QWidget) -> int:
    """One look for every table under `root`: no grid, zebra rows, roomy rows, left headers.

    Some pages build their tables by hand; this keeps them in line with `make_table`.
    """
    tables = root.findChildren(QTableView)
    for table in tables:
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        if table.selectionMode() == QAbstractItemView.SelectionMode.NoSelection:
            table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return len(tables)


def hand_cursors(root: QWidget) -> int:
    """A hand cursor over every button, drop-down and tab bar under `root` (and `root`).

    Returns how many widgets got it.
    """
    widgets = [root, *root.findChildren(QWidget)]
    clickable = [w for w in widgets if isinstance(w, QAbstractButton | QComboBox | QTabBar)]
    for widget in clickable:
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return len(clickable)


def _readable(text: str) -> str:
    text = text.replace("&", "").strip()
    return "" if text in GLYPH_NAMES else text


def accessible_name(button: QAbstractButton) -> str:
    """What a screen reader says for `button`: its name, its text, or its tooltip."""
    readable = _readable(button.text())
    return button.accessibleName() or readable or button.toolTip()


def name_controls(root: QWidget) -> int:
    """Name the icon-only and arrow-only buttons under `root` for screen readers (spec F1).

    The name is the tooltip, what the arrow means ("Previous", "Next") or, for the buttons Qt
    adds itself, what they do ("Select all"). Returns how many buttons were named.
    """
    named = 0
    for button in root.findChildren(QAbstractButton):
        if button.accessibleName() or _readable(button.text()):
            continue
        name = button.toolTip() or GLYPH_NAMES.get(button.text().strip(), "")
        name = name or BUILT_IN_NAMES.get(button.objectName(), "")
        if name:
            button.setAccessibleName(name)
            named += 1
    return named


def unnamed_controls(root: QWidget) -> list[str]:
    """The buttons under `root` a screen reader could not name (an empty list is the goal)."""
    buttons = root.findChildren(QAbstractButton)
    return [
        button.objectName() or type(button).__name__
        for button in buttons
        if not accessible_name(button)
    ]
