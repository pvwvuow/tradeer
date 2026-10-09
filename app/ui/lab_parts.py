"""The building blocks of the No Curve v2 AI Lab, drawn like the owner's design (Phase 20e).

- `StepStrip`: the four steps of the loop (Export, Paste, Run comparison, Activate in Paper);
  a step turns green only when it is really done.
- `LabCard`: the design's result card (`.tc`): a header with an icon, the title, tags and a
  note, then rows of 40 px, tables, checks and a footer.
- `GridTable`: the design's grid rows (a header of captions, then 42 px rows) for the
  suggested changes and the comparison.
- `ComposerBox`, `SendButton`, `Skeleton`, `RailButton`, `FlowLayout`: the composer with its
  glow while typing and breathing while busy, the send and stop button, the loading bars,
  the inspector's rail buttons and the wrapping rows of suggestions.

Everything paints with the theme's tokens (`apply_tokens`). Numbers, symbols, file names,
URLs and captions stay left to right in Persian, as in the design. `LAB_FA` holds the
design's Persian words.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.shell import Painted
from app.ui.theme import DEFAULT, NUMBER_FONT, ThemeTokens, mix, number_family, px
from app.ui.v2 import ICONS as V2_ICONS
from app.ui.v2 import Tag, draw_icon

LTR = Qt.LayoutDirection.LeftToRight
RTL = Qt.LayoutDirection.RightToLeft
MIDDLE = Qt.AlignmentFlag.AlignVCenter
STEP_SIZE = 24
STEP_NAMES = ("Export for AI", "Paste the answer", "Run comparison", "Activate in Paper")
ROW_HEIGHT = 40
GRID_ROW = 42
SEND_SIZE = 34
RAIL_SIZE = 40
TYPING_MS = 800
BREATHE_MS = 2400
SKELETON_MS = 1600
CHAT_WIDTH = 760
Margins = tuple[int, int, int, int]
LAB_FA: dict[str, str] = {
    "AI Lab": "آزمایشگاه AI",
    "Export for AI": "Export for AI",
    "Paste the answer": "Paste the answer",
    "Run comparison": "Run comparison",
    "Activate in Paper": "Activate in Paper",
    "What shall we look at?": "چه چیزی را بررسی کنیم؟",
    "From charts and tables to the comparison of an AI suggestion. Pick one or write.": (
        "از نمودار و جدول و مونت‌کارلو تا مقایسه‌ی پیشنهاد AI. یکی را انتخاب کنید یا بنویسید."
    ),
    "Ask, ask for a chart, or paste the AI's answer here...": (
        "سؤال بپرسید، نمودار بخواهید یا پاسخ AI را اینجا بچسبانید..."
    ),
    "The AI only advises. No real order is ever sent from here.": (
        "AI فقط مشاوره می‌دهد. سفارش واقعی از اینجا هرگز ارسال نمی‌شود."
    ),
    "Your message": "پیام شما",
    "Send": "ارسال",
    "Stop": "توقف",
    "Attach a file": "پیوست فایل",
    "Attaching files comes later.": "پیوست فایل بعداً اضافه می‌شود.",
    "New chat": "گفتگوی جدید",
    "Stopped.": "متوقف شد.",
    "Settings": "تنظیمات",
    "History": "تاریخچه",
    "Prompts": "پرامپت‌ها",
    "Files": "فایل‌ها",
    "Experiments": "آزمایش‌ها",
    "Usage": "مصرف",
    "Coming in the next version.": "در نسخه‌ی بعدی می‌آید.",
}


def lab_words(persian: bool) -> Callable[[str], str]:
    """The design's word for an English text in the chosen language."""

    def word(english: str) -> str:
        return LAB_FA.get(english, english) if persian else english

    return word


def lab_qss(tokens: ThemeTokens) -> str:
    """The AI Lab's own rules, on top of the frame's stylesheet."""
    t = tokens
    mono = number_family()
    return f"""
QFrame[lab="card"] {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 8px;
}}
QFrame[lab="card"] QWidget, QFrame[lab="aside"] QWidget, QFrame[lab="strip"] QWidget,
QFrame[lab="verdict"] QWidget {{
    background: transparent;
}}
QFrame[lab="aside"] {{
    background-color: {t.surface};
    border: 1px solid {t.border};
}}
QFrame[lab="strip"] {{
    background: transparent;
    border: 1px solid {t.border};
}}
QFrame[lab="row"] {{
    background: transparent;
    border: none;
    border-top: 1px solid {t.border};
}}
QFrame[lab="grid_head"] {{
    background: transparent;
    border: none;
    border-top: 1px solid {t.border};
    border-bottom: 1px solid {t.border_strong};
}}
QFrame[lab="verdict"] {{
    background: transparent;
    border: 1px solid {t.border_strong};
    border-radius: 8px;
}}
QFrame[lab="verdict"][tone="warning"] {{
    border-color: {t.warning};
}}
QFrame[lab="verdict"][tone="profit"] {{
    border-color: {t.profit};
}}
QLabel[lab="card_title"] {{
    color: {t.text};
    font-size: {px(13):g}pt;
    font-weight: 600;
}}
QLabel[lab="note"], QLabel[lab="footer"] {{
    color: {t.text_secondary};
    font-size: {px(12):g}pt;
}}
QLabel[lab="muted"] {{
    color: {t.text_secondary};
    font-size: {px(13):g}pt;
}}
QLabel[lab="text"] {{
    color: {t.text};
    font-size: {px(13):g}pt;
}}
QLabel[lab="mono"] {{
    color: {t.text};
    font-family: {mono};
    font-size: {px(13):g}pt;
}}
QLabel[lab="cap"] {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {px(11):g}pt;
}}
QLabel[lab="step"] {{
    color: {t.text};
    font-size: {px(13):g}pt;
}}
QLabel[lab="step"][state="current"] {{
    font-weight: 600;
}}
QLabel[lab="step"][state="later"] {{
    color: {t.text_secondary};
}}
QLabel[lab="bubble"] {{
    background-color: {t.hover};
    border: 1px solid {t.border_strong};
    padding: 12px 18px;
    font-size: {px(15):g}pt;
}}
QLabel[lab="answer"] {{
    color: {t.text};
    font-size: {px(15):g}pt;
}}
QLabel[lab="welcome"] {{
    font-size: {px(26):g}pt;
    font-weight: 700;
}}
QLabel[lab="sub"] {{
    color: {t.text_secondary};
    font-size: {px(14):g}pt;
}}
QLabel[lab="heading"] {{
    font-size: {px(14):g}pt;
    font-weight: 600;
}}
QLabel[lab="profit"] {{
    color: {t.profit};
    font-size: {px(13):g}pt;
}}
QLabel[lab="loss"] {{
    color: {t.loss};
    font-size: {px(13):g}pt;
}}
QLabel[lab="mono"][tone="profit"] {{
    color: {t.profit};
}}
QLabel[lab="mono"][tone="loss"] {{
    color: {t.loss};
}}
QPushButton[lab="sg"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    padding: 7px 12px;
    min-height: 0px;
    font-size: {px(13):g}pt;
    font-weight: 400;
}}
QPushButton[lab="sg"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[lab="sg"]:disabled {{
    color: {t.border_strong};
}}
QPushButton[lab="primary"] {{
    background-color: {t.text};
    color: {t.bg};
    border: 1px solid {t.text};
    border-radius: 6px;
    padding: 0px 16px;
    min-height: 36px;
    max-height: 38px;
    font-size: {px(14):g}pt;
    font-weight: 500;
}}
QPushButton[lab="primary"]:disabled {{
    background-color: {t.border_strong};
    border-color: {t.border_strong};
    color: {t.surface};
}}
QPushButton[lab="ghost"] {{
    background-color: transparent;
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    padding: 0px 16px;
    min-height: 36px;
    max-height: 38px;
    font-size: {px(14):g}pt;
    font-weight: 500;
}}
QPushButton[lab="ghost"]:hover {{
    background-color: {t.hover};
    border-color: {t.text_secondary};
}}
QPushButton[lab="ghost"]:disabled {{
    color: {t.text_secondary};
    border-color: {t.border};
}}
QPushButton[lab="link"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px 6px;
    min-height: 0px;
    font-size: {px(13):g}pt;
    font-weight: 400;
    text-decoration: underline;
}}
QPushButton[lab="link"]:hover {{
    color: {t.text};
}}
QFrame[lab="composer"] {{
    background-color: {t.surface};
    border: 1px solid {t.border_strong};
}}
QFrame[lab="composer"][state="focus"] {{
    border-color: {t.text_secondary};
}}
QFrame[lab="composer"][state="typing"] {{
    border-color: {t.text};
}}
QFrame[lab="composer"][state="busy"] {{
    border-color: {mix(t.surface, t.text, 0.3)};
}}
QFrame[lab="composer"] QWidget {{
    background: transparent;
}}
QPlainTextEdit[lab="input"] {{
    background: transparent;
    border: none;
    color: {t.text};
    font-size: {px(15):g}pt;
    padding: 0px;
}}
QLineEdit[lab="in"], QSpinBox[lab="in"], QComboBox[lab="in"], QDateEdit[lab="in"] {{
    min-height: 38px;
    max-height: 40px;
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    background: transparent;
    font-family: {mono};
    font-size: {px(13):g}pt;
    padding: 0px 12px;
}}
QLabel[lab="mono"][size="big"] {{
    font-size: {px(24):g}pt;
    font-weight: 600;
}}
QLabel[lab="mono"][size="small"] {{
    font-size: {px(12):g}pt;
}}
QProgressBar[lab="progress"] {{
    background-color: {t.hover};
    border: none;
    border-radius: 2px;
}}
QProgressBar[lab="progress"]::chunk {{
    background-color: {t.text};
    border-radius: 2px;
}}
QFrame[lab="vrule"] {{
    background-color: {t.border};
    border: none;
}}
QPushButton[lab="item"] {{
    background-color: transparent;
    color: {t.text};
    border: none;
    border-radius: 6px;
    padding: 8px 10px;
    text-align: left;
    font-size: {px(13):g}pt;
    font-weight: 400;
    min-height: 0px;
}}
QPushButton[lab="item"]:hover {{
    background-color: {t.hover};
}}
QPushButton[lab="item"][current="true"] {{
    background-color: {t.hover};
    border-left: 3px solid {t.text};
}}
QLabel[lab="label"] {{
    color: {t.text_secondary};
    font-size: {px(12):g}pt;
}}
"""


# Icons -------------------------------------------------------------------------------------
# The design's line icons on a 24 by 24 grid, as painter paths (the arrows, the check and the
# cross come from the Dashboard's set).
def _path(build: Callable[[QPainterPath], None]) -> Callable[[], QPainterPath]:
    def make() -> QPainterPath:
        path = QPainterPath()
        build(path)
        return path

    return make


def _file(path: QPainterPath) -> None:
    path.moveTo(14, 3)
    path.lineTo(7, 3)
    path.quadTo(5, 3, 5, 5)
    path.lineTo(5, 19)
    path.quadTo(5, 21, 7, 21)
    path.lineTo(17, 21)
    path.quadTo(19, 21, 19, 19)
    path.lineTo(19, 8)
    path.closeSubpath()
    path.moveTo(14, 3)
    path.lineTo(14, 8)
    path.lineTo(19, 8)


def _chat(path: QPainterPath) -> None:
    path.moveTo(4, 5)
    path.lineTo(20, 5)
    path.lineTo(20, 16)
    path.lineTo(10, 16)
    path.lineTo(5, 20)
    path.lineTo(5, 16)
    path.lineTo(4, 16)
    path.closeSubpath()


def _paste(path: QPainterPath) -> None:
    path.addRoundedRect(QRectF(5, 4, 14, 17), 2, 2)
    path.addRoundedRect(QRectF(9, 2, 6, 4), 1, 1)


def _compare(path: QPainterPath) -> None:
    for x, top in ((6, 12), (12, 6), (18, 9)):
        path.moveTo(x, 20)
        path.lineTo(x, top)


def _play(path: QPainterPath) -> None:
    path.moveTo(7, 5)
    path.lineTo(19, 12)
    path.lineTo(7, 19)
    path.closeSubpath()


def _gear(path: QPainterPath) -> None:
    path.addEllipse(QPointF(12, 12), 3, 3)
    path.addEllipse(QPointF(12, 12), 7.5, 7.5)
    for index in range(8):
        angle = index * math.pi / 4
        path.moveTo(12 + 7.5 * math.cos(angle), 12 + 7.5 * math.sin(angle))
        path.lineTo(12 + 10 * math.cos(angle), 12 + 10 * math.sin(angle))


def _clock(path: QPainterPath) -> None:
    path.addEllipse(QPointF(12, 12), 9, 9)
    path.moveTo(12, 7)
    path.lineTo(12, 12)
    path.lineTo(15.5, 14)


def _bookmark(path: QPainterPath) -> None:
    path.moveTo(6, 3)
    path.lineTo(18, 3)
    path.lineTo(18, 21)
    path.lineTo(12, 17)
    path.lineTo(6, 21)
    path.closeSubpath()


def _flask(path: QPainterPath) -> None:
    path.moveTo(9, 3)
    path.lineTo(15, 3)
    path.moveTo(10, 3)
    path.lineTo(10, 9)
    path.lineTo(5, 18)
    path.quadTo(4, 21, 7, 21)
    path.lineTo(17, 21)
    path.quadTo(20, 21, 19, 18)
    path.lineTo(14, 9)
    path.lineTo(14, 3)
    path.moveTo(7, 15)
    path.lineTo(17, 15)


def _bars(path: QPainterPath) -> None:
    for x, top in ((5, 13), (10, 6), (15, 15), (20, 9)):
        path.moveTo(x, 20)
        path.lineTo(x, top)


def _clip(path: QPainterPath) -> None:
    path.moveTo(16, 7)
    path.lineTo(8.5, 14.5)
    path.cubicTo(7, 16, 9, 18, 10.5, 16.5)
    path.lineTo(18, 9)
    path.cubicTo(21, 6, 17, 2, 14, 5)
    path.lineTo(6, 13)
    path.cubicTo(2, 17, 7, 22, 11, 18)
    path.lineTo(17, 12)


LAB_ICONS: dict[str, Callable[[], QPainterPath]] = {
    "file": _path(_file),
    "chat": _path(_chat),
    "paste": _path(_paste),
    "compare": _path(_compare),
    "play": _path(_play),
    "gear": _path(_gear),
    "clock": _path(_clock),
    "bookmark": _path(_bookmark),
    "flask": _path(_flask),
    "bars": _path(_bars),
    "clip": _path(_clip),
}


def draw_lab_icon(
    painter: QPainter,
    name: str,
    box: QRectF,
    color: str,
    stroke: float = 1.7,
) -> None:
    """Paint the design's icon `name` into the square `box` in `color`."""
    if name in V2_ICONS:
        draw_icon(painter, name, box, color)
        return
    make = LAB_ICONS.get(name)
    if make is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(box.topLeft())
    scale = box.width() / 24
    painter.scale(scale, scale)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(make())
    painter.restore()


class LabIcon(Painted):
    """A line icon of the AI Lab in a tone: muted (the default), ink, profit or loss."""

    def __init__(self, name: str, size: int = 15, tone: str = "muted") -> None:
        super().__init__()
        self.name = name
        self.tone = tone
        self.setFixedSize(size, size)

    def set(self, name: str, tone: str) -> None:
        self.name = name
        self.tone = tone
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        colors = {"ink": t.text, "profit": t.profit, "loss": t.loss, "warning": t.warning}
        color = colors.get(self.tone, t.text_secondary)
        painter = QPainter(self)
        draw_lab_icon(painter, self.name, QRectF(self.rect()), color)
        painter.end()


def lab_label(text: str, role: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("lab", role)
    label.setWordWrap(wrap)
    return label


def lab_button(text: str, role: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("lab", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def mono_label(text: str, tone: str = "") -> QLabel:
    """A number, a symbol, a file name or a URL: the mono face, always left to right."""
    label = lab_label(text, "mono")
    label.setLayoutDirection(LTR)
    label.setProperty("tone", tone)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def restyle(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def apply_tree(root: QWidget, tokens: ThemeTokens) -> None:
    """Give every painted piece under `root` the theme's tokens."""
    for widget in [root, *root.findChildren(QWidget)]:
        apply = getattr(widget, "apply_tokens", None)
        if callable(apply) and widget is not root:
            apply(tokens)


@dataclass(frozen=True)
class Cell:
    value: str

    def text(self) -> str:
        return self.value


# The four steps ----------------------------------------------------------------------------
class StepDot(Painted):
    """A step's circle (24 px): green with a check when done, the number otherwise."""

    def __init__(self, number: int) -> None:
        super().__init__()
        self.number = number
        self.state = "later"  # done, current or later
        self.setFixedSize(STEP_SIZE, STEP_SIZE)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0.5, 0.5, STEP_SIZE - 1, STEP_SIZE - 1)
        if self.state == "done":
            painter.setPen(QPen(QColor(t.profit), 1))
            painter.setBrush(QColor(t.profit))
            painter.drawEllipse(box)
            draw_icon(painter, "check", QRectF(5.5, 5.5, 13, 13), t.bg, 3.0)
        else:
            ink = t.text if self.state == "current" else t.border_strong
            painter.setPen(QPen(QColor(ink), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box)
            font = QFont(self.font())
            font.setFamily(NUMBER_FONT)
            font.setPointSizeF(px(12))
            painter.setFont(font)
            painter.setPen(QColor(t.text if self.state == "current" else t.text_secondary))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, str(self.number))
        painter.end()


class StepStrip(QFrame):
    """The design's strip of the four steps, in reading order, joined by rules."""

    def __init__(self, names: Sequence[str] = STEP_NAMES) -> None:
        super().__init__()
        self.setProperty("lab", "strip")
        self.setObjectName("AiLabSteps")
        row = QHBoxLayout(self)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(14)
        self.dots: list[StepDot] = []
        self.labels: list[QLabel] = []
        for index, name in enumerate(names):
            if index:
                rule = QFrame()
                rule.setFixedHeight(1)
                rule.setProperty("lab", "rule")
                rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                row.addWidget(rule, 1, MIDDLE)
            item = QHBoxLayout()
            item.setSpacing(8)
            dot = StepDot(index + 1)
            label = lab_label(name, "step")
            label.setProperty("state", "later")
            item.addWidget(dot, 0, MIDDLE)
            item.addWidget(label, 0, MIDDLE)
            row.addLayout(item)
            self.dots.append(dot)
            self.labels.append(label)
        self.done: tuple[bool, ...] = tuple(False for _ in names)

    def set_done(self, done: Sequence[bool]) -> None:
        """Mark the steps that are really done; the first one that is not is the current."""
        self.done = tuple(done)
        current = next((index for index, flag in enumerate(self.done) if not flag), -1)
        for index, (dot, label) in enumerate(zip(self.dots, self.labels, strict=True)):
            state = "done" if self.done[index] else "current" if index == current else "later"
            dot.set_state(state)
            if label.property("state") != state:
                label.setProperty("state", state)
                restyle(label)

    def set_names(self, names: Sequence[str]) -> None:
        for label, name in zip(self.labels, names, strict=True):
            label.setText(name)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        for dot in self.dots:
            dot.apply_tokens(tokens)
        for rule in self.findChildren(QFrame):
            if rule.property("lab") == "rule":
                rule.setStyleSheet(f"background-color: {tokens.border_strong}; border: none;")


# Cards -------------------------------------------------------------------------------------
class LabCard(QFrame):
    """The design's result card: header (icon, bold title, tags, a note at the end), then
    rows, tables or checks, each section under a hairline."""

    def __init__(self, icon: str, title: str, note: str = "") -> None:
        super().__init__()
        self.setProperty("lab", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        head = QWidget()
        self.head = QHBoxLayout(head)
        self.head.setContentsMargins(16, 11, 16, 11)
        self.head.setSpacing(10)
        self.icon = LabIcon(icon, 15)
        self.title = lab_label(title, "card_title")
        self.head.addWidget(self.icon, 0, MIDDLE)
        self.head.addWidget(self.title, 0, MIDDLE)
        self.tags: list[Tag] = []
        self.head.addStretch(1)
        self.note = lab_label(note, "note")
        self.note.setVisible(bool(note))
        self.head.addWidget(self.note, 0, MIDDLE)
        self.body.addWidget(head)
        self.rows: list[tuple[str, str]] = []
        self.footer_label: QLabel | None = None

    def add_tag(self, text: str, tone: str = "neutral", mono: bool = True) -> Tag:
        tag = Tag(text, tone, mono)
        self.head.insertWidget(2 + len(self.tags), tag, 0, MIDDLE)
        self.tags.append(tag)
        return tag

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def add_row(self, name: str, value: str, *, mono_name: bool = False) -> QFrame:
        """A 40 px row: the name at the reading start, the value (mono) at the end."""
        frame = QFrame()
        frame.setProperty("lab", "row")
        frame.setFixedHeight(ROW_HEIGHT)
        line = QHBoxLayout(frame)
        line.setContentsMargins(16, 0, 16, 0)
        line.setSpacing(10)
        line.addWidget(mono_label(name) if mono_name else lab_label(name, "muted"))
        line.addStretch(1)
        line.addWidget(mono_label(value))
        self.body.addWidget(frame)
        self.rows.append((name, value))
        return frame

    def add_section(self, widget: QWidget, margins: Margins = (0, 0, 0, 0)) -> QFrame:
        """Any content under a hairline (a table, the checks, the buttons)."""
        frame = QFrame()
        frame.setProperty("lab", "row")
        box = QVBoxLayout(frame)
        box.setContentsMargins(*margins)
        box.setSpacing(0)
        box.addWidget(widget)
        self.body.addWidget(frame)
        return frame

    def add_footer(self, text: str) -> QLabel:
        label = lab_label(text, "footer", wrap=True)
        label.setContentsMargins(16, 12, 16, 12)
        frame = self.add_section(label)
        frame.setObjectName("CardFooter")
        self.footer_label = label
        return label

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        apply_tree(self, tokens)


class CheckList(QWidget):
    """Rows of a check mark (green) or a cross (red), the text and a value at the end."""

    def __init__(self) -> None:
        super().__init__()
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 14, 16, 14)
        self.box.setSpacing(8)
        self.items: list[tuple[bool, str, str]] = []

    def add(self, ok: bool, text: str, value: str = "") -> None:
        row = QHBoxLayout()
        row.setSpacing(10)
        tone = "profit" if ok else "loss"
        row.addWidget(LabIcon("check" if ok else "cross", 14, tone), 0, MIDDLE)
        label = lab_label(text, "text", wrap=True)
        row.addWidget(label, 1)
        if value:
            row.addWidget(mono_label(value, tone))
        self.box.addLayout(row)
        self.items.append((ok, text, value))

    def clear(self) -> None:
        while self.box.count():
            item = self.box.takeAt(0)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            while row.count():
                part = row.takeAt(0)
                widget = part.widget() if part is not None else None
                if widget is not None:
                    widget.deleteLater()
            row.deleteLater()
        self.items = []


class GridTable(QWidget):
    """The design's grid: a row of captions (42 px) over 42 px rows, fixed and shared widths
    (0 = a share of the rest). Every cell is mono, left to right inside its cell."""

    def __init__(self, header: Sequence[str], widths: Sequence[int]) -> None:
        super().__init__()
        self.header = tuple(header)
        self.widths = tuple(widths)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)
        self.rows: list[list[str]] = []
        self.box.addWidget(self._line(list(header), head=True))

    def _line(self, cells: Sequence[str], head: bool = False, tone: str = "") -> QFrame:
        frame = QFrame()
        frame.setProperty("lab", "grid_head" if head else "row")
        frame.setFixedHeight(GRID_ROW)
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 0, 16, 0)
        grid.setHorizontalSpacing(10)
        for column, (text, width) in enumerate(zip(cells, self.widths, strict=True)):
            label = lab_label(text, "cap") if head else mono_label(text, tone)
            if width:
                label.setFixedWidth(width)
            grid.addWidget(label, 0, column)
            grid.setColumnStretch(column, 0 if width else 1)
        return frame

    def set_rows(self, rows: Sequence[Sequence[str]], tone: str = "") -> None:
        while self.box.count() > 1:
            item = self.box.takeAt(1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.rows = [list(row) for row in rows]
        for row in self.rows:
            self.box.addWidget(self._line(row, tone=tone))

    def rowCount(self) -> int:  # noqa: N802 (the QTableWidget name the tests know)
        return len(self.rows)

    def item(self, row: int, column: int) -> Cell:
        """One cell, like `QTableWidget.item` (the tests read the text)."""
        return Cell(self.rows[row][column])


# The composer ------------------------------------------------------------------------------
class ComposerBox(QFrame):
    """The design's composer box: the border of its state and a glow while typing (wider and
    brighter the more is typed), breathing while the AI works."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("lab", "composer")
        self.setProperty("state", "idle")
        self.tokens: ThemeTokens = DEFAULT
        self.motion = True
        self.length = 0
        self.glow = QGraphicsDropShadowEffect(self)
        self.glow.setOffset(0, 0)
        self.glow.setBlurRadius(0)
        self.glow.setEnabled(False)
        self.setGraphicsEffect(self.glow)
        self.breathe = QVariantAnimation(self)
        self.breathe.setStartValue(0.0)
        self.breathe.setKeyValueAt(0.5, 1.0)
        self.breathe.setEndValue(0.0)
        self.breathe.setDuration(BREATHE_MS)
        self.breathe.setLoopCount(-1)
        self.breathe.valueChanged.connect(self._breath)

    @property
    def state(self) -> str:
        value = self.property("state")
        return value if isinstance(value, str) else "idle"

    def set_state(self, state: str, length: int = 0) -> None:
        """idle, focus, typing (with the length of the text) or busy."""
        self.length = length
        if state != self.state:
            self.setProperty("state", state)
            restyle(self)
        if state == "busy" and self.motion:
            if self.breathe.state() != QVariantAnimation.State.Running:
                self.breathe.start()
        else:
            self.breathe.stop()
        self._glow()

    def _glow(self) -> None:
        state = self.state
        ink = QColor(self.tokens.text)
        if state == "typing":
            share = min(self.length, 60) / 60
            self.glow.setBlurRadius(18 + share * 18)
            ink.setAlphaF((12 + share * 10) / 100)
        elif state in ("focus", "busy"):
            self.glow.setBlurRadius(14)
            ink.setAlphaF(0.08)
        else:
            self.glow.setEnabled(False)
            return
        self.glow.setColor(ink)
        self.glow.setEnabled(True)

    def _breath(self, value: object) -> None:
        share = float(value) if isinstance(value, int | float) else 0.0
        ink = QColor(self.tokens.text)
        ink.setAlphaF(0.08 + 0.12 * share)
        self.glow.setBlurRadius(14 + 8 * share)
        self.glow.setColor(ink)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self._glow()


class SendButton(QPushButton):
    """Send (34 px): the ink when there is text, quiet when empty; Stop (a square) while busy."""

    def __init__(self) -> None:
        super().__init__()
        self.tokens: ThemeTokens = DEFAULT
        self.has_text = False
        self.busy = False
        self.setFixedSize(SEND_SIZE, SEND_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def set_look(self, has_text: bool, busy: bool) -> None:
        self.has_text = has_text
        self.busy = busy
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.busy or self.has_text
        painter.fillRect(self.rect(), QColor(t.text if full else t.hover))
        if self.busy:
            side = 12
            left = (self.width() - side) / 2
            top = (self.height() - side) / 2
            painter.fillRect(QRectF(left, top, side, side), QColor(t.bg))
        else:
            ink = t.bg if self.has_text else t.text_secondary
            draw_icon(painter, "up", QRectF(9, 9, 16, 16), ink)
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.text_secondary), 2))
            painter.drawRect(QRectF(1, 1, self.width() - 2, self.height() - 2))
        painter.end()


class Skeleton(Painted):
    """The loading bars (12 px) with the design's sweep (1.6 s); still with reduced motion."""

    def __init__(self, width: int = 340) -> None:
        super().__init__()
        self.setFixedSize(width, 34)
        self.phase = 0.0
        self.sweep = QVariantAnimation(self)
        self.sweep.setStartValue(0.0)
        self.sweep.setEndValue(1.0)
        self.sweep.setDuration(SKELETON_MS)
        self.sweep.setLoopCount(-1)
        self.sweep.valueChanged.connect(self._move)

    def start(self) -> None:
        self.sweep.start()

    def stop(self) -> None:
        self.sweep.stop()

    def _move(self, value: object) -> None:
        self.phase = float(value) if isinstance(value, int | float) else 0.0
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        for top, share in ((0, 1.0), (22, 0.76)):
            bar = QRectF(0, top, self.width() * share, 12)
            painter.fillRect(bar, QColor(t.hover))
            start = bar.left() + (self.phase * 2 - 0.5) * bar.width()
            band = QRectF(start, top, bar.width() / 3, 12)
            painter.fillRect(band.intersected(bar), QColor(mix(t.hover, t.surface, 0.6)))
        painter.end()


class RailButton(QPushButton):
    """One of the inspector's rail buttons (40 px): the ink when it is the open panel."""

    def __init__(self, icon: str, name: str) -> None:
        super().__init__()
        self.icon_name = icon
        self.tokens: ThemeTokens = DEFAULT
        self.setCheckable(True)
        self.setFixedSize(RAIL_SIZE, RAIL_SIZE)
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect())
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.text))
            painter.drawRoundedRect(box, 6, 6)
        elif self.underMouse() and self.isEnabled():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.hover))
            painter.drawRoundedRect(box, 6, 6)
        if self.isChecked():
            ink = t.bg
        else:
            ink = t.text_secondary if self.isEnabled() else t.border_strong
        draw_lab_icon(painter, self.icon_name, QRectF(11, 11, 18, 18), ink)
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.text), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 5, 5)
        painter.end()


class FlowBox(QWidget):
    """Buttons in rows that wrap, from the reading start (the design's `flex-wrap`)."""

    def __init__(self, spacing: int = 8, centered: bool = False) -> None:
        super().__init__()
        self.gap = spacing
        self.centered = centered
        self.items: list[QWidget] = []
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget: QWidget) -> None:
        widget.setParent(self)
        widget.show()
        self.items.append(widget)
        self.updateGeometry()

    def _rows(self, width: int) -> list[list[QWidget]]:
        rows: list[list[QWidget]] = [[]]
        used = 0
        for item in self.items:
            if item.isHidden():
                continue
            hint = item.sizeHint().width()
            if rows[-1] and used + self.gap + hint > width:
                rows.append([])
                used = 0
            used += (self.gap if rows[-1] else 0) + hint
            rows[-1].append(item)
        return [row for row in rows if row]

    def _place(self, width: int, move: bool) -> int:
        rtl = self.layoutDirection() == RTL
        top = 0
        for row in self._rows(width):
            total = sum(item.sizeHint().width() for item in row) + self.gap * (len(row) - 1)
            height = max(item.sizeHint().height() for item in row)
            x = (width - total) // 2 if self.centered else 0
            for item in row:
                hint = item.sizeHint()
                left = width - x - hint.width() if rtl else x
                if move:
                    item.setGeometry(QRect(QPoint(left, top), hint))
                x += hint.width() + self.gap
            top += height + self.gap
        return max(0, top - self.gap)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt name)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt name)
        return self._place(width, move=False)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        width = max(self.width(), CHAT_WIDTH // 2)
        return QSize(width, self._place(width, move=False))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        widest = max((item.sizeHint().width() for item in self.items), default=0)
        return QSize(widest, self._place(max(widest, 1), move=False))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._place(self.width(), move=True)
