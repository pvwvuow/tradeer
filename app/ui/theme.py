"""Design tokens in one place; the Qt stylesheet is generated from them (spec F1)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.core.ui_prefs import ThemeName

SPACE = 8
SPACE_WIDE = 12
SPACE_XL = 16
RADIUS = 12
RADIUS_CONTROL = 8
# Font sizes in points (12, 14, 18 and 24 px at 96 dpi). A size in px makes every widget font
# report point size -1, and Qt then warned "QFont::setPointSize: Point size <= 0" hundreds of
# times a minute on a real PC.
FONT_SMALL = 9
FONT_BODY = 10.5
FONT_SECTION = 13.5
FONT_TITLE = 18


@dataclass(frozen=True)
class ThemeTokens:
    name: ThemeName
    bg: str
    surface: str
    card: str
    border: str
    text: str
    text_secondary: str
    accent: str
    accent_text: str
    profit: str
    loss: str
    warning: str

    def colors(self) -> dict[str, str]:
        return {key: value for key, value in asdict(self).items() if key != "name"}


DARK = ThemeTokens(
    name=ThemeName.DARK,
    bg="#0B0D12",
    surface="#12151C",
    card="#171B24",
    border="#232836",
    text="#E6E8EE",
    text_secondary="#8A91A5",
    accent="#5B8CFF",
    accent_text="#0B0D12",
    profit="#22C55E",
    loss="#EF4444",
    warning="#F59E0B",
)

LIGHT = ThemeTokens(
    name=ThemeName.LIGHT,
    bg="#F6F7F9",
    surface="#FFFFFF",
    card="#FFFFFF",
    border="#DDE1E8",
    text="#12151C",
    text_secondary="#5B6475",
    accent="#2563EB",
    accent_text="#FFFFFF",
    profit="#15803D",
    loss="#B91C1C",
    warning="#B45309",
)


def tokens_for(theme: ThemeName) -> ThemeTokens:
    return LIGHT if theme is ThemeName.LIGHT else DARK


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else math.pow((srgb + 0.055) / 1.055, 2.4)


def relative_luminance(hex_color: str) -> float:
    red, green, blue = (int(hex_color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def build_qss(tokens: ThemeTokens) -> str:
    t = tokens
    return f"""
QWidget {{
    background-color: {t.bg};
    color: {t.text};
    font-size: {FONT_BODY:g}pt;
}}
QFrame#TopBar {{
    background-color: {t.surface};
    border-bottom: 1px solid {t.border};
}}
QFrame#Sidebar {{
    background-color: {t.surface};
    border-right: 1px solid {t.border};
}}
QFrame[role="card"] {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: {RADIUS}px;
}}
QLabel {{
    background: transparent;
}}
QLabel[role="brand"] {{
    font-size: {FONT_SECTION:g}pt;
    font-weight: 600;
}}
QLabel[role="title"] {{
    font-size: {FONT_TITLE:g}pt;
    font-weight: 700;
}}
QLabel[role="section"] {{
    color: {t.text_secondary};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 600;
    padding-top: {SPACE}px;
}}
QLabel[role="muted"] {{
    color: {t.text_secondary};
}}
QLabel[role="status"] {{
    color: {t.text_secondary};
    font-size: {FONT_SMALL:g}pt;
    padding: 0 {SPACE}px;
}}
QLabel[role="profit"] {{
    color: {t.profit};
}}
QLabel[role="loss"] {{
    color: {t.loss};
}}
QLabel[role="warning"] {{
    color: {t.warning};
}}
QFrame[role="row"] {{
    background-color: transparent;
    border-top: 1px solid {t.border};
}}
QLabel[role="badge"] {{
    color: {t.warning};
    border: 1px solid {t.warning};
    border-radius: {RADIUS_CONTROL}px;
    font-size: {FONT_SMALL:g}pt;
    font-weight: 700;
    padding: 2px {SPACE}px;
}}
QPushButton {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    padding: {SPACE}px {SPACE_XL}px;
}}
QPushButton:hover {{
    border-color: {t.accent};
}}
QPushButton:disabled {{
    color: {t.text_secondary};
}}
QPushButton[nav="true"] {{
    background-color: transparent;
    border: none;
    padding: {SPACE}px {SPACE_WIDE}px;
    text-align: left;
}}
QPushButton[nav="true"]:hover {{
    background-color: {t.card};
}}
QPushButton[nav="true"]:checked {{
    background-color: {t.card};
    color: {t.accent};
    font-weight: 600;
}}
QPushButton[variant="primary"] {{
    background-color: {t.accent};
    color: {t.accent_text};
    border: none;
}}
QPushButton[variant="danger"] {{
    background-color: transparent;
    color: {t.loss};
    border: 1px solid {t.loss};
}}
QPushButton[variant="danger"]:disabled {{
    color: {t.text_secondary};
    border-color: {t.border};
}}
QLineEdit {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    padding: {SPACE}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QLineEdit:focus {{
    border-color: {t.accent};
}}
QListWidget {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
}}
QListWidget::item {{
    border-radius: 6px;
    padding: {SPACE}px;
}}
QListWidget::item:selected {{
    background-color: {t.accent};
    color: {t.accent_text};
}}
QStatusBar {{
    background-color: {t.surface};
    border-top: 1px solid {t.border};
}}
QStatusBar::item {{
    border: none;
}}
QDialog#CommandPalette {{
    background-color: {t.surface};
}}
QToolTip {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border};
}}
"""
