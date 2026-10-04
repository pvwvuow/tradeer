"""Design tokens in one place; the Qt stylesheet is generated from them (spec F1).

Accessibility (spec F1, Phase 16b): every text color meets WCAG AA (4.5:1) on every surface it
is drawn on, controls have a border of at least 3:1 and a visible keyboard focus ring, and
`contrast_failures` lists any pair that misses (a unit test keeps the list empty).
"""

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
AA_TEXT = 4.5  # WCAG 2.1 AA, normal text
AA_NON_TEXT = 3.0  # WCAG 2.1 AA, control borders and the focus ring
TEXT_COLORS = ("text", "text_secondary", "accent", "profit", "loss", "warning")
SURFACES = ("bg", "surface", "card")
# Hover and selection fills only ever carry the plain text colors (selection-color is `text`).
FILLS = ("hover", "accent_soft")
PLAIN_TEXT = ("text", "text_secondary")


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
    # The 0.13 design system: hover and selection fills, a stronger border for controls and
    # soft backgrounds for the status chips (each tone's text meets WCAG AA on its fill).
    hover: str
    accent_soft: str
    border_strong: str
    profit_soft: str
    loss_soft: str
    warning_soft: str
    # Phase 16b: the border of buttons and inputs, at least 3:1 against the page (WCAG 1.4.11).
    control_border: str

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
    hover="#1D2230",
    accent_soft="#1A2440",
    border_strong="#2E3446",
    profit_soft="#0F2A1B",
    loss_soft="#2A1214",
    warning_soft="#2E2210",
    control_border="#6B7488",
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
    profit="#147236",
    loss="#B91C1C",
    warning="#A84B05",
    hover="#EEF1F5",
    accent_soft="#EDF3FF",
    border_strong="#C9CFD9",
    profit_soft="#EEF8F1",
    loss_soft="#FBEAEA",
    warning_soft="#FDF1E3",
    control_border="#7E8798",
)

CHIP_TONES: tuple[str, ...] = ("neutral", "accent", "profit", "loss", "warning")


def chip_colors(tokens: ThemeTokens, tone: str) -> tuple[str, str]:
    """(text, fill) of a status chip."""
    pairs = {
        "neutral": (tokens.text_secondary, tokens.hover),
        "accent": (tokens.accent, tokens.accent_soft),
        "profit": (tokens.profit, tokens.profit_soft),
        "loss": (tokens.loss, tokens.loss_soft),
        "warning": (tokens.warning, tokens.warning_soft),
    }
    return pairs[tone]


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


def contrast_pairs(tokens: ThemeTokens) -> list[tuple[str, str, float]]:
    """(foreground token, background token, minimum ratio) for every pair the app draws."""
    pairs = [(fg, bg, AA_TEXT) for fg in TEXT_COLORS for bg in SURFACES]
    pairs += [(fg, bg, AA_TEXT) for fg in PLAIN_TEXT for bg in FILLS]
    pairs.append(("accent_text", "accent", AA_TEXT))
    names = {
        "neutral": ("text_secondary", "hover"),
        "accent": ("accent", "accent_soft"),
        "profit": ("profit", "profit_soft"),
        "loss": ("loss", "loss_soft"),
        "warning": ("warning", "warning_soft"),
    }
    pairs += [(fg, bg, AA_TEXT) for fg, bg in names.values()]
    for edge in ("control_border", "accent"):
        pairs += [(edge, bg, AA_NON_TEXT) for bg in SURFACES]
    return pairs


def contrast_failures(tokens: ThemeTokens) -> list[str]:
    """The pairs below their WCAG AA minimum, e.g. "loss on hover: 4.22 < 4.5"."""
    colors = tokens.colors()
    failures = []
    for fg, bg, minimum in contrast_pairs(tokens):
        ratio = contrast_ratio(colors[fg], colors[bg])
        if ratio < minimum:
            failures.append(f"{fg} on {bg}: {ratio:.2f} < {minimum:g}")
    return failures


def _chip_rules(tokens: ThemeTokens) -> str:
    rules = []
    for tone in CHIP_TONES:
        text, fill = chip_colors(tokens, tone)
        rules.append(
            f'QLabel[chip="{tone}"] {{ color: {text}; background-color: {fill}; '
            f"border-radius: 10px; font-size: {FONT_SMALL:g}pt; font-weight: 600; "
            "padding: 3px 10px; }",
        )
    return "\n".join(rules)


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
QFrame[role="card"] QLabel, QFrame[role="card"] QCheckBox, QFrame[role="card"] QRadioButton {{
    background: transparent;
}}
QFrame[role="empty"] {{
    background-color: {t.card};
    border: 1px dashed {t.border_strong};
    border-radius: {RADIUS}px;
}}
QLabel {{
    background: transparent;
}}
QLabel[role="brand"] {{
    font-size: {FONT_SECTION:g}pt;
    font-weight: 600;
}}
QLabel[role="logo"] {{
    background-color: {t.accent};
    color: {t.accent_text};
    border-radius: {RADIUS_CONTROL}px;
    font-size: {FONT_BODY:g}pt;
    font-weight: 800;
}}
QLabel[role="title"] {{
    font-size: {FONT_TITLE:g}pt;
    font-weight: 700;
}}
QLabel[role="subtitle"] {{
    color: {t.text_secondary};
}}
QLabel[role="crumb"] {{
    color: {t.text_secondary};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 600;
}}
QLabel[role="heading"] {{
    font-size: {FONT_SECTION:g}pt;
    font-weight: 600;
}}
QLabel[role="kpi"] {{
    font-size: {FONT_TITLE:g}pt;
    font-weight: 700;
}}
QLabel[role="kpi_profit"] {{
    color: {t.profit};
    font-size: {FONT_TITLE:g}pt;
    font-weight: 700;
}}
QLabel[role="kpi_loss"] {{
    color: {t.loss};
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
QLabel[role="empty_icon"] {{
    color: {t.text_secondary};
    font-size: {FONT_TITLE:g}pt;
}}
QFrame[role="row"] {{
    background-color: transparent;
    border-top: 1px solid {t.border};
}}
QLabel[role="badge"] {{
    color: {t.warning};
    background-color: {t.warning_soft};
    border-radius: {RADIUS_CONTROL}px;
    font-size: {FONT_SMALL:g}pt;
    font-weight: 700;
    padding: 2px {SPACE}px;
}}
{_chip_rules(t)}
QPushButton {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.control_border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px {SPACE_XL}px;
}}
QPushButton:hover {{
    background-color: {t.hover};
    border-color: {t.accent};
}}
QPushButton:pressed {{
    background-color: {t.accent_soft};
}}
QPushButton:focus {{
    border: 1px solid {t.accent};
}}
QPushButton:disabled {{
    color: {t.text_secondary};
    background-color: {t.surface};
    border-color: {t.border};
}}
QPushButton[nav="true"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px {SPACE_WIDE}px;
    text-align: left;
}}
QPushButton[nav="true"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[nav="true"]:checked {{
    background-color: {t.accent_soft};
    color: {t.accent};
    font-weight: 600;
}}
QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    padding: 6px {SPACE_WIDE}px;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {t.hover};
    border-color: {t.border};
}}
QPushButton[variant="primary"] {{
    background-color: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    border-color: {t.text};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {t.accent_soft};
    color: {t.text_secondary};
    border-color: {t.accent_soft};
}}
QPushButton[variant="danger"] {{
    background-color: {t.loss_soft};
    color: {t.loss};
    border: 1px solid {t.loss};
    font-weight: 600;
}}
QPushButton[variant="danger"]:hover {{
    background-color: {t.loss_soft};
    border-color: {t.text};
}}
QPushButton[variant="danger"]:disabled {{
    color: {t.text_secondary};
    background-color: transparent;
    border-color: {t.border};
}}
QPushButton[nav="true"]:focus, QPushButton[variant="ghost"]:focus {{
    border: 1px solid {t.accent};
}}
QPushButton[variant="primary"]:focus, QPushButton[variant="danger"]:focus {{
    border: 1px solid {t.text};
}}
QLineEdit, QAbstractSpinBox, QComboBox {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.control_border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 6px {SPACE}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{
    border-color: {t.text_secondary};
}}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{
    border: 1px solid {t.accent};
}}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{
    color: {t.text_secondary};
    background-color: {t.surface};
    border-color: {t.border};
}}
QComboBox QAbstractItemView {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
    outline: 0;
}}
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: {SPACE}px;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px;
}}
QCheckBox:focus, QRadioButton:focus {{
    border: 1px solid {t.accent};
}}
QTextEdit, QPlainTextEdit, QTextBrowser {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QListWidget {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
    outline: 0;
}}
QListWidget::item {{
    border-radius: 6px;
    padding: {SPACE}px;
}}
QListWidget::item:hover {{
    background-color: {t.hover};
}}
QListWidget::item:selected {{
    background-color: {t.accent};
    color: {t.accent_text};
}}
QTableView, QTreeView {{
    background-color: {t.card};
    alternate-background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    gridline-color: {t.border};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
    outline: 0;
}}
QTableView:focus, QTreeView:focus, QListWidget:focus, QTextEdit:focus, QPlainTextEdit:focus,
QTextBrowser:focus {{
    border-color: {t.accent};
}}
QHeaderView {{
    background-color: transparent;
    border: none;
}}
QHeaderView::section {{
    background-color: {t.surface};
    color: {t.text_secondary};
    border: none;
    border-bottom: 1px solid {t.border};
    padding: 6px 10px;
    font-size: {FONT_SMALL:g}pt;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {t.surface};
    border: none;
}}
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {t.border};
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {t.text_secondary};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px {SPACE_XL}px;
    margin-right: 4px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {t.text};
}}
QTabBar::tab:selected {{
    color: {t.text};
    border-bottom: 2px solid {t.accent};
}}
QTabWidget#SettingsTabs QTabBar::tab:first {{
    margin-left: {SPACE_XL}px;
}}
QGroupBox {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: {RADIUS}px;
    margin-top: 14px;
    padding: {SPACE_WIDE}px;
    padding-top: {SPACE_XL}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {SPACE_WIDE}px;
    padding: 0 4px;
    color: {t.text_secondary};
    font-weight: 600;
}}
QProgressBar {{
    background-color: {t.hover};
    color: {t.text};
    border: none;
    border-radius: 4px;
    min-height: 16px;
    font-size: {FONT_SMALL:g}pt;
    text-align: center;
}}
QProgressBar[slim="true"] {{
    min-height: 6px;
    max-height: 6px;
}}
QProgressBar::chunk {{
    background-color: {t.accent};
    border-radius: 4px;
}}
QProgressBar[tone="warning"]::chunk {{
    background-color: {t.warning};
}}
QProgressBar[tone="loss"]::chunk {{
    background-color: {t.loss};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background-color: {t.border_strong};
    border-radius: 3px;
    min-height: 32px;
}}
QScrollBar::handle:horizontal {{
    background-color: {t.border_strong};
    border-radius: 3px;
    min-width: 32px;
}}
QScrollBar::handle:hover {{
    background-color: {t.text_secondary};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
    border: none;
    background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
QSplitter::handle {{
    background-color: {t.border};
}}
QMenu {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px {SPACE_XL}px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {t.accent_soft};
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
    border: 1px solid {t.border_strong};
}}
QToolTip {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    padding: 4px {SPACE}px;
}}
"""
