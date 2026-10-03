"""Qt helpers for the 0.13 design system: glyph icons, status chips and themed charts.

Icons come from the icon font every Windows 10 and 11 PC already has (Segoe Fluent Icons or
Segoe MDL2 Assets), so the app ships no image files. Where neither font exists (Linux CI)
the icons are simply left out instead of drawing empty boxes.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QGraphicsView, QLabel, QTableView, QWidget

from app.ui.theme import CHIP_TONES, ThemeTokens

ICON_FAMILIES: tuple[str, ...] = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
ICON_SIZE = 18
UI_FAMILIES: tuple[str, ...] = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans")
PIXEL_RATIO = 2.0


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


def glyph_pixmap(glyph: str, color: str, size: int = ICON_SIZE) -> QPixmap:
    side = round(size * PIXEL_RATIO)
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
    pixmap.setDevicePixelRatio(PIXEL_RATIO)
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


def style_plots(root: QWidget, tokens: ThemeTokens) -> int:
    """Give every pyqtgraph chart under `root` the theme's card color and axis colors.

    pyqtgraph draws black charts by default, which looked broken in the light theme.
    Returns how many charts were styled.
    """
    plots = [view for view in root.findChildren(QGraphicsView) if isinstance(view, pg.PlotWidget)]
    for plot in plots:
        _style_plot(plot, tokens)
    return len(plots)


def _style_plot(plot: Any, tokens: ThemeTokens) -> None:
    plot.setBackground(tokens.card)
    item = plot.getPlotItem()
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
