"""The Simple view's Home screen, drawn like the owner's No Curve v2 design (Phase 20d).

One proposal at a time on the wide side; the open trades and the emergency stop on the
narrow side (372 px), as on the design's Simple board:

- PROPOSAL n/N with a LIVE or WAITING FOR DATA tag (the design's tag says what data is shown).
- The proposal card: the symbol, the side and the strategy; one sentence with the size and the
  money at stake; the risk bar (the hatched loss and the soft profit, split at the entry by
  the reward to risk); the win chance as 100 dots (low end, estimate, high end); WHY with the
  real reasons; Show details with the exact numbers. Below a dashed line: hold to approve
  (Space or Enter too), then the usual question, and Skip.
- Open trades with their results, the day's result and the balance (an app extra).
- EMERGENCY: a hatched cover over "close everything now". Open the cover first, then the same
  kill switch as the Advanced view (it asks first). Ctrl+Shift+K works everywhere.
- A toast at the bottom says what an action did.

Every number comes from the same snapshots as the Advanced pages (signals, execution, risk)
through queued Qt signals, and every action is the same call (approve, dismiss, close, kill
switch): a plain view over one engine, never a second logic path. Nothing is a sample: an
unknown value is a dash or "not known yet". The texts of `app.ui.home_model` are English and
`translator` turns them into Persian; the design's own words are chosen here (`WORDS_FA`).
"""

from __future__ import annotations

import html
import math
import time
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.analysis.sessions import market_open
from app.core.ui_prefs import Language
from app.domain.modes import OperatingMode
from app.domain.probability import ProbabilityEstimate
from app.domain.signals import SignalRecord
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.models import Quote
from app.observability.logger import audit
from app.risk.limits import RiskUsage
from app.risk.risk_manager import RiskSnapshot
from app.strategies.registry import STRATEGIES
from app.ui.home_model import (
    KNOWLEDGE_QUESTION,
    MODE_LINES,
    PRACTICE_TEXT,
    PRACTICE_TITLE,
    SuggestionView,
    TradeRow,
    action_text,
    approve_block_text,
    approve_question,
    balance_view,
    close_question,
    home_status,
    kind_of,
    money,
    ordered_suggestions,
    plain_reason,
    price_moved,
    suggestion_view,
    trade_rows,
)
from app.ui.i18n import Translator, translate_widgets
from app.ui.navigation import SIMPLE_HOME
from app.ui.positions_page import TradingContext
from app.ui.risk_page import RiskContext
from app.ui.shell import Painted, ticker_symbol
from app.ui.signals_page import SignalsContext, ask
from app.ui.theme import DEFAULT, NUMBER_FONT, ThemeTokens, number_family, px
from app.ui.v2 import Icon, Section, Tag, draw_icon, hairline, set_tone

DEFAULT_TOLERANCE_R = 0.25
EMPTY_CONNECTED = (
    "The app tells you here as soon as it finds one. Many hours have no good trade, and that "
    "is normal."
)
EMPTY_DISCONNECTED = "Connect MetaTrader 5 in Settings (top right) so the app can watch the market."
Confirm = Callable[[str, str], bool]
DASH = "\u2014"
LRE = "\u202a"  # keeps a number left to right inside a Persian sentence
PDF = "\u202c"
RTL = Qt.LayoutDirection.RightToLeft
LTR = Qt.LayoutDirection.LeftToRight
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
MIDDLE = Qt.AlignmentFlag.AlignVCenter
# The design's geometry (px), Simple board 1280 by 820.
MAIN_MARGINS = (48, 36, 48, 36)  # `main{padding:36px 48px}`
MAIN_GAP = 20
ASIDE_WIDTH = 372
ASIDE_MARGINS = (28, 36, 28, 36)
ASIDE_GAP = 18
CARD_RADIUS = 10
BODY_MARGINS = (36, 32, 36, 32)
BODY_GAP = 30
FOOTER_MARGINS = (36, 22, 36, 22)
FOOTER_GAP = 14
NOTCH = 9  # the two half circles where the dashed line meets the edges (18 px)
SENTENCE_WIDTH = 720
RISK_BAR_HEIGHT = 34
RISK_MARK = 4  # the entry line stands out 4 px above and below the bar
DOT = 11
DOT_GAP = 5
DOTS = 10  # ten by ten: a dot is 1%
LEGEND_DOT = 9
HOLD_WIDTH = 320
HOLD_HEIGHT = 52
HOLD_STEP_MS = 30
HOLD_STEP = 0.04  # 25 steps of 30 ms: a hold of 0.75 s, like the design
EMERGENCY_HEIGHT = 60
COVER_MS = 450
COVER_LIFT = 1.04  # `translateY(-104%)`
TOAST_MS = 2600
TOAST_BOTTOM = 26
POP_MS = 350
POP_STEP_MS = 6
REASONS = 3
HOLD_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)

UNKNOWN_SIZE = "The size and the amounts are not known until the account is read."
FILTERS_PASSED = "It passed the app's filters before it was shown."
STOP_SENT = "Stop sent: the app's trades are being closed and new ones are stopped."
WIN_NOTE = "estimated win chance (uncertain)"
HOLD_PAPER = "Hold to place it in Paper"
HOLD_REAL = "Hold to send the real order"
HOLD_OTHER = "Hold to approve"
HOLDING = "Placing\u2026"
COVER_TEXT = "Emergency stop \u00b7 open the cover"
COVER_NAME = "Open the safety cover of the stop switch"
STOP_TEXT = "Close everything now"
STOP_TIP = "Closes the app's trades, cancels its orders and stops new ones (asks first)."
NOTHING_RUNNING = "Nothing is running yet."
SENTENCE_EN = (
    "{verb} {symbol} with a size of {lot}. If it goes wrong you lose about {lose}, and if it "
    "goes right you make about {make}."
)
SENTENCE_FA = (
    "{lot} لات {symbol} {verb}. اگر اشتباه کند حدود {lose} از دست می‌دهید و اگر درست باشد "
    "حدود {make} به دست می‌آورید."
)
RANGE_EN = "Likely range {range} from {count} samples. No result is guaranteed."
RANGE_FA = "بازه محتمل {range} بر پایه {count} نمونه. ضمانتی برای نتیجه نیست."
UNKNOWN_EN = "Not known yet: {count} of the {needed} past results it needs."
UNKNOWN_FA = "هنوز معلوم نیست: {count} از {needed} نتیجه قبلی که لازم است."
RISK_EN = "This trade risks {percent} of your money."
RISK_FA = "ریسک این معامله {percent} سرمایه است."
VERBS_FA = {"Buy": "بخرید", "Sell": "بفروشید"}
WORDS_FA: dict[str, str] = {
    "LIVE": "زنده",
    "WAITING FOR DATA": "در انتظار داده",
    "Buy": "خرید",
    "Sell": "فروش",
    WIN_NOTE: "تخمین احتمال برد (نامطمئن)",
    "at least likely": "حداقل محتمل",
    "up to the estimate": "تا تخمین",
    "uncertainty": "عدم قطعیت",
    HOLD_PAPER: "نگه دارید تا در Paper ثبت شود",
    HOLD_REAL: "نگه دارید تا سفارش واقعی ارسال شود",
    HOLD_OTHER: "نگه دارید تا تأیید شود",
    HOLDING: "در حال ثبت\u2026",
    "Skip": "رد کردن",
    "Open trades": "معاملات باز",
    "No open trades.": "معامله بازی وجود ندارد.",
    STOP_TEXT: "همین الان همه را ببند",
    COVER_TEXT: "توقف اضطراری \u00b7 پوشش را باز کنید",
    COVER_NAME: "باز کردن پوشش محافظ کلید توقف",
    "Cancel": "انصراف",
    UNKNOWN_SIZE: "حجم و مبلغ‌ها تا خوانده شدن حساب معلوم نیست.",
    FILTERS_PASSED: "پیش از نمایش، از فیلترهای برنامه گذشته است.",
    STOP_SENT: "توقف ارسال شد: معامله‌های ربات بسته می‌شوند و معامله جدید متوقف است.",
}
STRATEGY_FA: dict[str, str] = {
    "trend_pullback": "پولبک در جهت روند",
    "london_breakout": "شکست لندن",
    "range_reversion": "بازگشت در محدوده",
    "channel_breakout": "شکست کانال",
    "ema_momentum": "مومنتوم EMA",
}


class _Bridge(QObject):
    signals = Signal(object)
    trading = Signal(object)
    risk = Signal(object)


def ltr(text: str, persian: bool) -> str:
    """`text` kept left to right inside a Persian sentence (money, sizes, symbols)."""
    return f"{LRE}{text}{PDF}" if persian and text else text


def hold_label(mode: OperatingMode) -> str:
    if mode is OperatingMode.PAPER:
        return HOLD_PAPER
    return HOLD_REAL if mode is OperatingMode.SEMI_AUTO else HOLD_OTHER


def percent_bounds(estimate: ProbabilityEstimate) -> tuple[int, int, int] | None:
    """The win chance in whole percents (low end, estimate, high end); None while not known."""
    if estimate.value is None or estimate.low is None or estimate.high is None:
        return None
    low = min(100, max(0, round(estimate.low * 100)))
    value = min(100, max(low, round(estimate.value * 100)))
    high = min(100, max(value, round(estimate.high * 100)))
    return low, value, high


def loss_share(rr: float) -> float:
    """The loss side of the risk bar: the risk over risk plus reward, 1 / (1 + R:R)."""
    if not math.isfinite(rr) or rr <= 0:
        return 0.5
    return 1.0 / (1.0 + rr)


def home_label(text: str, role: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("home", role)
    label.setWordWrap(wrap)
    return label


def set_role(label: QLabel, role: str) -> None:
    """Change a label's `home` role and re-apply the stylesheet when it changed."""
    if label.property("home") != role:
        label.setProperty("home", role)
        style = label.style()
        style.unpolish(label)
        style.polish(label)


def home_button(text: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("home", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def paint_hatch(painter: QPainter, box: QRectF, color: str, stripe: float, gap: float) -> None:
    """The design's `repeating-linear-gradient(135deg, color 0 stripe, transparent ...)`."""
    if box.width() <= 0 or box.height() <= 0:
        return
    painter.save()
    painter.setClipRect(box, Qt.ClipOperation.IntersectClip)
    pen = QPen(QColor(color), stripe)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    period = (stripe + gap) * math.sqrt(2.0)
    rise = box.height()
    x = box.left() - rise
    while x < box.right() + rise:
        painter.drawLine(QPointF(x, box.bottom()), QPointF(x + rise, box.top()))
        x += period
    painter.restore()


def stop_icon() -> QIcon:
    """The design's filled square on the emergency button (white, 12 of 15 px)."""
    pixmap = QPixmap(30, 30)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawRoundedRect(QRectF(2.5, 2.5, 10, 10), 1.5, 1.5)
    painter.end()
    return QIcon(pixmap)


def home_qss(tokens: ThemeTokens) -> str:
    """The Home screen's own rules, on top of the frame's stylesheet."""
    t = tokens
    mono = number_family()
    return f"""
QWidget[home="card"] QWidget, QFrame[home="welcome"] QWidget {{
    background: transparent;
}}
QFrame[home="welcome"] {{
    background-color: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {CARD_RADIUS}px;
}}
QLabel[home="cap"] {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {px(11):g}pt;
}}
QLabel[home="symbol"] {{
    font-family: {mono};
    font-size: {px(34):g}pt;
    font-weight: 600;
}}
QLabel[home="strategy"], QLabel[home="note"], QLabel[home="ends"], QLabel[home="details"],
QLabel[home="status"], QLabel[home="pending"] {{
    color: {t.text_secondary};
    font-size: {px(13):g}pt;
}}
QLabel[home="sentence"] {{
    font-size: {px(16):g}pt;
}}
QLabel[home="win"] {{
    font-family: {mono};
    font-size: {px(36):g}pt;
    font-weight: 600;
}}
QLabel[home="range"] {{
    font-size: {px(13):g}pt;
}}
QLabel[home="legend"] {{
    color: {t.text_secondary};
    font-size: {px(11):g}pt;
}}
QLabel[home="reason"] {{
    font-size: {px(14):g}pt;
}}
QFrame[home="reason_row"] {{
    background: transparent;
    border: none;
    border-top: 1px solid {t.border};
}}
QLabel[home="warning"] {{
    color: {t.warning};
    font-size: {px(13):g}pt;
}}
QLabel[home="empty_title"] {{
    font-size: {px(20):g}pt;
    font-weight: 600;
}}
QLabel[home="empty_note"], QLabel[home="no_trades"] {{
    color: {t.text_secondary};
    font-size: {px(14):g}pt;
}}
QLabel[home="no_trades"] {{
    padding: 30px 0px;
    border-bottom: 1px solid {t.border};
}}
QLabel[home="state"] {{
    font-size: {px(14):g}pt;
    font-weight: 600;
}}
QPushButton[home="skip"] {{
    background-color: transparent;
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    padding: 0px 24px;
    min-height: {HOLD_HEIGHT - 2}px;
    max-height: {HOLD_HEIGHT - 2}px;
    font-size: {px(14):g}pt;
    font-weight: 500;
}}
QPushButton[home="skip"]:hover {{
    background-color: {t.hover};
    border-color: {t.text_secondary};
}}
QPushButton[home="skip"]:disabled {{
    color: {t.text_secondary};
    border-color: {t.border};
}}
QPushButton[home="link"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-radius: 4px;
    text-decoration: underline;
    padding: 2px 6px;
    min-height: 0px;
    font-size: {px(13):g}pt;
    font-weight: 400;
}}
QPushButton[home="link"]:hover {{
    background-color: transparent;
    color: {t.text};
}}
QPushButton[home="link"]:focus {{
    border: 1px solid {t.text};
}}
QPushButton[home="link"]:disabled {{
    color: {t.border_strong};
}}
QFrame[home="trade"] {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {t.border};
}}
QLabel[home="trade_sym"], QLabel[home="balance"] {{
    font-family: {mono};
    font-size: {px(14):g}pt;
    font-weight: 600;
}}
QLabel[home="trade_side"], QLabel[home="small"] {{
    color: {t.text_secondary};
    font-size: {px(12):g}pt;
}}
QLabel[home="trade_pl"] {{
    font-family: {mono};
    font-size: {px(18):g}pt;
    font-weight: 600;
}}
QLabel[home="day"] {{
    font-family: {mono};
    font-size: {px(30):g}pt;
    font-weight: 600;
}}
QLabel[home="trade_pl"][tone="profit"], QLabel[home="day"][tone="profit"],
QLabel[home="small"][tone="profit"] {{
    color: {t.profit};
}}
QLabel[home="trade_pl"][tone="loss"], QLabel[home="day"][tone="loss"],
QLabel[home="small"][tone="loss"] {{
    color: {t.loss};
}}
QLabel[home="trade_pl"][tone="muted"], QLabel[home="day"][tone="muted"] {{
    color: {t.text_secondary};
}}
QLabel[home="kbd"] {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {px(11):g}pt;
}}
QFrame[home="emergency"] {{
    background: transparent;
    border: 1px solid {t.loss};
    border-radius: 6px;
}}
QPushButton[home="stop"], QPushButton[home="stop"]:hover, QPushButton[home="stop"]:disabled {{
    background-color: {t.loss};
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    padding: 0px;
    min-height: 0px;
    font-size: {px(15):g}pt;
    font-weight: 600;
}}
QPushButton[home="stop"]:focus {{
    border: 2px solid {t.text};
}}
QLabel[home="toast"] {{
    background-color: {t.text};
    color: {t.bg};
    border-radius: 6px;
    padding: 12px 20px;
    font-size: {px(14):g}pt;
    font-weight: 500;
}}
"""


class ProposalCard(Painted):
    """The proposal's outline (10 px corners) with the dashed line above the buttons and the
    two half circles cut into the edges there, like a ticket."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("home", "card")
        self.footer: QWidget | None = None

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setBrush(QColor(t.surface))
        painter.setPen(QPen(QColor(t.border_strong), 1))
        painter.drawRoundedRect(box, CARD_RADIUS, CARD_RADIUS)
        footer = self.footer
        if footer is not None and footer.isVisible():
            y = footer.y() + 0.5
            dashed = QPen(QColor(t.border_strong), 1)
            dashed.setDashPattern([3.0, 3.0])
            painter.setPen(dashed)
            painter.drawLine(QPointF(NOTCH, y), QPointF(self.width() - NOTCH, y))
            painter.setBrush(QColor(t.bg))
            painter.setPen(QPen(QColor(t.border_strong), 1))
            for x in (0.0, float(self.width())):
                painter.drawEllipse(QPointF(x, y), NOTCH, NOTCH)
        painter.end()


class SideTag(Painted):
    """The proposal's side: an inverted tag with the arrow and Buy or Sell (12 px, 3 by 10)."""

    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.icon = "up"
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set(self, text: str, icon: str) -> None:
        self.text = text
        self.icon = icon
        self.updateGeometry()
        self.update()

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(px(12))
        return font

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        metrics = QFontMetrics(self._font())
        width = 10 + 14 + 5 + metrics.horizontalAdvance(self.text) + 10
        return QSize(width, max(20, metrics.height() + 6))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.accent))
        painter.drawRoundedRect(QRectF(self.rect()), 3, 3)
        rtl = self.layoutDirection() == RTL
        top = (self.height() - 14) / 2
        icon_x = self.width() - 10 - 14 if rtl else 10
        draw_icon(painter, self.icon, QRectF(icon_x, top, 14, 14), t.accent_text)
        words = QRectF(10, 0, self.width() - 10 - 14 - 5 - 10, self.height())
        if not rtl:
            words.moveLeft(10 + 14 + 5)
        painter.setFont(self._font())
        painter.setPen(QColor(t.accent_text))
        painter.drawText(words, Qt.AlignmentFlag.AlignCenter, self.text)
        painter.end()


class RiskBar(Painted):
    """The design's risk bar, left to right: the hatched loss up to the entry line, the soft
    profit after it and the two amounts at the ends (34 px high, 4 px corners)."""

    def __init__(self) -> None:
        super().__init__()
        self.share = 0.5
        self.lose_text = ""
        self.make_text = ""
        self.setFixedHeight(RISK_BAR_HEIGHT + 2 * RISK_MARK)
        self.setMinimumWidth(240)
        self.setLayoutDirection(LTR)

    def set(self, share: float, lose: str, make: str) -> None:
        self.share = min(1.0, max(0.0, share))
        self.lose_text = lose
        self.make_text = make
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0.5, RISK_MARK + 0.5, self.width() - 1, RISK_BAR_HEIGHT - 1)
        split = bar.left() + bar.width() * self.share
        outline = QPainterPath()
        outline.addRoundedRect(bar, 4, 4)
        painter.save()
        painter.setClipPath(outline)
        profit = QRectF(split, bar.top(), bar.right() - split, bar.height())
        painter.fillRect(profit, QColor(t.profit_soft))
        loss = QRectF(bar.left(), bar.top(), split - bar.left(), bar.height())
        paint_hatch(painter, loss, t.loss_soft, 4.0, 4.0)
        painter.restore()
        painter.setPen(QPen(QColor(t.border_strong), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)
        font = QFont(NUMBER_FONT)
        font.setPointSizeF(px(12))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        middle = bar.center().y() - 7
        draw_icon(painter, "down", QRectF(bar.left() + 10, middle, 14, 14), t.loss)
        painter.setPen(QColor(t.loss))
        words = QRectF(bar.left() + 28, bar.top(), max(0.0, split - bar.left()), bar.height())
        painter.drawText(words, LEFT | MIDDLE, self.lose_text)
        width = metrics.horizontalAdvance(self.make_text)
        start = bar.right() - 10 - width
        draw_icon(painter, "up", QRectF(start - 18, middle, 14, 14), t.profit)
        painter.setPen(QColor(t.profit))
        gain = QRectF(start, bar.top(), width + 2, bar.height())
        painter.drawText(gain, LEFT | MIDDLE, self.make_text)
        painter.fillRect(QRectF(split - 1, 0, 2, self.height()), QColor(t.text))
        painter.end()


class ProbabilityDots(Painted):
    """The win chance as 100 dots, left to right: filled up to the low end, faded up to the
    estimate, rings up to the high end and the hairline color after it. Not known yet: all of
    them in the hairline color. The dots pop in one after the other (6 ms apart)."""

    def __init__(self) -> None:
        super().__init__()
        side = DOTS * DOT + (DOTS - 1) * DOT_GAP
        self.setFixedSize(side, side)
        self.setLayoutDirection(LTR)
        self.bounds: tuple[int, int, int] | None = None
        self.key = ""
        self.motion = True
        self.elapsed = float(self.total_ms())
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(float(self.total_ms()))
        self.animation.setDuration(self.total_ms())
        self.animation.valueChanged.connect(self._grow)

    @staticmethod
    def total_ms() -> int:
        return POP_MS + POP_STEP_MS * (DOTS * DOTS - 1)

    def set(self, bounds: tuple[int, int, int] | None, key: str) -> None:
        if key == self.key and bounds == self.bounds:
            return
        fresh = key != self.key
        self.key = key
        self.bounds = bounds
        if fresh and self.motion and self.isVisible():
            self.animation.stop()
            self.elapsed = 0.0
            self.animation.start()
        elif self.animation.state() != QAbstractAnimation.State.Running:
            self.elapsed = float(self.total_ms())
        self.update()

    def _grow(self, value: object) -> None:
        self.elapsed = float(value) if isinstance(value, int | float) else float(self.total_ms())
        self.update()

    def kind(self, index: int) -> str:
        if self.bounds is None:
            return "empty"
        low, value, high = self.bounds
        if index < low:
            return "fill"
        if index < value:
            return "fade"
        return "ring" if index < high else "empty"

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index in range(DOTS * DOTS):
            grow = min(1.0, max(0.0, (self.elapsed - index * POP_STEP_MS) / POP_MS))
            if grow <= 0:
                continue
            row, column = divmod(index, DOTS)
            size = DOT * (0.3 + 0.7 * grow)
            left = column * (DOT + DOT_GAP) + (DOT - size) / 2
            top = row * (DOT + DOT_GAP) + (DOT - size) / 2
            box = QRectF(left, top, size, size)
            kind = self.kind(index)
            painter.setOpacity(grow * {"fade": 0.55, "ring": 0.85}.get(kind, 1.0))
            if kind == "ring":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(t.text), 1.5))
                painter.drawEllipse(box.adjusted(0.75, 0.75, -0.75, -0.75))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.border if kind == "empty" else t.text))
                painter.drawEllipse(box)
        painter.end()


class LegendDot(Painted):
    """A 9 px dot of the dots' legend: filled, faded or a ring."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.setFixedSize(LEGEND_DOT, LEGEND_DOT)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0, 0, LEGEND_DOT, LEGEND_DOT)
        if self.kind == "ring":
            painter.setPen(QPen(QColor(t.text), 1.5))
            painter.drawEllipse(box.adjusted(0.75, 0.75, -0.75, -0.75))
        else:
            painter.setOpacity(0.55 if self.kind == "fade" else 1.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.text))
            painter.drawEllipse(box)
        painter.end()


class HoldButton(QPushButton):
    """Hold to approve (320 by 52): the ink fills it from the reading side while the mouse
    button, Space or Enter is held; letting go before the end stops and empties it. At the
    end it sends `held` once; the page then asks the usual question before anything is sent.
    """

    held = Signal()

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.tokens: ThemeTokens = DEFAULT
        self.busy_text = HOLDING
        self.progress = 0.0
        self.setFixedSize(HOLD_WIDTH, HOLD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timer = QTimer(self)
        self.timer.setInterval(HOLD_STEP_MS)
        self.timer.timeout.connect(self._step)
        self.pressed.connect(self.start_hold)
        self.released.connect(self.stop_hold)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    @property
    def holding(self) -> bool:
        return self.timer.isActive()

    def start_hold(self) -> None:
        if not self.isEnabled() or self.timer.isActive():
            return
        self.progress = 0.0
        self.timer.start()
        self.update()

    def stop_hold(self) -> None:
        self.timer.stop()
        self.progress = 0.0
        self.update()

    def _step(self) -> None:
        self.progress = min(1.0, self.progress + HOLD_STEP)
        if self.progress >= 1.0:
            self.stop_hold()
            self.held.emit()
            return
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt name)
        if event.key() in HOLD_KEYS:
            if not event.isAutoRepeat():
                self.start_hold()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt name)
        if event.key() in HOLD_KEYS:
            if not event.isAutoRepeat():
                self.stop_hold()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt name)
        self.stop_hold()
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt name)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.stop_hold()
        super().changeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        outline = QPainterPath()
        outline.addRoundedRect(box, 6, 6)
        ink = QColor(t.text if self.isEnabled() else t.border_strong)
        if self.underMouse() and self.isEnabled() and not self.progress:
            painter.fillPath(outline, QColor(t.hover))
        width = box.width() * self.progress
        fill = QRectF(box.left(), box.top(), width, box.height())
        if self.layoutDirection() == RTL:
            fill.moveRight(box.right())
        if width > 0:
            painter.save()
            painter.setClipPath(outline)
            painter.fillRect(fill, ink)
            painter.restore()
        painter.setPen(QPen(ink, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)
        if self.hasFocus() and not width:
            painter.setPen(QPen(ink, 2))
            painter.drawRoundedRect(box.adjusted(3, 3, -3, -3), 4, 4)
        font = QFont(self.font())
        font.setPointSizeF(px(15))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        words = self.busy_text if self.progress > 0 else self.text()
        painter.setPen(ink if self.isEnabled() else QColor(t.text_secondary))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, words)
        if width > 0:
            painter.save()
            painter.setClipRect(fill)
            painter.setPen(QColor(t.accent_text))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, words)
            painter.restore()
        painter.end()


class StopCover(QPushButton):
    """The hatched cover over the emergency button, its label in the middle."""

    def __init__(self) -> None:
        super().__init__(COVER_TEXT)
        self.tokens: ThemeTokens = DEFAULT
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        box = QRectF(self.rect())
        outline = QPainterPath()
        outline.addRoundedRect(box, 5, 5)
        painter.fillPath(outline, QColor(t.surface))
        painter.save()
        painter.setClipPath(outline)
        paint_hatch(painter, box, t.loss_soft, 10.0, 10.0)
        painter.restore()
        font = QFont(self.font())
        font.setPointSizeF(px(13))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(self.text()) + 28
        height = metrics.height() + 10
        chip = QRectF((box.width() - width) / 2, (box.height() - height) / 2, width, height)
        painter.setBrush(QColor(t.surface))
        painter.setPen(QPen(QColor(t.loss), 1))
        painter.drawRoundedRect(chip.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.text), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(2, 2, -2, -2), 4, 4)
        painter.end()


class EmergencyBox(QFrame):
    """The design's EMERGENCY block (60 px): the stop button under a cover that slides up
    (0.45 s) when it is opened and comes back down on Cancel."""

    def __init__(self, stop: QPushButton, cover: StopCover) -> None:
        super().__init__()
        self.setProperty("home", "emergency")
        self.setFixedHeight(EMERGENCY_HEIGHT)
        self.stop = stop
        self.cover = cover
        stop.setParent(self)
        cover.setParent(self)
        self.armed = False
        self.motion = True
        self.slide = QPropertyAnimation(cover, b"pos", self)
        self.slide.setDuration(COVER_MS)
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        curve.addCubicBezierSegment(QPointF(0.6, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
        self.slide.setEasingCurve(curve)

    def inside(self) -> QRect:
        return self.rect().adjusted(1, 1, -1, -1)

    def cover_at(self, armed: bool) -> QPoint:
        inner = self.inside()
        lift = math.ceil(inner.height() * COVER_LIFT) if armed else 0
        return QPoint(inner.left(), inner.top() - lift)

    def set_armed(self, armed: bool) -> None:
        if armed == self.armed:
            return
        self.armed = armed
        self.slide.stop()
        end = self.cover_at(armed)
        if self.motion and self.isVisible():
            self.slide.setStartValue(self.cover.pos())
            self.slide.setEndValue(end)
            self.slide.start()
        else:
            self.cover.move(end)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        inner = self.inside()
        self.stop.setGeometry(inner)
        self.cover.resize(inner.size())
        if self.slide.state() != QAbstractAnimation.State.Running:
            self.cover.move(self.cover_at(self.armed))
        self.cover.raise_()


class Toast(QLabel):
    """The design's toast: ink with page-colored words, 26 px above the bottom, 2.6 s."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("home", "toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(TOAST_MS)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def say(self, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self.timer.start()

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        left = max(0, (parent.width() - self.width()) // 2)
        self.move(left, max(0, parent.height() - TOAST_BOTTOM - self.height()))


class HomePage(QWidget):
    def __init__(
        self,
        signals: SignalsContext | None = None,
        risk: RiskContext | None = None,
        trading: TradingContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(f"page_{SIMPLE_HOME.page_id}")
        self.signals_context = signals
        self.risk_context = risk
        self.trading = trading
        self.translator = Translator(Language.EN)
        self.confirm: Confirm = lambda title, text: ask(self, title, text)
        self.stop: Callable[[], bool] | None = None
        self.on_onboarded: Callable[[bool], None] | None = None
        self.on_status_bar: Callable[[bool], None] | None = None
        self.quote: Callable[[str], Quote | None] = lambda symbol: None
        self.now: Callable[[], float] = time.time
        self.tokens: ThemeTokens = DEFAULT
        self.connected = False
        self.armed = False
        self.last_signals: SignalsSnapshot | None = None
        self.last_trading: ExecutionSnapshot | None = None
        self.last_risk: RiskSnapshot | None = None
        self.suggestion: SignalRecord | None = None
        self.view: SuggestionView | None = None
        self.sentence_text = ""
        self.rows: list[TradeRow] = []
        self.close_buttons: list[QPushButton] = []
        self.reason_labels: list[QLabel] = []
        self._reasons: list[str] = []
        self._row_keys: list[tuple[str, int, bool]] = []
        self._row_results: list[tuple[Icon, QLabel]] = []
        self._acted: set[str] = set()
        self._asking = False
        self.bridge = _Bridge()
        self.bridge.signals.connect(self.show_signals, Qt.ConnectionType.QueuedConnection)
        self.bridge.trading.connect(self.show_trading, Qt.ConnectionType.QueuedConnection)
        self.bridge.risk.connect(self.show_risk, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_welcome(), 1)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("HomeScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self._build_content())
        layout.addWidget(self.scroll_area, 1)
        self.toast = Toast(self)
        if signals is not None:
            signals.pipeline.add_listener(self.bridge.signals.emit)
            self.last_signals = signals.pipeline.snapshot
        if risk is not None:
            risk.manager.add_listener(self.bridge.risk.emit)
            self.last_risk = risk.manager.snapshot
        if trading is not None:
            trading.engine.add_listener(self.bridge.trading.emit)
            self.last_trading = trading.engine.snapshot
        self.apply_tokens(DEFAULT)

    # Building ------------------------------------------------------------------------------
    def _build_welcome(self) -> QWidget:
        """The first start: practice money first, then Simple or Advanced (spec F0, F2)."""
        self.welcome_area = QWidget()
        outer = QVBoxLayout(self.welcome_area)
        outer.setContentsMargins(*MAIN_MARGINS)
        self.welcome = QFrame()
        self.welcome.setObjectName("WelcomeCard")
        self.welcome.setProperty("home", "welcome")
        self.welcome.setMaximumWidth(SENTENCE_WIDTH + BODY_MARGINS[0] + BODY_MARGINS[2])
        layout = QVBoxLayout(self.welcome)
        layout.setContentsMargins(*BODY_MARGINS)
        layout.setSpacing(14)
        layout.addWidget(home_label("Welcome", "empty_title"))
        self.practice_label = home_label(PRACTICE_TITLE, "state")
        layout.addWidget(self.practice_label)
        layout.addWidget(home_label(PRACTICE_TEXT, "sentence", wrap=True))
        layout.addWidget(home_label(KNOWLEDGE_QUESTION, "state"))
        row = QHBoxLayout()
        row.setSpacing(FOOTER_GAP)
        self.new_button = QPushButton("I'm new to trading: keep it simple")
        self.new_button.setObjectName("NewToTradingButton")
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(self._welcome_slot(False))
        self.trader_button = QPushButton("I already trade: show the Advanced view")
        self.trader_button.setObjectName("TraderButton")
        self.trader_button.clicked.connect(self._welcome_slot(True))
        row.addWidget(self.new_button)
        row.addWidget(self.trader_button)
        row.addStretch(1)
        layout.addLayout(row)
        outer.addWidget(self.welcome)
        outer.addStretch(1)
        self.welcome_area.setVisible(False)
        return self.welcome_area

    def _build_content(self) -> QWidget:
        content = QWidget()
        row = QHBoxLayout(content)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_main(), 1)
        row.addWidget(hairline(vertical=True))
        row.addWidget(self._build_aside())
        return content

    def _build_main(self) -> QWidget:
        main = QWidget()
        main.setMinimumWidth(SENTENCE_WIDTH // 2)
        layout = QVBoxLayout(main)
        layout.setContentsMargins(*MAIN_MARGINS)
        layout.setSpacing(MAIN_GAP)
        marker = QHBoxLayout()
        marker.setSpacing(14)
        self.proposal_label = home_label("PROPOSAL 0/0", "cap")
        self.proposal_label.setObjectName("ProposalCount")
        self.data_tag = Tag("", "warning", mono=False)
        marker.addWidget(self.proposal_label)
        marker.addWidget(self.data_tag, 0, MIDDLE)
        marker.addStretch(1)
        layout.addLayout(marker)
        layout.addWidget(self._build_card())
        layout.addLayout(self._build_status())
        layout.addStretch(1)
        return main

    def _build_card(self) -> ProposalCard:
        self.card = ProposalCard()
        self.card.setObjectName("SuggestionCard")
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.card_body = QWidget()
        body = QVBoxLayout(self.card_body)
        body.setContentsMargins(*BODY_MARGINS)
        body.setSpacing(BODY_GAP)
        body.addWidget(self._build_title())
        self.sentence_label = home_label("", "sentence", wrap=True)
        self.sentence_label.setObjectName("SuggestionSentence")
        self.sentence_label.setTextFormat(Qt.TextFormat.RichText)
        self.sentence_label.setMaximumWidth(SENTENCE_WIDTH)
        body.addWidget(self.sentence_label)
        body.addWidget(self._build_risk())
        body.addWidget(self._build_chance())
        body.addWidget(self._build_why())
        self.empty_title = home_label("No trade suggestions right now", "empty_title", wrap=True)
        self.empty_title.setObjectName("NoSuggestion")
        self.empty_note = home_label(EMPTY_DISCONNECTED, "empty_note", wrap=True)
        body.addWidget(self.empty_title)
        body.addWidget(self.empty_note)
        layout.addWidget(self.card_body)
        layout.addWidget(self._build_footer())
        self.card.footer = self.card_footer
        return self.card

    def _build_title(self) -> QWidget:
        self.title_row = QWidget()
        row = QHBoxLayout(self.title_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.card_title = home_label("", "symbol")
        self.card_title.setObjectName("SuggestionTitle")
        self.card_title.setLayoutDirection(LTR)
        self.side_tag = SideTag()
        self.strategy_label = home_label("", "strategy")
        row.addWidget(self.card_title)
        row.addWidget(self.side_tag, 0, MIDDLE)
        row.addWidget(self.strategy_label)
        row.addStretch(1)
        return self.title_row

    def _build_risk(self) -> QWidget:
        self.risk_block = QWidget()
        self.risk_block.setLayoutDirection(LTR)
        box = QVBoxLayout(self.risk_block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6 - RISK_MARK)
        self.risk_bar = RiskBar()
        box.addWidget(self.risk_bar)
        captions = QHBoxLayout()
        captions.setSpacing(0)
        self.stop_cap = home_label("STOP LOSS", "cap")
        self.entry_cap = home_label("ENTRY \u00b7 R:R 1:", "cap")
        self.target_cap = home_label("TARGET", "cap")
        captions.addWidget(self.stop_cap)
        captions.addStretch(1)
        captions.addWidget(self.entry_cap)
        captions.addStretch(1)
        captions.addWidget(self.target_cap)
        box.addLayout(captions)
        return self.risk_block

    def _build_chance(self) -> QWidget:
        self.chance_block = QWidget()
        row = QHBoxLayout(self.chance_block)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(28)
        self.dots = ProbabilityDots()
        row.addWidget(self.dots, 0, MIDDLE)
        words = QVBoxLayout()
        words.setSpacing(0)
        value = QHBoxLayout()
        value.setSpacing(10)
        self.win_label = home_label(DASH, "win")
        self.win_label.setObjectName("WinChance")
        self.win_label.setLayoutDirection(LTR)
        self.win_note = home_label(WIN_NOTE, "note")
        value.addWidget(self.win_label, 0, Qt.AlignmentFlag.AlignBottom)
        value.addWidget(self.win_note, 0, Qt.AlignmentFlag.AlignBottom)
        value.addStretch(1)
        words.addLayout(value)
        words.addSpacing(4)
        self.range_label = home_label("", "range", wrap=True)
        self.range_label.setObjectName("WinRange")
        words.addWidget(self.range_label)
        words.addSpacing(14)
        legend = QHBoxLayout()
        legend.setSpacing(16)
        self.legend_labels: list[QLabel] = []
        for kind, text in (
            ("fill", "at least likely"),
            ("fade", "up to the estimate"),
            ("ring", "uncertainty"),
        ):
            item = QHBoxLayout()
            item.setSpacing(6)
            item.addWidget(LegendDot(kind), 0, MIDDLE)
            label = home_label(text, "legend")
            label.setProperty("source", text)
            self.legend_labels.append(label)
            item.addWidget(label)
            legend.addLayout(item)
        legend.addStretch(1)
        words.addLayout(legend)
        row.addLayout(words, 1)
        return self.chance_block

    def _build_why(self) -> QWidget:
        self.why_block = QWidget()
        box = QVBoxLayout(self.why_block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(home_label("WHY", "cap"))
        box.addSpacing(6)
        self.reasons_box = QVBoxLayout()
        self.reasons_box.setSpacing(0)
        box.addLayout(self.reasons_box)
        self.moved_label = home_label("", "warning", wrap=True)
        box.addWidget(self.moved_label)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 6, 0, 0)
        self.details_button = home_button("Show details", "link")
        self.details_button.setObjectName("DetailsButton")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        tools.addWidget(self.details_button)
        tools.addStretch(1)
        box.addLayout(tools)
        self.details_label = home_label("", "details", wrap=True)
        self.details_label.setObjectName("SuggestionDetails")
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details_label.setVisible(False)
        box.addWidget(self.details_label)
        return self.why_block

    def _build_footer(self) -> QWidget:
        self.card_footer = QWidget()
        row = QHBoxLayout(self.card_footer)
        row.setContentsMargins(*FOOTER_MARGINS)
        row.setSpacing(FOOTER_GAP)
        self.approve_button = HoldButton(HOLD_PAPER)
        self.approve_button.setObjectName("SimpleApproveButton")
        self.approve_button.held.connect(self.approve)
        self.skip_button = home_button("Skip", "skip")
        self.skip_button.setObjectName("SimpleSkipButton")
        self.skip_button.clicked.connect(self.skip)
        notes = QVBoxLayout()
        notes.setSpacing(2)
        self.ends_label = home_label("", "ends", wrap=True)
        self.block_label = home_label("", "warning", wrap=True)
        notes.addWidget(self.ends_label)
        notes.addWidget(self.block_label)
        row.addWidget(self.approve_button)
        row.addWidget(self.skip_button)
        row.addLayout(notes, 1)
        return self.card_footer

    def _build_status(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        self.state_line = home_label("", "state", wrap=True)
        self.state_line.setObjectName("HomeStatus")
        self.mode_line = home_label("", "status", wrap=True)
        self.mode_line.setObjectName("HomeModeLine")
        self.status_line = home_label("Status: not connected", "status", wrap=True)
        row = QHBoxLayout()
        self.status_bar_button = home_button("Show status bar", "link")
        self.status_bar_button.setObjectName("StatusBarToggle")
        self.status_bar_button.setCheckable(True)
        self.status_bar_button.toggled.connect(self._toggle_status_bar)
        row.addWidget(self.status_bar_button)
        row.addStretch(1)
        box.addWidget(self.state_line)
        box.addWidget(self.mode_line)
        box.addWidget(self.status_line)
        box.addLayout(row)
        return box

    def _build_aside(self) -> QWidget:
        aside = QWidget()
        aside.setObjectName("HomeAside")
        aside.setFixedWidth(ASIDE_WIDTH)
        layout = QVBoxLayout(aside)
        layout.setContentsMargins(*ASIDE_MARGINS)
        layout.setSpacing(ASIDE_GAP)
        self.trades_count = Tag("0", "neutral")
        self.trades_section = Section("Open trades", self.trades_count)
        self.trades_section.setObjectName("OpenTradesCard")
        layout.addWidget(self.trades_section)
        trades = QVBoxLayout()
        trades.setSpacing(0)
        self.trades_box = QVBoxLayout()
        self.trades_box.setSpacing(0)
        trades.addLayout(self.trades_box)
        self.no_trades = home_label("No open trades.", "no_trades", wrap=True)
        self.no_trades.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trades.addWidget(self.no_trades)
        day = QHBoxLayout()
        day.setContentsMargins(0, 14, 0, 0)
        day.addWidget(home_label("DAY P/L", "cap"), 0, Qt.AlignmentFlag.AlignBottom)
        day.addStretch(1)
        self.today_label = home_label(DASH, "day")
        self.today_label.setObjectName("TodayResult")
        self.today_label.setLayoutDirection(LTR)
        day.addWidget(self.today_label, 0, Qt.AlignmentFlag.AlignBottom)
        trades.addLayout(day)
        balance = QVBoxLayout()
        balance.setContentsMargins(0, 14, 0, 0)
        balance.setSpacing(4)
        self.balance_title = home_label("BALANCE", "cap")
        line = QHBoxLayout()
        self.balance_value = home_label("not known yet", "balance")
        self.balance_value.setObjectName("BalanceValue")
        self.week_label = home_label("", "small")
        self.week_label.setObjectName("WeekResult")
        line.addWidget(self.balance_value)
        line.addStretch(1)
        line.addWidget(self.week_label)
        balance.addWidget(self.balance_title)
        balance.addLayout(line)
        trades.addLayout(balance)
        layout.addLayout(trades)
        layout.addStretch(1)
        layout.addLayout(self._build_emergency())
        return aside

    def _build_emergency(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(0)
        box.addWidget(home_label("EMERGENCY", "cap"))
        box.addSpacing(8)
        self.stop_button = home_button(STOP_TEXT, "stop")
        self.stop_button.setObjectName("SimpleStopButton")
        self.stop_button.setIcon(stop_icon())
        self.stop_button.setIconSize(QSize(15, 15))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.confirm_stop)
        self.stop_cover = StopCover()
        self.stop_cover.setObjectName("StopCover")
        self.stop_cover.clicked.connect(self.arm_stop)
        self.emergency = EmergencyBox(self.stop_button, self.stop_cover)
        box.addWidget(self.emergency)
        keys = QHBoxLayout()
        keys.setContentsMargins(0, 8, 0, 0)
        kbd = home_label("Ctrl+Shift+K", "kbd")
        kbd.setLayoutDirection(LTR)
        keys.addWidget(kbd)
        keys.addStretch(1)
        self.cancel_button = home_button("Cancel", "link")
        self.cancel_button.setObjectName("StopCancel")
        self.cancel_button.clicked.connect(self.disarm_stop)
        policy = self.cancel_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.cancel_button.setSizePolicy(policy)
        self.cancel_button.setVisible(False)
        keys.addWidget(self.cancel_button)
        box.addLayout(keys)
        return box

    # Inputs ---------------------------------------------------------------------------------
    def show_welcome(self, visible: bool) -> None:
        """The first-run panel: practice money first, then Simple or Advanced (spec F0, F2)."""
        self.welcome_area.setVisible(visible)
        self.scroll_area.setVisible(not visible)

    def set_connection(self, connected: bool, text: str) -> None:
        self.connected = connected
        self.status_line.setText(text)
        self.refresh()

    def show_signals(self, snapshot: object) -> None:
        if isinstance(snapshot, SignalsSnapshot):
            self.last_signals = snapshot
            waiting = {record.id for record in snapshot.pending()}
            self._acted &= waiting
            self.refresh()

    def show_trading(self, snapshot: object) -> None:
        if isinstance(snapshot, ExecutionSnapshot):
            self.last_trading = snapshot
            self.refresh()

    def show_risk(self, snapshot: object) -> None:
        if isinstance(snapshot, RiskSnapshot):
            self.last_risk = snapshot
            self.refresh()

    def tick(self) -> None:
        """Every second from the window clock: the countdown and the price note."""
        self.refresh()

    def set_motion(self, motion: bool) -> None:
        """Reduced motion: the dots and the cover appear at once."""
        self.dots.motion = motion
        self.emergency.motion = motion

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.setStyleSheet(home_qss(tokens))
        for widget in self.findChildren(QWidget):
            apply = getattr(widget, "apply_tokens", None)
            if callable(apply):
                apply(tokens)
        self.refresh()

    def retranslate(self) -> None:
        """Show every text of the page in the chosen language (English: nothing to do)."""
        translate_widgets(self.translator, self)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self.toast.place()

    # Showing --------------------------------------------------------------------------------
    @property
    def persian(self) -> bool:
        return self.translator.right_to_left

    def word(self, english: str) -> str:
        """One of the design's own words in the chosen language."""
        return WORDS_FA.get(english, english) if self.persian else english

    def mode(self) -> OperatingMode:
        if self.trading is not None:
            return self.trading.settings.mode
        if self.last_trading is not None:
            return self.last_trading.mode
        return OperatingMode.PAPER

    def refresh(self) -> None:
        if self._asking:
            return  # a question is open: change nothing under it
        now = self.now()
        mode = self.mode()
        self._show_words(mode)
        self.mode_line.setText(MODE_LINES.get(mode, ""))
        usage = self.last_risk.usage if self.last_risk is not None else None
        currency = usage.currency if usage is not None else ""
        trading = self.last_trading or ExecutionSnapshot(mode=mode)
        balance = balance_view(usage, mode, trading.daily_results, now)
        self.balance_title.setText(balance.title.upper())
        self.balance_value.setText(balance.balance)
        self.week_label.setText(balance.week)
        set_tone(self.week_label, balance.week_kind)
        self._show_day(usage)
        waiting = self._show_suggestion(now, mode, usage)
        capital = usage.capital if usage is not None else math.nan
        self._show_trades(trading, capital, currency)
        open_trades = sum(1 for row in self.rows if not row.pending)
        self.trades_count.set(str(open_trades))
        self.state_line.setText(
            home_status(
                connected=self.connected,
                stopped=trading.stopped,
                halted=usage.halted if usage is not None else "",
                suggestions=waiting,
                open_trades=open_trades,
                market_open=market_open(now),
            ),
        )
        self.retranslate()

    def _show_words(self, mode: OperatingMode) -> None:
        word = self.word
        self.approve_button.setText(word(hold_label(mode)))
        self.approve_button.busy_text = word(HOLDING)
        self.skip_button.setText(word("Skip"))
        self.win_note.setText(word(WIN_NOTE))
        for label in self.legend_labels:
            label.setText(word(str(label.property("source"))))
        self.trades_section.title.setText(word("Open trades"))
        self.no_trades.setText(word("No open trades."))
        self.stop_button.setText(word(STOP_TEXT))
        self.stop_cover.setText(word(COVER_TEXT))
        self.stop_cover.setAccessibleName(word(COVER_NAME))
        self.cancel_button.setText(word("Cancel"))
        running = self.trading is not None
        self.stop_button.setToolTip(STOP_TIP if running else NOTHING_RUNNING)
        self.stop_cover.setToolTip(STOP_TIP if running else NOTHING_RUNNING)
        self.stop_cover.setEnabled(running and not self.armed)
        self.stop_button.setEnabled(running and self.armed)

    def _show_day(self, usage: RiskUsage | None) -> None:
        """DAY P/L: the equity now against the day's start (closed and open results)."""
        start = usage.day_start_equity if usage is not None else 0.0
        if usage is None or start <= 0 or not math.isfinite(usage.equity):
            self.today_label.setText(DASH)
            self.today_label.setToolTip("")
            set_tone(self.today_label, "muted")
            return
        change = usage.equity - start
        self.today_label.setText(money(change, usage.currency, signed=True))
        self.today_label.setToolTip(f"{change / start * 100.0:+.2f}%")
        set_tone(self.today_label, kind_of(change) if change else "")

    def _show_suggestion(
        self,
        now: float,
        mode: OperatingMode,
        usage: RiskUsage | None,
    ) -> int:
        snapshot = self.last_signals
        records = snapshot.pending() if snapshot is not None else []
        ordered = ordered_suggestions(records, self._acted)
        record = ordered[0] if ordered else None
        self.suggestion = record
        shown = record is not None
        live = self.connected and self.signals_context is not None
        self.proposal_label.setText(f"PROPOSAL {1 if shown else 0}/{len(ordered)}")
        tag = "LIVE" if live else "WAITING FOR DATA"
        self.data_tag.set(self.word(tag), "profit" if live else "warning")
        for part in (
            self.title_row,
            self.sentence_label,
            self.risk_block,
            self.chance_block,
            self.why_block,
            self.card_footer,
        ):
            part.setVisible(shown)
        self.details_label.setVisible(shown and self.details_button.isChecked())
        self.empty_title.setVisible(not shown)
        self.empty_note.setVisible(not shown)
        self.empty_note.setText(EMPTY_CONNECTED if self.connected else EMPTY_DISCONNECTED)
        self.card.update()
        if record is None or snapshot is None:
            self.view = None
            self.sentence_text = ""
            return 0
        currency = usage.currency if usage is not None else ""
        signal = record.signal
        quote = self.quote(signal.symbol)
        moved = price_moved(
            record,
            quote.bid if quote is not None else None,
            quote.ask if quote is not None else None,
            self._tolerance(),
        )
        kind = STRATEGIES.get(signal.strategy)
        title = kind.title if kind is not None else ""
        view = suggestion_view(
            record,
            currency,
            now,
            total=len(ordered),
            moved=moved,
            strategy_title=title,
        )
        self.view = view
        action, _ = action_text(signal.direction)
        self.card_title.setText(ticker_symbol(signal.symbol))
        self.card_title.setToolTip(view.title)
        self.side_tag.set(self.word(action), "up" if action == "Buy" else "down")
        strategy = STRATEGY_FA.get(signal.strategy, "") if self.persian else ""
        self.strategy_label.setText(strategy or title or signal.strategy)
        self._show_sentence(record, currency, action)
        self._show_risk(record, currency)
        self._show_chance(record)
        self._show_reasons(record, usage)
        self.moved_label.setText(view.moved)
        self.moved_label.setVisible(bool(view.moved))
        self.ends_label.setText(view.ends)
        block = approve_block_text(snapshot.approval_block, mode)
        if self.signals_context is None:
            block = "Approve is not available: the signals are not running."
        self.block_label.setText(block)
        self.block_label.setVisible(bool(block))
        self.approve_button.setEnabled(not block and signal.expires_at > now)
        self.skip_button.setEnabled(self.signals_context is not None)
        self.details_label.setText("\n".join(f"{name}: {text}" for name, text in view.details))
        return len(ordered)

    def _show_sentence(self, record: SignalRecord, currency: str, action: str) -> None:
        """The design's sentence: the size, then the money at stake on both sides."""
        risk = record.risk_money
        if risk is None or not math.isfinite(risk) or record.volume is None:
            self.sentence_text = self.word(UNKNOWN_SIZE)
            self.sentence_label.setText(html.escape(self.sentence_text))
            return
        fa = self.persian
        t = self.tokens
        values = {
            "verb": VERBS_FA.get(action, action) if fa else action,
            "symbol": ltr(ticker_symbol(record.signal.symbol), fa),
            "lot": ltr(f"{record.volume:.2f}", fa),
            "lose": ltr(money(abs(risk), currency), fa),
            "make": ltr(money(abs(risk) * record.signal.rr, currency), fa),
        }
        template = SENTENCE_FA if fa else SENTENCE_EN
        self.sentence_text = template.format(**values)
        colors = {"lot": t.text, "lose": t.loss, "make": t.profit}
        rich = {name: html.escape(text) for name, text in values.items()}
        for name, color in colors.items():
            rich[name] = f'<b style="font-weight:600;color:{color}">{rich[name]}</b>'
        self.sentence_label.setText(html.escape(template).format(**rich))

    def _show_risk(self, record: SignalRecord, currency: str) -> None:
        signal = record.signal
        rr = signal.rr
        risk = record.risk_money
        if risk is None or not math.isfinite(risk):
            lose, make = DASH, DASH
        else:
            lose = money(-abs(risk), currency)
            make = money(abs(risk) * rr, currency, signed=True)
        self.risk_bar.set(loss_share(rr), lose, make)
        ratio = f"{rr:.1f}" if math.isfinite(rr) and rr > 0 else DASH
        self.entry_cap.setText(f"ENTRY \u00b7 R:R 1:{ratio}")
        self.stop_cap.setToolTip(signal.price(signal.sl))
        self.entry_cap.setToolTip(signal.price(signal.entry))
        self.target_cap.setToolTip(signal.price(signal.tp))

    def _show_chance(self, record: SignalRecord) -> None:
        estimate = record.probability
        bounds = percent_bounds(estimate)
        self.dots.set(bounds, record.id)
        fa = self.persian
        if bounds is None:
            self.win_label.setText(DASH)
            template = UNKNOWN_FA if fa else UNKNOWN_EN
            text = template.format(
                count=ltr(str(estimate.samples), fa),
                needed=ltr(str(estimate.min_samples), fa),
            )
        else:
            low, value, high = bounds
            self.win_label.setText(f"{value}%")
            template = RANGE_FA if fa else RANGE_EN
            span = ltr(f"{low}\u2013{high}%", fa)
            text = template.format(range=span, count=ltr(str(estimate.samples), fa))
        self.range_label.setText(text)

    def reasons_for(self, record: SignalRecord, usage: RiskUsage | None) -> list[str]:
        """WHY: only what is true of this signal, at most three lines.

        The strategy's setup in plain words, the share of the money at risk when the account
        is known, and that it passed the filters (a waiting signal has: the others are
        filtered out before anyone sees them).
        """
        fa = self.persian
        reasons = [plain_reason(record)]
        risk = record.risk_money
        capital = usage.capital if usage is not None else math.nan
        if risk is not None and math.isfinite(risk) and capital > 0 and math.isfinite(capital):
            share = ltr(f"{abs(risk) / capital * 100.0:.2f}%", fa)
            reasons.append((RISK_FA if fa else RISK_EN).format(percent=share))
        reasons.append(self.word(FILTERS_PASSED))
        return reasons[:REASONS]

    def _show_reasons(self, record: SignalRecord, usage: RiskUsage | None) -> None:
        reasons = self.reasons_for(record, usage)
        if reasons == self._reasons:
            return
        self._reasons = reasons
        while self.reasons_box.count():
            item = self.reasons_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.reason_labels = []
        for text in reasons:
            frame = QFrame()
            frame.setProperty("home", "reason_row")
            line = QHBoxLayout(frame)
            line.setContentsMargins(0, 6, 0, 6)
            line.setSpacing(10)
            mark = Icon("check", 15, "profit")
            mark.apply_tokens(self.tokens)
            line.addWidget(mark, 0, MIDDLE)
            label = home_label(text, "reason", wrap=True)
            line.addWidget(label, 1)
            self.reason_labels.append(label)
            self.reasons_box.addWidget(frame)

    def _show_trades(self, trading: ExecutionSnapshot, capital: float, currency: str) -> None:
        rows = trade_rows(trading, capital, currency)
        views = list(trading.positions)
        keys = [(row.mode, row.ticket, row.pending) for row in rows]
        self.rows = rows
        self.no_trades.setVisible(not rows)
        if keys == self._row_keys:
            for (icon, label), row, view in zip(self._row_results, rows, views, strict=True):
                self._show_result(icon, label, row, view, currency)
            return
        while self.trades_box.count():
            item = self.trades_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._row_keys = keys
        self._row_results = []
        self.close_buttons = []
        for row, view in zip(rows, views, strict=True):
            self.trades_box.addWidget(self._trade_row(row, view, currency))

    def _trade_row(self, row: TradeRow, view: PositionView, currency: str) -> QFrame:
        """A row of the design's open trades: the symbol, side and size, the result."""
        frame = QFrame()
        frame.setProperty("home", "trade")
        line = QHBoxLayout(frame)
        line.setContentsMargins(0, 12, 0, 12)
        line.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(2)
        symbol = home_label(ticker_symbol(view.symbol), "trade_sym")
        symbol.setToolTip(row.title)
        left.addWidget(symbol)
        below = QHBoxLayout()
        below.setSpacing(8)
        action, _ = action_text(view.direction)
        side = f"{self.word(action)} \u00b7 {ltr(f'{view.volume:.2f}', self.persian)}"
        below.addWidget(home_label(side, "trade_side"))
        below.addStretch(1)
        if not row.pending:
            button = home_button("Close now", "link")
            button.setEnabled(self.trading is not None)
            button.clicked.connect(self._close_slot(row.mode, row.ticket))
            below.addWidget(button)
            self.close_buttons.append(button)
        left.addLayout(below)
        line.addLayout(left, 1)
        result = QHBoxLayout()
        result.setSpacing(5)
        icon = Icon("up", 14, "")
        icon.apply_tokens(self.tokens)
        label = home_label("", "trade_pl")
        label.setLayoutDirection(LTR)
        result.addWidget(icon, 0, MIDDLE)
        result.addWidget(label)
        line.addLayout(result)
        self._row_results.append((icon, label))
        self._show_result(icon, label, row, view, currency)
        return frame

    def _show_result(
        self,
        icon: Icon,
        label: QLabel,
        row: TradeRow,
        view: PositionView,
        currency: str,
    ) -> None:
        label.setToolTip(row.result)
        if row.pending or view.profit is None:
            icon.setVisible(False)
            set_role(label, "pending")
            label.setText(row.result)
            set_tone(label, "")
            return
        set_role(label, "trade_pl")
        tone = kind_of(view.profit)
        icon.setVisible(tone != "muted")
        icon.set("up" if view.profit >= 0 else "down", tone)
        label.setText(money(view.profit, currency, signed=True))
        set_tone(label, tone)

    def _tolerance(self) -> float:
        if self.trading is None:
            return DEFAULT_TOLERANCE_R
        return self.trading.settings.config.settings.max_entry_move_r

    def default_texts(self) -> list[str]:
        """What the screen says before Details is opened (the zero-jargon check).

        The design's own captions (STOP LOSS, ENTRY, R:R, TARGET, WHY, DAY P/L) are left out:
        they are the owner's chosen labels, not sentences.
        """
        texts = [self.mode_line.text(), self.balance_value.text(), self.today_label.text()]
        texts += [self.week_label.text(), self.state_line.text()]
        if self.view is not None:
            texts += [self.view.title, self.side_tag.text]  # the strategy is its name
            texts += [self.sentence_text, self.win_note.text(), self.range_label.text()]
            texts += [label.text() for label in self.reason_labels]
            texts += [self.ends_label.text(), self.moved_label.text(), self.block_label.text()]
        else:
            texts += [self.empty_title.text(), self.empty_note.text()]
        for row in self.rows:
            texts += row.texts()
        return [text for text in texts if text]

    # Actions --------------------------------------------------------------------------------
    def say(self, text: str) -> None:
        """The toast: what an action did, for 2.6 s."""
        self.toast.say(self.translator.text(text))

    def _ask(self, title: str, text: str) -> bool:
        self._asking = True
        try:
            return self.confirm(self.translator.text(title), self.translator.text(text))
        finally:
            self._asking = False

    def approve(self) -> bool:
        """Ask, then hand the suggestion to the same approval as the Signals page."""
        record, view = self.suggestion, self.view
        if record is None or view is None or self.signals_context is None:
            return False
        snapshot = self.last_signals
        if snapshot is None or snapshot.approval_block or record.signal.expires_at <= self.now():
            return False
        if not self._ask("Approve this trade?", approve_question(view, self.mode())):
            return False
        self.signals_context.pipeline.approve(record.id)
        audit("signal approved", before=record.signal.state.value, after=record.id)
        self._acted.add(record.id)
        self.refresh()
        done = "Approved: it is checked again and placed in a few seconds."
        self.state_line.setText(done)
        self.say(done)
        self.retranslate()
        return True

    def skip(self) -> bool:
        record = self.suggestion
        if record is None or self.signals_context is None:
            return False
        self.signals_context.pipeline.dismiss(record.id)
        audit("signal dismissed", before=record.signal.state.value, after=record.id)
        self._acted.add(record.id)
        self.refresh()
        return True

    def close_trade(self, mode: str, ticket: int) -> bool:
        row = next((r for r in self.rows if r.mode == mode and r.ticket == ticket), None)
        if row is None or row.pending or self.trading is None:
            return False
        if not self._ask("Close this trade?", close_question(row)):
            return False
        self.trading.engine.request_close(mode, ticket)
        audit("position close requested", before=None, after={"ticket": ticket})
        done = "Closing: done in a few seconds."
        self.state_line.setText(done)
        self.say(done)
        self.retranslate()
        return True

    def arm_stop(self) -> bool:
        """Open the cover of the emergency button (nothing is stopped yet)."""
        if self.trading is None:
            return False
        self._set_armed(True)
        self.stop_button.setFocus()
        return True

    def disarm_stop(self) -> None:
        self._set_armed(False)

    def _set_armed(self, armed: bool) -> None:
        self.armed = armed
        self.emergency.set_armed(armed)
        self.cancel_button.setVisible(armed)
        running = self.trading is not None
        self.stop_button.setEnabled(running and armed)
        self.stop_cover.setEnabled(running and not armed)

    def confirm_stop(self) -> bool:
        """The opened emergency button: the kill switch (it asks), then the cover closes."""
        if not self.armed:
            return False
        stopped = self.stop_trading()
        self._set_armed(False)
        if stopped:
            self.say(self.word(STOP_SENT))
        return stopped

    def stop_trading(self) -> bool:
        """The kill switch, exactly as in the Advanced view (it asks first)."""
        if self.stop is None:
            return False
        self._asking = True
        try:
            stopped = self.stop()
        finally:
            self._asking = False
        self.refresh()
        return stopped

    def answer_welcome(self, advanced: bool) -> None:
        self.show_welcome(False)
        audit("first-run screen answered", before=None, after={"advanced": advanced})
        if self.on_onboarded is not None:
            self.on_onboarded(advanced)

    def _welcome_slot(self, advanced: bool) -> Callable[[], None]:
        def slot() -> None:
            self.answer_welcome(advanced)

        return slot

    def _close_slot(self, mode: str, ticket: int) -> Callable[[], None]:
        def slot() -> None:
            self.close_trade(mode, ticket)

        return slot

    def _toggle_details(self, shown: bool) -> None:
        self.details_button.setText("Hide details" if shown else "Show details")
        self.details_label.setVisible(shown and self.suggestion is not None)
        self.retranslate()

    def _toggle_status_bar(self, shown: bool) -> None:
        self.status_bar_button.setText("Hide status bar" if shown else "Show status bar")
        if self.on_status_bar is not None:
            self.on_status_bar(shown)
        self.retranslate()
