"""Design tokens in one place; the Qt stylesheet is generated from them (spec F1).

Accessibility (spec F1, Phase 16b): words meet WCAG AA (4.5:1) on every surface they are
drawn on, keyboard focus is always visible, and `contrast_failures` lists any pair that misses
its minimum (a unit test keeps the list empty).

0.33 look, No Curve v2 (the owner's design of 9 October 2026, docs/NOCURVE_V2.md):

- Navy ink on a cool grey page. The light theme is the default (`.fa` in the design); the dark
  theme is `.fa.lt`. The ink itself is the accent: the primary button, the current page in
  the sidebar, the chosen segment and the PAPER tag are drawn inverted (page color on ink).
  The design's gold accent is defined there but never drawn, so it is left out here.
- Every color is the design's own value, unchanged. Two of them sit a hair under AA on the
  page: profit 4.37:1 and warning 4.41:1 (light theme), so result colors are held to 4.2:1
  (`TONE_TEXT`) instead of 4.5. The design's strong border (#AEB9CA, about 1.7:1) is a
  hairline, not the only cue of a control, so it is not held to 3:1; the focus ring (the ink)
  is.
- Flat and drawn with lines: 8 px boxes, 6 px buttons, 4 px sidebar rows, 3 px tags; tags are
  outlined (transparent inside), only the inverted one is filled.
- Two faces: Vazirmatn (or the window font) for words, IBM Plex Mono for every figure and
  caption. Sizes are the design's pixels, written in points (`px`) so widget fonts never
  report point size -1.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.core.ui_prefs import ThemeName

PT_PER_PX = 0.75  # 96 dpi: 1 px is 0.75 pt


def px(pixels: float) -> float:
    """A design size in pixels, as the point size Qt fonts are given."""
    return pixels * PT_PER_PX


SPACE = 8
SPACE_WIDE = 12
SPACE_XL = 16
RADIUS = 8  # `.bx` boxes and cards
RADIUS_CONTROL = 6  # `.btn`, `.seg`, `.ib`, `.in`
RADIUS_SMALL = 4  # `.nv` sidebar rows
RADIUS_TAG = 3  # `.tag`
CONTROL_HEIGHT = 18  # the least inner height of buttons and inputs (px); table cells stay 30
# The design's control heights in pixels (the frame and the pages build them in 20b on).
BUTTON_HEIGHT = 44  # `.btn`
SEGMENT_HEIGHT = 32  # `.seg > *`
ICON_BUTTON = 36  # `.ib`
NAV_HEIGHT = 38  # `.nv`
INPUT_HEIGHT = 40  # `.in`
ROW_HEIGHT = 52  # `.rw`
HEADER_ROW_HEIGHT = 34  # `.rw.h`
CAPTION_SPACING = 0.8  # `.cap` letter spacing in px (QFont.setLetterSpacing, not QSS)
# Font sizes: the design's pixels in points.
FONT_SMALL = px(11)  # `.cap`, `.tag`, table headers
FONT_LABEL = px(12)  # `.lbl`, sublines
FONT_ROW = px(13)  # `.rw` table rows, `.seg` choices
FONT_BODY = px(14)  # words, `.btn`, `.nv`
FONT_SECTION = px(14)  # `.sec b` (600)
FONT_TITLE = px(26)  # the page title
FONT_KPI = px(27)  # dashboard KPI values
WORD_FONT = "Vazirmatn"  # the design's word face (Persian and English)
NUMBER_FONT = "IBM Plex Mono"  # the design's face; Windows falls back to the next ones
NUMBER_FONTS: tuple[str, ...] = (NUMBER_FONT, "Cascadia Mono", "Consolas")  # substitutions
AA_TEXT = 4.5  # WCAG 2.1 AA, normal text
AA_NON_TEXT = 3.0  # WCAG 2.1 AA, the focus ring and the ink accent
TONE_TEXT = 4.2  # profit, loss and warning: the design's exact colors (see the docstring)
PLAIN_TEXT = ("text", "text_secondary")
WORD_COLORS = (*PLAIN_TEXT, "accent")
TONE_COLORS = ("profit", "loss", "warning")
TEXT_COLORS = (*WORD_COLORS, *TONE_COLORS)
SURFACES = ("bg", "surface", "card")
# Hover and selection fills only ever carry the plain text colors (selection-color is `text`).
FILLS = ("hover", "accent_soft")
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
    bg: str  # --bg, the page
    surface: str  # --sf, cards, the ticker, the status bar
    card: str  # --sf as well: the content layer
    border: str  # --bd, hairlines and row separators
    text: str  # --tx, words and the ink
    text_secondary: str  # --t2, captions and muted words
    accent: str  # the ink (--tx): selected and primary controls are drawn inverted
    accent_text: str  # words on the ink (--bg)
    profit: str  # --pf
    loss: str  # --ls
    warning: str  # --wn
    hover: str  # --hv
    accent_soft: str  # pressed and selected fills (--hv)
    border_strong: str  # --bs, control and tag outlines, the table header rule
    profit_soft: str  # --ps over the surface
    loss_soft: str  # --lss over the surface (the hatch, a failed step)
    warning_soft: str  # --ws over the surface
    control_border: str  # the border of buttons and inputs (--bs)

    def colors(self) -> dict[str, str]:
        return {key: value for key, value in asdict(self).items() if key != "name"}

    @property
    def dark(self) -> bool:
        return self.name is not ThemeName.LIGHT


# `.fa` in the design: the default. Soft fills are the design's rgba at 12% over white.
LIGHT = ThemeTokens(
    name=ThemeName.LIGHT,
    bg="#EDF0F4",
    surface="#FFFFFF",
    card="#FFFFFF",
    border="#D5DBE5",
    text="#0F1B2D",
    text_secondary="#52607A",
    accent="#0F1B2D",
    accent_text="#EDF0F4",
    profit="#1B7F53",
    loss="#B93636",
    warning="#9A6310",
    hover="#E3E8EF",
    accent_soft="#E3E8EF",
    border_strong="#AEB9CA",
    profit_soft="#E4F0EA",
    loss_soft="#F7E7E7",
    warning_soft="#F3ECE2",
    control_border="#AEB9CA",
)

# `.fa.lt` in the design. Soft fills are its rgba (14%, 15%, 14%) over the surface.
DARK = ThemeTokens(
    name=ThemeName.DARK,
    bg="#0A0E13",
    surface="#121921",
    card="#121921",
    border="#243040",
    text="#E8EDF4",
    text_secondary="#9AA8BB",
    accent="#E8EDF4",
    accent_text="#0A0E13",
    profit="#4CC38A",
    loss="#FF7D7D",
    warning="#E3B04B",
    hover="#1A2430",
    accent_soft="#1A2430",
    border_strong="#37485F",
    profit_soft="#1A3130",
    loss_soft="#36282F",
    warning_soft="#2F2E27",
    control_border="#37485F",
)

DEFAULT = LIGHT  # the design opens in the light theme
CHIP_TONES: tuple[str, ...] = ("neutral", "accent", "profit", "loss", "warning")


def chip_colors(tokens: ThemeTokens, tone: str) -> tuple[str, str]:
    """(text, background) of a tag (`.tag`).

    Tags are outlined and transparent inside, so the background is the surface they sit on;
    only the accent tag (`.tag.in`) is filled: page color on ink.
    """
    pairs = {
        "neutral": (tokens.text_secondary, tokens.surface),
        "accent": (tokens.accent_text, tokens.accent),
        "profit": (tokens.profit, tokens.surface),
        "loss": (tokens.loss, tokens.surface),
        "warning": (tokens.warning, tokens.surface),
    }
    return pairs[tone]


def chip_edge(tokens: ThemeTokens, tone: str) -> str:
    """The outline of a tag: the strong border, the ink, or the tone's own color."""
    if tone == "neutral":
        return tokens.border_strong
    if tone == "accent":
        return tokens.accent
    return chip_colors(tokens, tone)[0]


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
    pairs = [(fg, bg, AA_TEXT) for fg in WORD_COLORS for bg in SURFACES]
    pairs += [(fg, bg, TONE_TEXT) for fg in TONE_COLORS for bg in SURFACES]
    pairs += [(fg, bg, AA_TEXT) for fg in PLAIN_TEXT for bg in FILLS]
    pairs.append(("accent_text", "accent", AA_TEXT))
    pairs += [(tone, f"{tone}_soft", TONE_TEXT) for tone in TONE_COLORS]
    pairs += [("accent", bg, AA_NON_TEXT) for bg in SURFACES]
    return pairs


def contrast_failures(tokens: ThemeTokens) -> list[str]:
    """The pairs below their minimum, e.g. "loss on hover: 4.22 < 4.5"."""
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
    bar_low: str  # the header and the sidebar: the page itself in the design
    accent_top: str  # the primary button at rest (flat, the ink itself)
    accent_hover: str
    row_hover: str  # the table row under the mouse (--hv)
    track: str  # scroll bar and progress bar tracks
    field: str  # inputs at rest (transparent in the design: the surface they sit on)


def shades(tokens: ThemeTokens) -> Shades:
    t = tokens
    return Shades(
        card_low=t.card,
        bar_low=t.bg,
        accent_top=t.accent,
        accent_hover=mix(t.accent, WHITE, 0.5 if t.dark else 0.18),
        row_hover=t.hover,
        track=t.hover,
        field=t.card,
    )


def _chip_rules(tokens: ThemeTokens) -> str:
    """Tags (`.tag`): 1 px outline, 3 px radius, 11 px mono; the accent tag is inverted."""
    rules = []
    for tone in CHIP_TONES:
        text, fill = chip_colors(tokens, tone)
        background = fill if tone == "accent" else "transparent"
        rules.append(
            f'QLabel[chip="{tone}"] {{ color: {text}; background-color: {background}; '
            f"border: 1px solid {chip_edge(tokens, tone)}; border-radius: {RADIUS_TAG}px; "
            f"font-family: {number_family()}; font-size: {FONT_SMALL:g}pt; "
            "font-weight: 400; padding: 1px 7px; }",
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
    border-radius: {RADIUS_TAG}px;
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
    font-size: {FONT_BODY:g}pt;
    font-weight: 600;
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
    font-weight: 600;
}}
QLabel[role="subtitle"] {{
    color: {t.text_secondary};
}}
QLabel[role="crumb"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 400;
}}
QLabel[role="heading"] {{
    font-size: {FONT_SECTION:g}pt;
    font-weight: 600;
}}
{_kpi_rule("kpi", t.text)}
{_kpi_rule("kpi_profit", t.profit)}
{_kpi_rule("kpi_loss", t.loss)}
QLabel[role="section"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 400;
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
    border-radius: {RADIUS_TAG}px;
    font-family: {number_family()};
    font-size: {FONT_SMALL:g}pt;
    font-weight: 400;
    padding: 1px 7px;
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
    border-color: {t.text_secondary};
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
    background-color: transparent;
    border-color: {t.border};
}}
QPushButton[nav="true"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-radius: {RADIUS_SMALL}px;
    padding: 8px {SPACE_WIDE}px;
    min-height: 20px;
    text-align: left;
    font-weight: 400;
}}
QPushButton[nav="true"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[nav="true"]:checked {{
    background-color: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 500;
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
    padding: 6px {SPACE_WIDE}px;
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
    background-color: {t.bg};
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
    border-radius: {RADIUS}px;
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
    font-weight: 400;
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
    font-weight: 400;
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
    background-color: {t.surface};
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
