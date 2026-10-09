"""Design tokens in one place; the Qt stylesheet is generated from them (spec F1).

Accessibility (spec F1, Phase 16b): every text color meets WCAG AA (4.5:1) on every surface it
is drawn on, controls have a border of at least 3:1 and a visible keyboard focus ring, and
`contrast_failures` lists any pair that misses (a unit test keeps the list empty).

0.31 look, Workstation v2 (the owner's own design, 9 October 2026, docs/UI_V2.md):

- Ink and cream: a green-black ink page with cream text (light theme: cream paper with ink
  text). The ink itself is the accent: the primary button, the current page in the sidebar,
  checked buttons and progress bars are drawn inverted (cream on ink, or ink on cream).
  Green, coral and amber stay for results and warnings only.
- Flat and drawn with lines: no gradients, small radii (8 px cards, 6 px controls, 4 px
  tags), hairline borders, tabs underlined instead of pills, status chips as outlined tags,
  sidebar pages numbered 01 to 14.
- Two faces: the window font for words, a monospaced face for every figure and caption
  (IBM Plex Mono where installed, then Cascadia Mono and Consolas, which Windows ships), so
  columns of numbers line up.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.core.ui_prefs import ThemeName

SPACE = 8
SPACE_WIDE = 12
SPACE_XL = 16
RADIUS = 8
RADIUS_CONTROL = 6
RADIUS_SMALL = 4
CONTROL_HEIGHT = 18  # the least inner height of buttons and inputs (px); table cells stay 30
# Font sizes in points (12, 14, 18 and 24 px at 96 dpi). A size in px makes every widget font
# report point size -1, and Qt then warned "QFont::setPointSize: Point size <= 0" hundreds of
# times a minute on a real PC.
FONT_SMALL = 9
FONT_BODY = 10.5
FONT_SECTION = 11
FONT_TITLE = 18
FONT_KPI = 18
NUMBER_FONT = "IBM Plex Mono"  # the design's face; Windows falls back to the next ones
NUMBER_FONTS: tuple[str, ...] = (NUMBER_FONT, "Cascadia Mono", "Consolas")  # substitutions
AA_TEXT = 4.5  # WCAG 2.1 AA, normal text
AA_NON_TEXT = 3.0  # WCAG 2.1 AA, control borders and the focus ring
TEXT_COLORS = ("text", "text_secondary", "accent", "profit", "loss", "warning")
SURFACES = ("bg", "surface", "card")
# Hover and selection fills only ever carry the plain text colors (selection-color is `text`).
FILLS = ("hover", "accent_soft")
PLAIN_TEXT = ("text", "text_secondary")
WHITE = "#FFFFFF"
BLACK = "#000000"


def number_family() -> str:
    """The QSS font-family of the number face: one name, quoted.

    The fallbacks are not listed in the stylesheet (a list of families that are all missing
    is the suspect of an aborted Linux offscreen test run); `app.ui.style.ui_font` registers
    them as Qt font substitutions instead.
    """
    return f'"{NUMBER_FONT}"'


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

    @property
    def dark(self) -> bool:
        return self.name is not ThemeName.LIGHT


DARK = ThemeTokens(
    name=ThemeName.DARK,
    bg="#0E100F",
    surface="#121513",
    card="#161A18",
    border="#252B28",
    text="#ECE8DC",
    text_secondary="#A2ACA4",
    accent="#ECE8DC",
    accent_text="#0E100F",
    profit="#5AD19A",
    loss="#F2795F",
    warning="#D9B45A",
    hover="#1C211E",
    accent_soft="#262C28",
    border_strong="#364039",
    profit_soft="#12271D",
    loss_soft="#2C1814",
    warning_soft="#282112",
    control_border="#6B756D",
)

LIGHT = ThemeTokens(
    name=ThemeName.LIGHT,
    bg="#F1EEE6",
    surface="#EBE7DD",
    card="#F8F6F0",
    border="#D9D4C7",
    text="#1B1C19",
    text_secondary="#565C53",
    accent="#1B1C19",
    accent_text="#F8F6F0",
    profit="#0B6E49",
    loss="#A8341E",
    warning="#735709",
    hover="#E5E1D6",
    accent_soft="#DEDACD",
    border_strong="#B8B2A2",
    profit_soft="#DCEBE1",
    loss_soft="#F4DFD8",
    warning_soft="#EFE6CD",
    control_border="#7E7869",
)

CHIP_TONES: tuple[str, ...] = ("neutral", "accent", "profit", "loss", "warning")


def chip_colors(tokens: ThemeTokens, tone: str) -> tuple[str, str]:
    """(text, fill) of a status chip; the chip's outline is drawn in its text color."""
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


def mix(color: str, other: str, amount: float) -> str:
    """`color` moved `amount` (0 to 1) of the way to `other`, as uppercase hex."""
    share = min(1.0, max(0.0, amount))
    first = [int(color[index : index + 2], 16) for index in (1, 3, 5)]
    second = [int(other[index : index + 2], 16) for index in (1, 3, 5)]
    channels = [round(a + (b - a) * share) for a, b in zip(first, second, strict=True)]
    return "#" + "".join(f"{value:02X}" for value in channels)


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


@dataclass(frozen=True)
class Shades:
    """Colors the stylesheet derives from the tokens (never carry text alone)."""

    card_low: str  # the content layer (flat, the card color itself)
    bar_low: str  # the window chrome (flat, the surface color)
    accent_top: str  # the primary button at rest (0.31: flat, the ink itself)
    accent_hover: str
    row_hover: str  # the table row under the mouse
    track: str  # scroll bar and progress bar tracks
    field: str  # inputs at rest: recessed in the page color


def shades(tokens: ThemeTokens) -> Shades:
    t = tokens
    if t.dark:
        return Shades(
            card_low=t.card,
            bar_low=t.surface,
            accent_top=t.accent,
            accent_hover=mix(t.accent, WHITE, 0.5),
            row_hover=mix(t.card, t.hover, 0.8),
            track=mix(t.bg, t.border, 0.8),
            field=mix(t.bg, t.card, 0.35),
        )
    return Shades(
        card_low=t.card,
        bar_low=t.surface,
        accent_top=t.accent,
        accent_hover=mix(t.accent, WHITE, 0.18),
        row_hover=mix(t.card, t.hover, 0.7),
        track=t.hover,
        field=t.card,
    )


def _chip_rules(tokens: ThemeTokens) -> str:
    """Status chips as outlined tags: the tone's text and edge on its soft fill."""
    rules = []
    for tone in CHIP_TONES:
        text, fill = chip_colors(tokens, tone)
        rules.append(
            f'QLabel[chip="{tone}"] {{ color: {text}; background-color: {fill}; '
            f"border: 1px solid {text}; border-radius: {RADIUS_SMALL}px; "
            f"font-family: {number_family()}; font-size: {FONT_SMALL:g}pt; "
            "font-weight: 600; padding: 3px 9px; }",
        )
    return "\n".join(rules)


def _indicator_rules(tokens: ThemeTokens) -> str:
    """Check boxes and radio buttons drawn without images: a filled box or a ring."""
    t = tokens
    return f"""
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    background-color: {t.card};
    border: 1px solid {t.control_border};
}}
QCheckBox::indicator {{
    border-radius: {RADIUS_SMALL}px;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}
QCheckBox::indicator:checked {{
    background-color: {t.accent};
    border: 4px solid {t.accent_soft};
}}
QCheckBox::indicator:indeterminate {{
    background-color: {t.accent_soft};
    border: 1px solid {t.accent};
}}
QRadioButton::indicator:checked {{
    background-color: {t.card};
    border: 5px solid {t.accent};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {t.surface};
    border-color: {t.border};
}}
"""


def _arrow_rules(tokens: ThemeTokens) -> str:
    """Drop-down and spin box arrows as small triangles (no image files needed)."""
    t = tokens
    return f"""
QComboBox {{
    padding-right: 30px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 28px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_secondary};
}}
QComboBox::down-arrow:on, QComboBox::down-arrow:hover {{
    border-top-color: {t.accent};
}}
QAbstractSpinBox {{
    padding-right: 24px;
}}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border;
    width: 22px;
    border: none;
    background: transparent;
}}
QAbstractSpinBox::up-button {{
    subcontrol-position: top right;
}}
QAbstractSpinBox::down-button {{
    subcontrol-position: bottom right;
}}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
    background-color: {t.hover};
    border-radius: {RADIUS_SMALL}px;
}}
QAbstractSpinBox::up-arrow {{
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {t.text_secondary};
}}
QAbstractSpinBox::down-arrow {{
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_secondary};
}}
QAbstractSpinBox::up-arrow:hover {{
    border-bottom-color: {t.accent};
}}
QAbstractSpinBox::down-arrow:hover {{
    border-top-color: {t.accent};
}}
"""


def _kpi_rule(role: str, color: str) -> str:
    return f"""
QLabel[role="{role}"] {{
    color: {color};
    font-family: {number_family()};
    font-size: {FONT_KPI:g}pt;
    font-weight: 500;
}}"""


def build_qss(tokens: ThemeTokens) -> str:
    t = tokens
    s = shades(t)
    return f"""
QWidget {{
    background-color: {t.bg};
    color: {t.text};
    font-size: {FONT_BODY:g}pt;
}}
QFrame#TopBar {{
    background-color: {s.bar_low};
    border: none;
    border-bottom: 1px solid {t.border};
}}
QFrame#Sidebar {{
    background-color: {s.bar_low};
    border: none;
    border-right: 1px solid {t.border};
}}
QFrame#Sidebar QLabel {{
    background: transparent;
}}
QFrame[role="card"] {{
    background-color: {s.card_low};
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
    font-weight: 700;
}}
QLabel[role="logo"] {{
    background-color: transparent;
    color: {t.text};
    border: 2px solid {t.text};
    border-radius: {RADIUS_SMALL}px;
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 600;
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
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 500;
}}
QLabel[role="heading"] {{
    font-size: {FONT_SECTION:g}pt;
    font-weight: 700;
}}
{_kpi_rule("kpi", t.text)}
{_kpi_rule("kpi_profit", t.profit)}
{_kpi_rule("kpi_loss", t.loss)}
QLabel[role="section"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 500;
    padding: {SPACE_WIDE}px {SPACE_WIDE}px 4px {SPACE_WIDE}px;
    border-bottom: 1px solid {t.border};
    margin-bottom: 4px;
}}
QLabel[role="muted"] {{
    color: {t.text_secondary};
}}
QLabel[role="status"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
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
    color: {t.accent};
    font-size: {FONT_TITLE:g}pt;
}}
QFrame[role="row"] {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {t.border};
}}
QLabel[role="badge"] {{
    color: {t.warning};
    background-color: transparent;
    border: 1px solid {t.warning};
    border-radius: {RADIUS_SMALL}px;
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 600;
    padding: 2px {SPACE}px;
}}
{_chip_rules(t)}
QPushButton {{
    background-color: transparent;
    color: {t.text};
    border: 1px solid {t.control_border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px {SPACE_XL}px;
    min-height: {CONTROL_HEIGHT}px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t.hover};
    border-color: {t.text};
}}
QPushButton:pressed {{
    background-color: {t.accent_soft};
    border-color: {t.text};
}}
QPushButton:checked {{
    background-color: {t.accent};
    color: {t.accent_text};
    border-color: {t.accent};
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
    border: 1px solid transparent;
    border-radius: {RADIUS_CONTROL}px;
    padding: 8px {SPACE_WIDE}px;
    min-height: 20px;
    text-align: left;
    font-weight: 500;
}}
QPushButton[nav="true"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[nav="true"]:checked {{
    background-color: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 600;
}}
QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    padding: 7px {SPACE_WIDE}px;
    font-weight: 500;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {t.hover};
    border-color: {t.border_strong};
}}
QPushButton[variant="primary"] {{
    background-color: {s.accent_top};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {s.accent_hover};
    border-color: {s.accent_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {t.accent};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {t.accent_soft};
    color: {t.text_secondary};
    border-color: {t.accent_soft};
}}
QPushButton[variant="danger"] {{
    background-color: transparent;
    color: {t.loss};
    border: 1px solid {t.loss};
    font-weight: 600;
}}
QPushButton[variant="danger"]:hover {{
    background-color: {t.loss_soft};
    border-color: {t.loss};
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
    border: 2px solid {t.text_secondary};
}}
QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_CONTROL}px;
    padding: 5px;
}}
QToolButton:hover {{
    background-color: {t.hover};
    border-color: {t.border_strong};
}}
QToolButton:focus {{
    border: 1px solid {t.accent};
}}
QLineEdit, QAbstractSpinBox, QComboBox {{
    background-color: {s.field};
    color: {t.text};
    border: 1px solid {t.control_border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 6px 10px;
    min-height: {CONTROL_HEIGHT}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{
    border-color: {t.text_secondary};
}}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{
    border: 1px solid {t.accent};
    background-color: {t.card};
}}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{
    color: {t.text_secondary};
    background-color: {t.surface};
    border-color: {t.border};
}}
{_arrow_rules(t)}
QComboBox QAbstractItemView {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
    outline: 0;
}}
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: {SPACE}px;
    border: 1px solid transparent;
    border-radius: {RADIUS_SMALL}px;
    padding: 3px;
}}
QCheckBox:focus, QRadioButton:focus {{
    border: 1px solid {t.accent};
}}
{_indicator_rules(t)}
QTextEdit, QPlainTextEdit, QTextBrowser {{
    background-color: {s.field};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: {RADIUS_CONTROL}px;
    padding: 8px;
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
    border-radius: {RADIUS_SMALL}px;
    padding: {SPACE}px;
}}
QListWidget::item:hover {{
    background-color: {t.hover};
}}
QListWidget::item:selected {{
    background-color: {t.accent_soft};
    color: {t.text};
}}
QTableView, QTreeView {{
    background-color: {t.card};
    alternate-background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    gridline-color: {t.border};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
    outline: 0;
}}
QTableView::item, QTreeView::item {{
    padding: 0 10px;
    border: none;
    border-bottom: 1px solid {t.border};
}}
QTableView::item:hover, QTreeView::item:hover {{
    background-color: {s.row_hover};
}}
QTableView::item:selected, QTreeView::item:selected {{
    background-color: {t.accent_soft};
    color: {t.text};
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
    background-color: {t.card};
    color: {t.text_secondary};
    border: none;
    border-bottom: 1px solid {t.border_strong};
    padding: 8px 10px;
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 500;
}}
QHeaderView::section:hover {{
    color: {t.text};
}}
QTableCornerButton::section {{
    background-color: {t.card};
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
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px {SPACE_WIDE}px;
    margin: 4px {SPACE}px 0 0;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: {t.text};
    border-bottom: 2px solid {t.border_strong};
}}
QTabBar::tab:selected {{
    color: {t.text};
    border-bottom: 2px solid {t.accent};
    font-weight: 600;
}}
QTabWidget#SettingsTabs QTabBar::tab:first {{
    margin-left: {SPACE_XL}px;
}}
QGroupBox {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: {RADIUS}px;
    margin-top: 18px;
    padding: {SPACE_XL}px;
    padding-top: {SPACE_XL + 4}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {SPACE_XL}px;
    padding: 0 6px;
    color: {t.text_secondary};
    font-family: {number_family()};
    font-weight: 500;
}}
QProgressBar {{
    background-color: {s.track};
    color: {t.text};
    border: none;
    border-radius: 2px;
    min-height: 14px;
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    text-align: center;
}}
QProgressBar[slim="true"] {{
    min-height: 5px;
    max-height: 5px;
    border-radius: 1px;
}}
QProgressBar::chunk {{
    background-color: {t.accent};
    border-radius: 2px;
}}
QProgressBar[slim="true"]::chunk {{
    border-radius: 1px;
}}
QProgressBar[tone="warning"]::chunk {{
    background-color: {t.warning};
}}
QProgressBar[tone="loss"]::chunk {{
    background-color: {t.loss};
}}
QSlider::groove:horizontal {{
    height: 3px;
    background-color: {s.track};
    border-radius: 1px;
}}
QSlider::sub-page:horizontal {{
    background-color: {t.accent};
    border-radius: 1px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0;
    background-color: {t.accent};
    border: 2px solid {t.bg};
    border-radius: 8px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 3px 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 2px 3px;
}}
QScrollBar::handle:vertical {{
    background-color: {t.border_strong};
    border-radius: 3px;
    min-height: 40px;
}}
QScrollBar::handle:horizontal {{
    background-color: {t.border_strong};
    border-radius: 3px;
    min-width: 40px;
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
QSplitter::handle:hover {{
    background-color: {t.accent};
}}
QMenu {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px {SPACE_XL}px;
    border-radius: {RADIUS_SMALL}px;
}}
QMenu::item:selected {{
    background-color: {t.accent_soft};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t.border};
    margin: 4px {SPACE}px;
}}
QStatusBar {{
    background-color: {s.bar_low};
    border-top: 1px solid {t.border};
    min-height: 28px;
    font-family: {number_family()};
}}
QStatusBar::item {{
    border: none;
}}
QDialog#CommandPalette {{
    background-color: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {RADIUS}px;
}}
QToolTip {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: {RADIUS_SMALL}px;
    padding: 6px {SPACE_WIDE}px;
}}
"""
