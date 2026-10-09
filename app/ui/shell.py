"""The window frame, drawn like the owner's No Curve v2 design (docs/NOCURVE_V2.md, 20b).

The pieces the design draws around every page are small painted widgets, so they look the
same as the mockup on every PC: the logo (a square with three bars), the live ticker strip,
the numbered sidebar entries with their badges, the Simple / Advanced segment, buttons that
show a keyboard hint ("Ctrl K") and the status lights.

With Persian chosen the whole frame is Persian and runs right to left, as in the design.
English stays the source text, so a missing word falls back to English instead of breaking.

0.34 (No Curve v2): the sizes are the design's own: sidebar 232 px with 38 px rows, group
rules of 38 px, captions 11 px, tags with a 1 px strong outline, segments 32 px with 14 px
sides, a 36 px theme button, and the ticker moves one full set of quotes every 48 s (the
design's `tick 48s linear infinite`). Its leading cap says LIVE or OFFLINE, never SAMPLE.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from app.ui.i18n import FONT_DIR, load_persian_font
from app.ui.theme import DEFAULT, NUMBER_FONT, ThemeTokens, chip_edge, number_family

TICK_MS = 40  # the ticker repaints every 40 ms
TICK_CYCLE_MS = 48_000  # one full set of quotes goes by in 48 s, like the design
TICKER_HEIGHT = 30
TOP_BAR_HEIGHT = 52
FOOTER_HEIGHT = 32
SIDEBAR_WIDTH = 232
NAV_HEIGHT = 38
GROUP_RULE_HEIGHT = 38  # `padding: 18px 12px 6px` around an 11 px caption
LOGO_SIZE = 28
ICON_BUTTON_SIZE = 36  # the design's `.ib`
HEADER_MARGIN = 20  # `padding: 0 20px`
HEADER_GAP = 18  # between the brand, the view caption and the controls
CLUSTER_GAP = 14  # between the controls at the far end of the header
VIEW_CAPTION = "ADVANCED VIEW"  # the design writes it in English in both languages
CAPTION_PT = 8.25  # 11 px: the design's `cap` (group names, page numbers, crumbs)
SMALL_PT = 8.25  # 11 px: tags and keyboard hints

# The words of the frame (top bar, sidebar, status bar) in Persian, keyed by the English.
SHELL_FA: dict[str, str] = {
    "Dashboard": "داشبورد",
    "Market": "بازار",
    "Signals": "سیگنال‌ها",
    "Positions & Trades": "موقعیت‌ها و معاملات",
    "Analytics": "آمار",
    "Journal": "ژورنال",
    "Backtest": "بک‌تست",
    "Model": "مدل",
    "AI Lab": "آزمایشگاه AI",
    "Strategies": "استراتژی‌ها",
    "Risk": "ریسک",
    "Logs": "لاگ‌ها",
    "Health": "سلامت",
    "Settings": "تنظیمات",
    "Search": "جستجو",
    "Simple": "ساده",
    "Advanced": "پیشرفته",
    "Stop trading": "توقف معاملات",
    "Pages": "صفحه‌ها",
}

# The design's page headers in Persian: (title, one-line summary) by page id.
PAGE_HEADS_FA: dict[str, tuple[str, str]] = {
    "dashboard": ("داشبورد", "وضعیت حساب، ریسک و آخرین تصمیم‌های ربات در یک نگاه"),
    "market": (
        "بازار",
        "تحلیل لحظه‌ای نمادهای فهرست و رویدادهای پیش رو. تحلیل بازار سیگنال نیست.",
    ),
    "signals": (
        "سیگنال‌ها",
        "هر سیگنال با تصمیم و دلیلش. اندازه و محدودیت‌های ریسک موقع تأیید دوباره بررسی می‌شوند.",
    ),
    "positions": (
        "موقعیت‌ها و معاملات",
        "فقط معاملات ربات. معاملات دستی نشان داده نمی‌شوند و ربات به آن‌ها دست نمی‌زند.",
    ),
    "analytics": ("آمار", "عملکرد را از چند زاویه ببینید. نمونه‌ی کم یعنی نتیجه‌ی کم‌اعتبار."),
    "journal": (
        "ژورنال",
        "برای هر معامله بنویسید چه شد و چرا. یادداشت‌ها فقط روی همین دستگاه می‌مانند.",
    ),
    "backtest": (
        "بک‌تست",
        "همان استراتژی، ریسک و اجرای زنده روی تاریخچه‌ی MT5، با اسپرد، کمیسیون، لغزش و "
        "سوآپ. نتیجه‌ی گذشته قول آینده نیست.",
    ),
    "model": (
        "مدل",
        "برآورد احتمال برد با LightGBM، walk-forward پاک‌شده، کالیبراسیون و مقایسه با خط پایه",
    ),
    "ai_lab": (
        "آزمایشگاه AI",
        "گفتگو با یک دستیار که برنامه و معاملات را بررسی می‌کند و فقط پیشنهاد می‌دهد.",
    ),
    "strategies": (
        "استراتژی‌ها",
        "روشن و خاموش کردن، پارامترها و مسیر امن به‌سوی حالت خودکار. تغییرها از کندل "
        "بسته‌ی بعدی اعمال می‌شوند.",
    ),
    "risk": (
        "ریسک",
        "محدودیت‌هایی که موتور روی هر سیگنال اعمال می‌کند و ربات نمی‌تواند از آن‌ها عبور کند",
    ),
    "logs": (
        "لاگ‌ها",
        "یک پوشه برای هر دسته و یک فایل برای هر روز. رمز و توکن پیش از نوشتن ماسک می‌شوند.",
    ),
    "health": ("سلامت", "بررسی هر دقیقه، بودجه‌ی عملکرد، کارگرهای پس‌زمینه و گزارش پایداری"),
    "settings": (
        "تنظیمات",
        "MT5، حالت معامله، ظاهر، اعلان‌ها، همگام‌سازی، به‌روزرسانی و ذخیره‌سازی",
    ),
}

# Status bar texts as templates for `app.ui.i18n.Translator` (fields are translated too).
FRAME_FA: dict[str, str] = {
    "Bot: stopped (kill switch)": "متوقف شده با کلید توقف",
    "Bot: disconnected": "ربات منتظر اتصال",
    "Bot: stopped": "ربات خاموش",
    "Bot: running \u00b7 {count} open": "ربات فعال \u00b7 {count} معامله‌ی باز",
    "Cloud: {state}": "همگام‌سازی ابری: {state}",
    "{text} \u00b7 {count} waiting": "{text} \u00b7 {count} در صف",
    "{text} \u00b7 {count} refused": "{text} \u00b7 {count} رد شده",
    "off": "خاموش",
    "signed out": "خارج شده",
    "up to date": "به‌روز",
    "uploading": "در حال آپلود",
    "offline": "بدون اینترنت",
    "project paused": "پروژه متوقف است",
    "setup needed": "نیاز به راه‌اندازی",
    "error": "خطا",
    "Market closed (weekend)": "بازار بسته (آخر هفته)",
    "Between sessions": "بین سشن‌ها",
    "{session} \u00b7 {change} in {duration}": "{session} \u00b7 {change} تا {duration}",
    "{first} + {second} overlap": "هم‌پوشانی {first} و {second}",
    "{name} session": "{name} باز",
    "{name} opens": "باز شدن {name}",
    "{name} closes": "بسته شدن {name}",
    "Asia": "آسیا",
    "London": "لندن",
    "New York": "نیویورک",
    "{days}d {hours}h": "{days} روز {hours} س",
    "{hours}h {minutes}m": "{hours} س {minutes} د",
    "{minutes}m": "{minutes} د",
}

TEXT_FAMILY = "Vazirmatn"  # the design's face for words, Persian and English alike
NUMBER_FILES = "IBMPlexMono-*.ttf"  # downloaded by scripts/ci/build.ps1 (SIL Open Font License)
_LOADED: dict[str, bool] = {}

_PERSIAN_DIGITS = str.maketrans("0123456789", "".join(chr(0x06F0 + n) for n in range(10)))


def load_frame_fonts() -> bool:
    """Register the bundled faces once: Vazirmatn and IBM Plex Mono (False if not bundled).

    Source runs and the Linux tests have no bundled fonts; Qt then uses the next family.
    """
    if "done" in _LOADED:
        return _LOADED["done"]
    found = load_persian_font()
    for path in sorted(FONT_DIR.glob(NUMBER_FILES)):
        found = QFontDatabase.addApplicationFont(str(path)) >= 0 and found
    _LOADED["done"] = found
    return found


def frame_qss(tokens: ThemeTokens, rtl: bool = False) -> str:
    """The frame's stylesheet, added after `build_qss`: the design's header, sidebar, tags,
    segment, page titles and status bar. Right to left mirrors the side lines and corners.
    """
    t = tokens
    start, end = ("right", "left") if rtl else ("left", "right")
    tags = "\n".join(
        f'QLabel[chip="{tone}"] {{ color: {tone_color(t, tone)}; background-color: '
        f"transparent; border: 1px solid {chip_edge(t, tone)}; border-radius: 3px; "
        f"font-size: {SMALL_PT:g}pt; font-weight: 400; padding: 1px 7px; }}"
        for tone in ("neutral", "profit", "loss", "warning")
    )
    return f"""
QFrame#TopBar {{
    background-color: {t.bg};
    border: none;
    border-bottom: 1px solid {t.border};
}}
QFrame#Sidebar {{
    background-color: {t.bg};
    border: none;
    border-{end}: 1px solid {t.border};
}}
QLabel[role="brand"] {{
    font-size: 10.5pt;
    font-weight: 600;
}}
QLabel[role="crumb"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
    font-size: {CAPTION_PT:g}pt;
    font-weight: 400;
}}
QLabel[role="title"] {{
    font-size: 19.5pt;
    font-weight: 600;
}}
QLabel[role="subtitle"] {{
    color: {t.text_secondary};
    font-size: 9.75pt;
}}
{tags}
QLabel[chip="accent"] {{
    color: {t.accent_text};
    background-color: {t.accent};
    border: 1px solid {t.accent};
    border-radius: 3px;
    font-size: {SMALL_PT:g}pt;
    font-weight: 400;
    padding: 1px 7px;
}}
QPushButton[painted="true"] {{
    min-height: 0px;
    padding: 0px;
    border: none;
}}
QFrame#Segmented {{
    background-color: transparent;
    border: 1px solid {t.border_strong};
    border-radius: 6px;
}}
QFrame#Segmented QPushButton {{
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-radius: 0px;
    padding: 0px 14px;
    min-height: 30px;
    max-height: 30px;
    font-size: 9.75pt;
    font-weight: 400;
}}
QFrame#Segmented QPushButton[segment="first"], QFrame#Segmented QPushButton[segment="middle"] {{
    border-{end}: 1px solid {t.border_strong};
}}
QFrame#Segmented QPushButton[segment="first"] {{
    border-top-{start}-radius: 5px;
    border-bottom-{start}-radius: 5px;
}}
QFrame#Segmented QPushButton[segment="last"] {{
    border-top-{end}-radius: 5px;
    border-bottom-{end}-radius: 5px;
}}
QFrame#Segmented QPushButton:hover {{
    color: {t.text};
    background-color: transparent;
}}
QFrame#Segmented QPushButton:checked {{
    background-color: {t.accent};
    color: {t.accent_text};
    font-weight: 500;
}}
QFrame#Segmented QPushButton:focus {{
    border: 1px solid {t.text_secondary};
}}
QPushButton#Language {{
    font-family: {number_family()};
    font-size: 9pt;
    padding: 0px 8px;
    min-height: 30px;
    max-height: 30px;
}}
QPushButton#ThemeButton {{
    padding: 0px;
    border: 1px solid transparent;
    border-radius: 6px;
    min-height: 0px;
}}
QPushButton#ThemeButton:hover {{
    background-color: {t.hover};
    border-color: {t.hover};
}}
QPushButton#ThemeButton:focus {{
    border: 1px solid {t.accent};
}}
QStatusBar {{
    background-color: {t.surface};
    border: none;
    border-top: 1px solid {t.border};
}}
QStatusBar QWidget {{
    background-color: transparent;
}}
QLabel[role="foot"] {{
    color: {t.text_secondary};
    font-size: 9pt;
    padding: 0px 7px;
}}
QLabel[role="foot_num"] {{
    color: {t.text_secondary};
    font-family: {number_family()};
    font-size: 9pt;
    padding: 0px 7px;
}}
"""


def shell_text(english: str, persian: bool) -> str:
    """A frame word in the chosen language (English when no Persian word exists)."""
    return SHELL_FA.get(english, english) if persian else english


def persian_digits(text: str) -> str:
    """Western digits as Persian digits (the design writes counts in Persian)."""
    return text.translate(_PERSIAN_DIGITS)


def price_text(price: float) -> str:
    """A ticker price as the design writes it: 1.0854, 154.47, 2314.90."""
    if not math.isfinite(price) or price <= 0:
        return "\u2014"
    if price >= 50:
        return f"{price:.2f}"
    return f"{price:.4f}"


def ticker_symbol(symbol: str) -> str:
    """A symbol as the design writes it: EURUSD as EUR/USD, XAUUSD.m as XAU/USD.m."""
    head, tail = symbol[:6], symbol[6:]
    if len(head) == 6 and head.isalpha() and head.isupper() and (not tail or tail[0] in ".-_"):
        return f"{head[:3]}/{head[3:]}{tail}"
    return symbol


def tone_color(tokens: ThemeTokens, tone: str) -> str:
    """The color of a status light or badge tone (neutral is the secondary text)."""
    colors = {
        "profit": tokens.profit,
        "loss": tokens.loss,
        "warning": tokens.warning,
        "accent": tokens.accent,
        "ink": tokens.accent,
    }
    return colors.get(tone, tokens.text_secondary)


def caption_font(point_size: float = CAPTION_PT) -> QFont:
    """The design's caption face: the number font, small, spaced out a little."""
    font = QFont(NUMBER_FONT)
    font.setPointSizeF(point_size)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    return font


def _rtl(widget: QWidget) -> bool:
    return widget.layoutDirection() == Qt.LayoutDirection.RightToLeft


class Themed(Protocol):
    """A frame piece that repaints in the theme's colors."""

    def apply_tokens(self, tokens: ThemeTokens) -> None: ...


@dataclass(frozen=True)
class TickerItem:
    symbol: str
    price: str
    change: float  # percent since the first price the app saw today; nan = unknown

    def change_text(self) -> str:
        if not math.isfinite(self.change):
            return ""
        arrow = "\u25b2" if self.change >= 0 else "\u25bc"
        return f"{arrow} {abs(self.change):.2f}%"


class DayOpens:
    """The first price the app saw for each symbol on each UTC day (the ticker's change)."""

    def __init__(self) -> None:
        self._day: dict[str, int] = {}
        self._open: dict[str, float] = {}

    def items(self, prices: Mapping[str, float], now: float) -> list[TickerItem]:
        day = int(now // 86_400)
        found: list[TickerItem] = []
        for symbol, price in prices.items():
            if not math.isfinite(price) or price <= 0:
                continue
            if self._day.get(symbol) != day:
                self._day[symbol] = day
                self._open[symbol] = price
            first = self._open[symbol]
            change = (price / first - 1.0) * 100.0 if first > 0 else math.nan
            found.append(TickerItem(symbol, price_text(price), change))
        return found


class Painted(QWidget):
    """A widget that paints itself with the theme's tokens."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tokens: ThemeTokens = DEFAULT

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()


class LogoMark(Painted):
    """The design's logo: an outlined square with three rising bars, in the text color."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Logo")
        self.setFixedSize(LOGO_SIZE, LOGO_SIZE)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.tokens.text)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.75, 0.75, LOGO_SIZE - 1.5, LOGO_SIZE - 1.5), 4, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        bottom = LOGO_SIZE - 6.5  # 1.5 px edge + 5 px padding
        for left, height in ((6.5, 7.0), (12.5, 14.0), (18.5, 10.0)):
            painter.drawRect(QRectF(left, bottom - height, 3.0, height))
        painter.end()


class Led(Painted):
    """A small round status light (the design's `led`)."""

    def __init__(self, size: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = "neutral"
        self.setFixedSize(size, size)

    def set_tone(self, tone: str) -> None:
        if tone != self.tone:
            self.tone = tone
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tone_color(self.tokens, self.tone)))
        painter.drawEllipse(QRectF(self.rect()))
        painter.end()


class GroupRule(Painted):
    """A sidebar group: its name in capitals followed by a hairline to the edge."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name.upper()
        self.setFixedHeight(GROUP_RULE_HEIGHT)
        self.setAccessibleName(name)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        font = caption_font()
        painter.setFont(font)
        metrics = QFontMetrics(font)
        baseline = self.height() - 6 - metrics.descent()
        width = metrics.horizontalAdvance(self.name)
        middle = baseline - metrics.ascent() // 2 + 1
        painter.setPen(QColor(t.text_secondary))
        if _rtl(self):
            text_left = self.width() - 12 - width
            painter.drawText(text_left, baseline, self.name)
            painter.setPen(QColor(t.border))
            painter.drawLine(12, middle, text_left - 8, middle)
        else:
            painter.drawText(12, baseline, self.name)
            painter.setPen(QColor(t.border))
            painter.drawLine(12 + width + 8, middle, self.width() - 12, middle)
        painter.end()


class NavButton(QPushButton):
    """A sidebar page: the page number in the caption face, the title and a badge.

    `text()` stays "01   Dashboard" for screen readers and tests; the button paints the
    number and the title in their own faces, the current page inverted (cream on ink).
    """

    def __init__(self, number: int, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tokens: ThemeTokens = DEFAULT
        self.number = f"{number:02d}"
        self.title = title
        self.badge = ""
        self.badge_tone = "warning"
        self.dot = ""  # a status light at the end of the row, e.g. "warning" on Health
        self.setText(f"{self.number}   {title}")
        self.setAccessibleName(title)
        self.setCheckable(True)
        self.setFixedHeight(NAV_HEIGHT)
        self.setProperty("painted", True)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def set_title(self, title: str) -> None:
        self.title = title
        self.setText(f"{self.number}   {title}")
        self.setAccessibleName(title)
        self.update()

    def set_badge(self, text: str, tone: str = "warning") -> None:
        self.badge, self.badge_tone = text, tone
        self.update()

    def set_dot(self, tone: str) -> None:
        self.dot = tone
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return QSize(SIDEBAR_WIDTH - 20, NAV_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return QSize(120, NAV_HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        checked = self.isChecked()
        hover = self.underMouse()
        if checked:
            fill, text, number = t.accent, t.accent_text, t.accent_text
        elif hover:
            fill, text, number = t.hover, t.text, t.text_secondary
        else:
            fill, text, number = "", t.text_secondary, t.text_secondary
        if fill:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(rect, 4, 4)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(t.text_secondary if checked else t.accent), 1))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        rtl = _rtl(self)
        cap = caption_font()
        painter.setFont(cap)
        cap_metrics = QFontMetrics(cap)
        middle = self.height() / 2
        cap_base = int(middle + (cap_metrics.ascent() - cap_metrics.descent()) / 2)
        start = 12

        def place(offset: int, width: int) -> int:
            return self.width() - offset - width if rtl else offset

        painter.setPen(QColor(number))
        number_width = cap_metrics.horizontalAdvance(self.number)
        painter.drawText(place(start, number_width), cap_base, self.number)
        title_font = QFont(self.font())
        title_font.setPointSizeF(10.5)
        title_font.setWeight(QFont.Weight.Medium if checked else QFont.Weight.Normal)
        painter.setFont(title_font)
        metrics = QFontMetrics(title_font)
        base = int(middle + (metrics.ascent() - metrics.descent()) / 2)
        title_at = start + 18 + 12
        painter.setPen(QColor(text))
        painter.drawText(place(title_at, metrics.horizontalAdvance(self.title)), base, self.title)
        end = 12
        if self.dot:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tone_color(t, self.dot)))
            left = 12 if rtl else self.width() - 12 - 8
            painter.drawEllipse(QRectF(left, middle - 4, 8, 8))
            end += 8 + 8
        if self.badge:
            small = caption_font(SMALL_PT)
            painter.setFont(small)
            small_metrics = QFontMetrics(small)
            width = small_metrics.horizontalAdvance(self.badge) + 14
            left = end if rtl else self.width() - end - width
            box = QRectF(left + 0.5, middle - 9.5, width - 1, 19)
            color = QColor(t.accent_text if checked else tone_color(t, self.badge_tone))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 1))
            painter.drawRoundedRect(box, 3, 3)
            badge_base = int(middle + (small_metrics.ascent() - small_metrics.descent()) / 2)
            painter.drawText(left + 7, badge_base, self.badge)
        painter.end()


class KbdButton(QPushButton):
    """A flat button with an icon, its text and a keyboard hint box, e.g. Search [Ctrl K].

    `variant` is "ghost" (quiet, outlined on hover) or "danger" (coral outline, the kill
    switch). The text stays the button's `text()`; the button paints itself.
    """

    def __init__(
        self,
        text: str,
        kbd: str,
        variant: str = "ghost",
        point_size: float = 10.1,
        height: int = 32,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.tokens: ThemeTokens = DEFAULT
        self.kbd = kbd
        self.variant = variant
        self.point_size = point_size
        self.glyph_icon = QIcon()
        self.setFixedHeight(height)
        self.setProperty("painted", True)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def set_glyph(self, icon: QIcon) -> None:
        self.glyph_icon = icon
        self.updateGeometry()
        self.update()

    def _fonts(self) -> tuple[QFont, QFont]:
        main = QFont(self.font())
        main.setPointSizeF(self.point_size)
        main.setWeight(QFont.Weight.Medium)
        hint = QFont(NUMBER_FONT)
        hint.setPointSizeF(SMALL_PT)
        return main, hint

    def _parts(self) -> tuple[int, int, int]:
        main, hint = self._fonts()
        icon = 0 if self.glyph_icon.isNull() else 15
        text = QFontMetrics(main).horizontalAdvance(self.text())
        kbd = QFontMetrics(hint).horizontalAdvance(self.kbd) + 10 if self.kbd else 0
        return icon, text, kbd

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        icon, text, kbd = self._parts()
        pad = 10 if self.variant == "danger" else 12
        gaps = 8 * (bool(icon) + bool(kbd))
        return QSize(pad * 2 + icon + text + kbd + gaps, self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.42)
        danger = self.variant == "danger"
        hover = self.underMouse() and self.isEnabled()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        edge = t.loss if danger else (t.border_strong if hover else "")
        if self.hasFocus():
            edge = t.text_secondary if danger else t.accent
        fill = (t.loss_soft if danger else t.hover) if hover else ""
        if fill:
            painter.setBrush(QColor(fill))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if edge:
            painter.setPen(QPen(QColor(edge), 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 6, 6)
        color = QColor(t.loss if danger else (t.text if hover else t.text_secondary))
        main, hint = self._fonts()
        icon, text, kbd = self._parts()
        pad = 10 if danger else 12
        rtl = _rtl(self)
        x = pad
        middle = self.height() / 2

        def at(offset: float, width: float) -> float:
            return self.width() - offset - width if rtl else offset

        if icon:
            mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
            pixmap = self.glyph_icon.pixmap(QSize(icon, icon), mode)
            painter.drawPixmap(int(at(x, icon)), int(middle - icon / 2), pixmap)
            x += icon + 8
        painter.setFont(main)
        painter.setPen(color)
        metrics = QFontMetrics(main)
        base = int(middle + (metrics.ascent() - metrics.descent()) / 2)
        painter.drawText(int(at(x, text)), base, self.text())
        x += text + 8
        if kbd:
            painter.setFont(hint)
            box_left = at(x, kbd)
            box = QRectF(box_left, middle - 8, kbd, 16)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(t.loss if danger else t.border_strong), 1))
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            painter.setPen(QColor(t.loss if danger else t.text_secondary))
            hint_metrics = QFontMetrics(hint)
            hint_base = int(middle + (hint_metrics.ascent() - hint_metrics.descent()) / 2)
            painter.drawText(int(box_left) + 5, hint_base, self.kbd)
        painter.end()


class TickerStrip(Painted):
    """The LIVE strip: the watched symbols glide by with their price and today's change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TickerStrip")
        self.setFixedHeight(TICKER_HEIGHT)
        self.items: list[TickerItem] = []
        self.live = False
        self.offset = 0.0
        self.font_ = QFont(NUMBER_FONT)
        self.font_.setPointSizeF(9.0)
        self.symbol_font = QFont(self.font_)
        self.symbol_font.setWeight(QFont.Weight.Medium)
        self.cycle = 1200  # the width of one set of quotes, measured when painted
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)
        self.timer.start(TICK_MS)

    def set_items(self, items: Sequence[TickerItem], live: bool) -> None:
        self.items = list(items)
        self.live = live and bool(self.items)
        self.update()

    def step(self) -> None:
        if self.live and self.isVisible():
            self.offset += self.cycle * TICK_MS / TICK_CYCLE_MS
            self.update()

    def item_width(self, item: TickerItem, metrics: QFontMetrics) -> int:
        symbol = QFontMetrics(self.symbol_font).horizontalAdvance(ticker_symbol(item.symbol))
        parts = [item.price, item.change_text()]
        widths = [symbol, *(metrics.horizontalAdvance(part) for part in parts if part)]
        return sum(widths) + 8 * (len(widths) - 1) + 44

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(t.surface))
        painter.setPen(QColor(t.border))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        cap = caption_font()
        painter.setFont(cap)
        cap_metrics = QFontMetrics(cap)
        middle = self.height() / 2
        cap_base = int(middle + (cap_metrics.ascent() - cap_metrics.descent()) / 2)
        label = "LIVE" if self.live else "OFFLINE"
        badge = 14 + cap_metrics.horizontalAdvance(label) + 14
        painter.setPen(QColor(t.text_secondary))
        painter.drawText(14, cap_base, label)
        painter.setPen(QColor(t.border))
        painter.drawLine(badge, 0, badge, self.height())
        painter.setFont(self.font_)
        metrics = QFontMetrics(self.font_)
        baseline = int(middle + (metrics.ascent() - metrics.descent()) / 2)
        if not self.items:
            painter.setPen(QColor(t.text_secondary))
            painter.drawText(badge + 22, baseline, "\u2014")
            painter.end()
            return
        widths = [self.item_width(item, metrics) for item in self.items]
        total = sum(widths)
        self.cycle = max(total, 1)
        painter.setClipRect(badge + 1, 0, self.width() - badge, self.height())
        x = badge - (self.offset % total if total else 0)
        while x < self.width():
            for item, width in zip(self.items, widths, strict=True):
                if x + width > badge:
                    self._draw_item(painter, item, x, width, baseline, metrics)
                x += width
        painter.end()

    def _draw_item(
        self,
        painter: QPainter,
        item: TickerItem,
        x: float,
        width: int,
        baseline: int,
        metrics: QFontMetrics,
    ) -> None:
        t = self.tokens
        painter.setPen(QColor(t.border))
        painter.drawLine(int(x), 0, int(x), self.height())
        left = int(x) + 22
        symbol = ticker_symbol(item.symbol)
        painter.setPen(QColor(t.text))
        painter.setFont(self.symbol_font)
        painter.drawText(left, baseline, symbol)
        left += QFontMetrics(self.symbol_font).horizontalAdvance(symbol) + 8
        painter.setFont(self.font_)
        painter.drawText(left, baseline, item.price if self.live else "\u2014")
        left += metrics.horizontalAdvance(item.price) + 8
        change = item.change_text()
        if change and self.live:
            painter.setPen(QColor(t.profit if item.change >= 0 else t.loss))
            painter.drawText(left, baseline, change)


class Segmented(QFrame):
    """Joined buttons, the chosen one drawn inverted (the design's `seg`)."""

    def __init__(self, labels: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        last = len(labels) - 1
        for index, text in enumerate(labels):
            button = QPushButton(text)
            button.setCheckable(True)
            edge = "first" if index == 0 else "last" if index == last else "middle"
            button.setProperty("segment", edge)
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)

    def choose(self, index: int) -> None:
        self.buttons[index].setChecked(True)

    def set_labels(self, labels: Sequence[str]) -> None:
        for button, text in zip(self.buttons, labels, strict=True):
            button.setText(text)
