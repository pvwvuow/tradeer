"""Design tokens in one place; the Qt stylesheet is generated from them (spec F1).

Accessibility (spec F1, Phase 16b): every text color meets WCAG AA (4.5:1) on every surface it
is drawn on, controls have a border of at least 3:1 and a visible keyboard focus ring, and
`contrast_failures` lists any pair that misses (a unit test keeps the list empty).

0.26 look (asked for on 8 October 2026, "a new, beautiful and professional interface; this
one looks old, rework it"): a new palette and a calmer, flatter build.

- Ink and violet: near-black ink surfaces tinted toward violet (light theme: soft paper
  greys), one violet accent for everything the user can act on. Green, red and amber are
  kept for results and warnings only, so the accent never reads as a gain or a loss.
- Three layers instead of gradients: the window chrome (top bar, sidebar, status bar) on
  `surface`, the page on `bg`, the content on `card` with a hairline border. Only the
  primary button and the logo keep a gradient.
- Fields sit recessed in the page color and light up in the card color with a violet edge
  when focused; tables lose the zebra stripes for row lines and a row highlight; tabs are a
  segmented control; the sidebar's current page is a violet pill.
- Numbers (the Dashboard figures) use Bahnschrift, the DIN-style face Windows ships for
  instrument panels; it falls back to the window font where it is missing.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.core.ui_prefs import ThemeName

SPACE = 8
SPACE_WIDE = 12
SPACE_XL = 16
RADIUS = 16
RADIUS_CONTROL = 10
RADIUS_SMALL = 6
CONTROL_HEIGHT = 18  # the least inner height of buttons and inputs (px); table cells stay 30
# Font sizes in points (12, 14, 18 and 24 px at 96 dpi). A size in px makes every widget font
# report point size -1, and Qt then warned "QFont::setPointSize: Point size <= 0" hundreds of
# times a minute on a real PC.
FONT_SMALL = 9
FONT_BODY = 10.5
FONT_SECTION = 13.5
FONT_TITLE = 19
FONT_KPI = 21
NUMBER_FONT = "Bahnschrift"  # Windows 10 and 11; Qt uses the window font where it is missing
AA_TEXT = 4.5  # WCAG 2.1 AA, normal text
AA_NON_TEXT = 3.0  # WCAG 2.1 AA, control borders and the focus ring
TEXT_COLORS = ("text", "text_secondary", "accent", "profit", "loss", "warning")
SURFACES = ("bg", "surface", "card")
# Hover and selection fills only ever carry the plain text colors (selection-color is `text`).
FILLS = ("hover", "accent_soft")
PLAIN_TEXT = ("text", "text_secondary")
WHITE = "#FFFFFF"
BLACK = "#000000"


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
    bg="#08080C",
    surface="#0E0E14",
    card="#14141C",
    border="#22222E",
    text="#EDECF4",
    text_secondary="#9C9AB0",
    accent="#8E80FF",
    accent_text="#0A0912",
    profit="#2AD07A",
    loss="#FF5C5C",
    warning="#F5A524",
    hover="#1C1B27",
    accent_soft="#1F1B40",
    border_strong="#30303F",
    profit_soft="#0C2A1B",
    loss_soft="#2E1316",
    warning_soft="#2D2210",
    control_border="#6F6D86",
)

LIGHT = ThemeTokens(
    name=ThemeName.LIGHT,
    bg="#F3F3F7",
    surface="#FAFAFC",
    card="#FFFFFF",
    border="#E2E1EA",
    text="#121119",
    text_secondary="#5C5A6D",
    accent="#5A43E3",
    accent_text="#FFFFFF",
    profit="#11793A",
    loss="#C22525",
    warning="#9F4A06",
    hover="#EDECF3",
    accent_soft="#EEEBFF",
    border_strong="#CFCDDA",
    profit_soft="#E8F6EE",
    loss_soft="#FCEBEB",
    warning_soft="#FBEFE0",
    control_border="#7C7990",
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

    card_low: str  # the content layer (0.26: flat, the card color itself)
    bar_low: str  # the window chrome (0.26: flat, the surface color)
    accent_top: str  # the light end of the primary button gradient
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
            accent_top=mix(t.accent, WHITE, 0.16),
            accent_hover=mix(t.accent, WHITE, 0.26),
            row_hover=mix(t.card, t.hover, 0.8),
            track=mix(t.bg, t.border, 0.6),
            field=mix(t.bg, t.card, 0.35),
        )
    return Shades(
        card_low=t.card,
        bar_low=t.surface,
        accent_top=mix(t.accent, WHITE, 0.06),
        accent_hover=mix(t.accent, BLACK, 0.1),
        row_hover=mix(t.card, t.hover, 0.7),
        track=t.hover,
        field=t.surface,
    )


def _gradient(top: str, bottom: str) -> str:
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom})"


def _chip_rules(tokens: ThemeTokens) -> str:
    rules = []
    for tone in CHIP_TONES:
        text, fill = chip_colors(tokens, tone)
        rules.append(
            f'QLabel[chip="{tone}"] {{ color: {text}; background-color: {fill}; '
            f"border-radius: 12px; font-size: {FONT_SMALL:g}pt; font-weight: 700; "
            "padding: 4px 12px; }",
        )
    return "\n".join(rules)


def _indicator_rules(tokens: ThemeTokens) -> str:
    """Check boxes and radio buttons drawn without images: a filled box or a ring."""
    t = tokens
    return f"""
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px;
    height: 18px;
    background-color: {t.card};
    border: 1px solid {t.control_border};
}}
QCheckBox::indicator {{
    border-radius: 6px;
}}
QRadioButton::indicator {{
    border-radius: 10px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}
QCheckBox::indicator:checked {{
    background-color: {t.accent};
    border: 5px solid {t.accent_soft};
}}
QCheckBox::indicator:indeterminate {{
    background-color: {t.accent_soft};
    border: 1px solid {t.accent};
}}
QRadioButton::indicator:checked {{
    background-color: {t.card};
    border: 6px solid {t.accent};
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
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {t.text_secondary};
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
    font-family: "{NUMBER_FONT}";
    font-size: {FONT_KPI:g}pt;
    font-weight: 600;
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
    background-color: {_gradient(s.accent_top, t.accent)};
    color: {t.accent_text};
    border-radius: {RADIUS_CONTROL}px;
    font-family: "{NUMBER_FONT}";
    font-size: {FONT_BODY:g}pt;
    font-weight: 700;
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
    font-weight: 700;
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
    font-size: {FONT_SMALL:g}pt;
    font-weight: 700;
    padding: {SPACE_WIDE}px {SPACE_WIDE}px 4px {SPACE_WIDE}px;
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
    background-color: {t.warning_soft};
    border-radius: 12px;
    font-size: {FONT_SMALL:g}pt;
    font-weight: 700;
    padding: 3px {SPACE_WIDE}px;
}}
{_chip_rules(t)}
QPushButton {{
    background-color: {t.hover};
    color: {t.text};
    border: 1px solid {t.control_border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 8px {SPACE_XL}px;
    min-height: {CONTROL_HEIGHT}px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {t.accent_soft};
    border-color: {t.accent};
}}
QPushButton:pressed {{
    background-color: {t.accent_soft};
    border-color: {t.text};
}}
QPushButton:checked {{
    background-color: {t.accent_soft};
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
    padding: 9px {SPACE_WIDE}px;
    min-height: 20px;
    text-align: left;
    font-weight: 500;
}}
QPushButton[nav="true"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[nav="true"]:checked {{
    background-color: {t.accent_soft};
    color: {t.accent};
    border: 1px solid {t.accent_soft};
    font-weight: 700;
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
    background-color: {_gradient(s.accent_top, t.accent)};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 700;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {_gradient(s.accent_hover, s.accent_top)};
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
    background-color: {t.loss_soft};
    color: {t.loss};
    border: 1px solid {t.loss};
    font-weight: 700;
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
    padding: 7px 12px;
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
    padding: 9px 10px;
    font-size: {FONT_SMALL:g}pt;
    font-weight: 700;
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
    background-color: {t.surface};
    color: {t.text_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px {SPACE_XL}px;
    margin: 8px 6px 8px 0;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {t.text};
    background-color: {t.hover};
}}
QTabBar::tab:selected {{
    color: {t.accent};
    background-color: {t.accent_soft};
    border: 1px solid {t.accent};
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
    font-weight: 700;
}}
QProgressBar {{
    background-color: {s.track};
    color: {t.text};
    border: none;
    border-radius: 6px;
    min-height: 16px;
    font-size: {FONT_SMALL:g}pt;
    text-align: center;
}}
QProgressBar[slim="true"] {{
    min-height: 6px;
    max-height: 6px;
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background-color: {t.accent};
    border-radius: 6px;
}}
QProgressBar[slim="true"]::chunk {{
    border-radius: 3px;
}}
QProgressBar[tone="warning"]::chunk {{
    background-color: {t.warning};
}}
QProgressBar[tone="loss"]::chunk {{
    background-color: {t.loss};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background-color: {s.track};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background-color: {t.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0;
    background-color: {t.text};
    border: 2px solid {t.accent};
    border-radius: 8px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 3px 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 9px;
    margin: 2px 3px;
}}
QScrollBar::handle:vertical {{
    background-color: {t.border_strong};
    border-radius: 4px;
    min-height: 40px;
}}
QScrollBar::handle:horizontal {{
    background-color: {t.border_strong};
    border-radius: 4px;
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
    padding: 6px;
}}
QMenu::item {{
    padding: 8px {SPACE_XL}px;
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
    min-height: 30px;
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
    padding: 7px {SPACE_WIDE}px;
}}
"""
