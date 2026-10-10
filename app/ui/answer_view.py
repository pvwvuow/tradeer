"""The AI's answer drawn as a report (docs/AI_LAB_V4.md section 2, 0.45.0).

`AnswerView` takes the agent's Markdown, reads its blocks (`app.ai.answer_blocks`) and draws
each one with the theme: text and lists, headings, tables with numbers at the end and +/-
in green and red, code with its own Copy, callouts for a tip, a warning or a stop, tiles for
the key numbers, small line, bar and donut charts of the agent's own data, numbered next
steps and meters for a limit. An `app-chart` block keeps a place where the chat puts the
chart card of the app's own data (`slots`). `text()` is the Markdown as it came, for the
history and the Copy button of the answer. Persian runs right to left; numbers stay left to
right.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Callable, Sequence

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ai.answer_blocks import (
    AppChart,
    Block,
    Callout,
    Chart,
    Code,
    Heading,
    Metrics,
    Progress,
    Rule,
    Steps,
    Table,
    Text,
    number,
    parse,
    tone_of,
)
from app.ui.lab_charts import BarChart, Line, LineChart, Meter
from app.ui.lab_parts import LTR, RTL, StepDot, apply_tree, lab_button, lab_label
from app.ui.shell import Painted
from app.ui.theme import DEFAULT, NUMBER_FONT, ThemeTokens, mix, number_family, px

COPIED_MS = 1500
TILE_COLUMNS = 4
DONUT_SIZE = 132
LINE_TONES = ("ink", "muted", "profit", "loss", "warning")
CALLOUT_MARK = {
    "info": "\u24d8",
    "success": "\u2713",
    "warning": "\u26a0",
    "danger": "\u2715",
    "quote": "\u201d",
}
VIEW_FA: dict[str, str] = {
    "Copy": "کپی",
    "Copied": "کپی شد",
    "Copy table": "کپی جدول",
    "Copy code": "کپی کد",
    "rows": "ردیف",
    "Next steps": "قدم‌های بعدی",
}


def persian(text: str) -> bool:
    """The first letter is Persian or Arabic: the text runs right to left (the rule of the
    first strong character, as in Unicode bidi)."""
    for char in text:
        if char.isalpha():
            return "\u0600" <= char <= "\u06ff"
    return False


def view_words(fa: bool) -> Callable[[str], str]:
    def word(english: str) -> str:
        return VIEW_FA.get(english, english) if fa else english

    return word


def answer_qss(tokens: ThemeTokens) -> str:
    """The rules of the answer's blocks, after the chat's."""
    t = tokens
    mono = number_family()
    return f"""
QLabel[lab="answer"] {{
    color: {t.text};
    font-size: {px(15):g}pt;
}}
QLabel[lab="h1"], QLabel[lab="h2"] {{
    color: {t.text};
    font-size: {px(18):g}pt;
    font-weight: 700;
}}
QLabel[lab="h3"] {{
    color: {t.text};
    font-size: {px(16):g}pt;
    font-weight: 600;
}}
QLabel[lab="h4"] {{
    color: {t.text_secondary};
    font-size: {px(14):g}pt;
    font-weight: 600;
}}
QFrame[lab="block"] {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame[lab="block"] QWidget {{
    background: transparent;
}}
QFrame[lab="code"] {{
    background-color: {mix(t.surface, t.text, 0.04)};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame[lab="code"] QWidget {{
    background: transparent;
}}
QLabel[lab="code"] {{
    color: {t.text};
    font-family: {mono};
    font-size: {px(13):g}pt;
}}
QFrame[lab="trow"] {{
    background: transparent;
    border: none;
    border-top: 1px solid {t.border};
}}
QFrame[lab="trow"][odd="true"] {{
    background-color: {mix(t.surface, t.text, 0.025)};
}}
QFrame[lab="thead"] {{
    background: transparent;
    border: none;
}}
QLabel[lab="cell"] {{
    color: {t.text};
    font-size: {px(13):g}pt;
}}
QLabel[lab="cell"][mono="true"] {{
    font-family: {mono};
}}
QLabel[lab="cell"][tone="profit"], QLabel[lab="tile_value"][tone="profit"] {{
    color: {t.profit};
}}
QLabel[lab="cell"][tone="loss"], QLabel[lab="tile_value"][tone="loss"] {{
    color: {t.loss};
}}
QLabel[lab="tile_value"][tone="warning"] {{
    color: {t.warning};
}}
QFrame[lab="tile"] {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame[lab="tile"] QWidget {{
    background: transparent;
}}
QLabel[lab="tile_value"] {{
    color: {t.text};
    font-family: {mono};
    font-size: {px(22):g}pt;
    font-weight: 600;
}}
QFrame[lab="callout"] {{
    background-color: {mix(t.surface, t.text, 0.03)};
    border: 1px solid {t.border};
    border-left: 3px solid {t.text_secondary};
    border-radius: 10px;
}}
QFrame[lab="callout"][tone="warning"] {{
    background-color: {mix(t.surface, t.warning, 0.08)};
    border-left-color: {t.warning};
}}
QFrame[lab="callout"][tone="danger"] {{
    background-color: {mix(t.surface, t.loss, 0.08)};
    border-left-color: {t.loss};
}}
QFrame[lab="callout"][tone="success"] {{
    background-color: {mix(t.surface, t.profit, 0.08)};
    border-left-color: {t.profit};
}}
QFrame[lab="callout"] QWidget {{
    background: transparent;
}}
QLabel[lab="mark"] {{
    color: {t.text_secondary};
    font-size: {px(15):g}pt;
    font-weight: 700;
}}
QLabel[lab="mark"][tone="warning"] {{
    color: {t.warning};
}}
QLabel[lab="mark"][tone="danger"] {{
    color: {t.loss};
}}
QLabel[lab="mark"][tone="success"] {{
    color: {t.profit};
}}
QPushButton[lab="copy"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 3px 8px;
    min-height: 0px;
    font-size: {px(12):g}pt;
    font-weight: 400;
}}
QPushButton[lab="copy"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QFrame[lab="hr"] {{
    background-color: {t.border};
    border: none;
}}
"""


def copy_text(text: str) -> bool:
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(text)
    return True


class CopyButton(QPushButton):
    """A quiet "Copy" that says "Copied" for a moment after a click."""

    def __init__(self, payload: Callable[[], str], fa: bool = False, name: str = "Copy") -> None:
        word = view_words(fa)
        super().__init__(word(name))
        self.word = word
        self.name = name
        self.payload = payload
        self.setProperty("lab", "copy")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(word(name))
        self.setToolTip(word(name))
        self.clicked.connect(self.copy)
        self.last = ""

    def copy(self) -> str:
        self.last = self.payload()
        if copy_text(self.last):
            self.setText("\u2713 " + self.word("Copied"))
            QTimer.singleShot(COPIED_MS, self._back)
        return self.last

    def _back(self) -> None:
        self.setText(self.word(self.name))


def set_direction(label: QLabel, text: str) -> None:
    """Persian text right to left and right aligned; anything else left to right."""
    if persian(text):
        label.setLayoutDirection(RTL)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    else:
        label.setLayoutDirection(LTR)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)


def markdown_label(text: str, role: str = "answer") -> QLabel:
    label = lab_label(text, role, wrap=True)
    label.setTextFormat(Qt.TextFormat.MarkdownText)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.LinksAccessibleByMouse,
    )
    label.setOpenExternalLinks(False)
    set_direction(label, text)
    return label


def plain_label(text: str, role: str, wrap: bool = True) -> QLabel:
    label = lab_label(text, role, wrap=wrap)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def block_frame(role: str = "block") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("lab", role)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 12, 16, 14)
    box.setSpacing(10)
    return frame, box


def head_row(title: str, button: QWidget | None = None, caption: str = "") -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    if title:
        name = plain_label(title, "card_title", wrap=True)
        set_direction(name, title)
        row.addWidget(name, 1)
    if caption:
        row.addWidget(lab_label(caption, "cap"))
    if not title:
        row.addStretch(1)
    if button is not None:
        row.addWidget(button)
    return row


# The blocks -------------------------------------------------------------------------------
def table_widget(table: Table, fa: bool) -> QFrame:
    """A table drawn by the chat: captions, hairlines, every other row a shade darker,
    numbers in the mono face at the end of their cell, +/- in green and red."""
    word = view_words(fa)
    frame, box = block_frame()
    frame.setObjectName("AnswerTable")
    box.setContentsMargins(0, 8, 0, 4)
    box.setSpacing(0)
    copy = CopyButton(table.tsv, fa, "Copy table")
    caption = f"{len(table.rows)} {word('rows')}"
    head = QWidget()
    top = head_row("", copy, caption)
    top.setContentsMargins(16, 0, 10, 6)
    head.setLayout(top)
    box.addWidget(head)
    columns = len(table.header)
    numeric = [
        bool(table.rows)
        and all(number(row[index]) is not None or not row[index].strip() for row in table.rows)
        for index in range(columns)
    ]
    grid_rows = [(table.header, True), *((row, False) for row in table.rows)]
    for position, (cells, head_line) in enumerate(grid_rows):
        line = QFrame()
        line.setProperty("lab", "thead" if head_line else "trow")
        line.setProperty("odd", "true" if position % 2 == 0 and not head_line else "false")
        grid = QGridLayout(line)
        grid.setContentsMargins(16, 8, 16, 8)
        grid.setHorizontalSpacing(14)
        for column in range(columns):
            text = cells[column] if column < len(cells) else ""
            if head_line:
                label = plain_label(text.upper() if not persian(text) else text, "cap")
            elif numeric[column]:
                label = plain_label(text, "cell", wrap=False)
                label.setProperty("mono", "true")
                label.setProperty("tone", tone_of(text))
                label.setLayoutDirection(LTR)
            else:
                label = markdown_label(text, "cell")
            align = table.align[column] if column < len(table.align) else "left"
            end = numeric[column] or align == "right"
            if end:
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif align == "center":
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label, 0, column)
            grid.setColumnStretch(column, 1 if not numeric[column] else 0)
            if numeric[column]:
                grid.setColumnMinimumWidth(column, 64)
        box.addWidget(line)
    return frame


def code_widget(code: Code, fa: bool) -> QFrame:
    frame, box = block_frame("code")
    frame.setObjectName("AnswerCode")
    frame.setLayoutDirection(LTR)
    box.setContentsMargins(14, 8, 10, 12)
    copy = CopyButton(lambda: code.text, fa, "Copy code")
    box.addLayout(head_row("", copy, code.language or "text"))
    text = plain_label(code.text, "code", wrap=True)
    text.setLayoutDirection(LTR)
    box.addWidget(text)
    return frame


def callout_widget(callout: Callout) -> QFrame:
    frame = QFrame()
    frame.setProperty("lab", "callout")
    frame.setProperty("tone", callout.tone)
    frame.setObjectName("AnswerCallout")
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 12, 16, 12)
    row.setSpacing(12)
    mark = lab_label(CALLOUT_MARK.get(callout.tone, "\u24d8"), "mark")
    mark.setProperty("tone", callout.tone)
    row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
    body = QVBoxLayout()
    body.setSpacing(4)
    if callout.title:
        title = markdown_label(f"**{callout.title}**", "answer")
        body.addWidget(title)
    if callout.text:
        body.addWidget(markdown_label(callout.text, "answer"))
    row.addLayout(body, 1)
    return frame


def metrics_widget(metrics: Metrics) -> QWidget:
    """The key numbers as tiles, up to four in a row."""
    holder = QWidget()
    holder.setObjectName("AnswerMetrics")
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(10)
    columns = min(len(metrics.items), TILE_COLUMNS) or 1
    for index, item in enumerate(metrics.items):
        tile = QFrame()
        tile.setProperty("lab", "tile")
        box = QVBoxLayout(tile)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(4)
        name = plain_label(item.label, "label", wrap=True)
        set_direction(name, item.label)
        value = plain_label(item.value, "tile_value", wrap=False)
        value.setProperty("tone", item.tone)
        value.setLayoutDirection(LTR)
        box.addWidget(name)
        box.addWidget(value)
        if item.note:
            note = plain_label(item.note, "note", wrap=True)
            set_direction(note, item.note)
            box.addWidget(note)
        grid.addWidget(tile, index // columns, index % columns)
    for column in range(columns):
        grid.setColumnStretch(column, 1)
    return holder


def palette(tokens: ThemeTokens) -> list[str]:
    """The colours of a donut's shares, in order."""
    t = tokens
    return [
        t.text,
        t.text_secondary,
        t.profit,
        t.loss,
        t.warning,
        t.border_strong,
        mix(t.text, t.surface, 0.5),
    ]


class DonutChart(Painted):
    """Shares of a whole as a ring, the biggest share in the middle."""

    def __init__(self, values: Sequence[float], size: int = DONUT_SIZE) -> None:
        super().__init__()
        self.values = [max(float(value), 0.0) for value in values]
        self.setFixedSize(size, size)
        self.setLayoutDirection(LTR)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(self.values)
        ring = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        if total <= 0:
            painter.setPen(QPen(QColor(t.border), 14))
            painter.drawEllipse(ring)
            painter.end()
            return
        colors = palette(t)
        start = 90 * 16
        for index, value in enumerate(self.values):
            span = -round(value / total * 360 * 16)
            pen = QPen(QColor(colors[index % len(colors)]), 16)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(ring, start, span)
            start += span
        biggest = max(self.values) / total
        font = QFont(self.font())
        font.setFamily(NUMBER_FONT)
        font.setPointSizeF(px(16))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(t.text))
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, f"{biggest:.0%}")
        painter.end()


class Swatch(Painted):
    """The colour of one share in the donut's legend."""

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        self.setFixedSize(10, 10)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = palette(self.tokens)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors[self.index % len(colors)]))
        painter.drawRoundedRect(QRectF(self.rect()), 3, 3)
        painter.end()


def _value(value: float, unit: str) -> str:
    if not math.isfinite(value):
        return "n/a"
    shown = f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{shown}{unit}" if unit in ("%", "R", " R") else f"{shown} {unit}".strip()


def chart_widget(chart: Chart) -> QFrame:
    """A small chart of the agent's own numbers: line or area, bar, or donut."""
    frame, box = block_frame()
    frame.setObjectName("AnswerChart")
    box.addLayout(head_row(chart.title, None, chart.style.upper()))
    first = chart.series[0]
    if chart.style == "donut":
        row = QHBoxLayout()
        row.setSpacing(18)
        donut = DonutChart(first.values)
        row.addWidget(donut, 0, Qt.AlignmentFlag.AlignVCenter)
        legend = QVBoxLayout()
        legend.setSpacing(6)
        total = sum(max(value, 0.0) for value in first.values) or 1.0
        for index, (label, value) in enumerate(zip(chart.labels, first.values, strict=False)):
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(Swatch(index), 0, Qt.AlignmentFlag.AlignVCenter)
            name = plain_label(label, "text", wrap=True)
            set_direction(name, label)
            line.addWidget(name, 1)
            share = plain_label(f"{_value(value, chart.unit)}  {value / total:.0%}", "mono")
            share.setLayoutDirection(LTR)
            line.addWidget(share)
            legend.addLayout(line)
        legend.addStretch(1)
        row.addLayout(legend, 1)
        box.addLayout(row)
        return frame
    if chart.style == "bar":
        bars = BarChart(170, chart.title or "Chart")
        signed = any(value < 0 for value in first.values)
        count = len(chart.labels)
        step = max(1, math.ceil(count / 10))
        labels = [label if index % step == 0 else "" for index, label in enumerate(chart.labels)]
        tips = [
            f"{label}: {_value(value, chart.unit)}"
            for label, value in zip(chart.labels, first.values, strict=False)
        ]
        bars.set_bars(first.values, labels, signed=signed, tips=tips)
        box.addWidget(bars)
    else:
        lines = LineChart(180, chart.title or "Chart")
        found = [
            Line(series.values, series.name, LINE_TONES[index % len(LINE_TONES)], index > 0)
            for index, series in enumerate(chart.series)
        ]
        unit = chart.unit if chart.unit in ("%", "R") else ""
        lines.set_lines(found, zero=any(v < 0 for s in chart.series for v in s.values), unit=unit)
        box.addWidget(lines)
        ends = f"{chart.labels[0]} \u2192 {chart.labels[-1]}" if len(chart.labels) > 1 else ""
        names = " \u00b7 ".join(series.name for series in chart.series if series.name)
        footer = " \u00b7 ".join(part for part in (ends, names) if part)
        if footer:
            note = plain_label(footer, "note", wrap=True)
            note.setLayoutDirection(LTR)
            box.addWidget(note)
    return frame


def steps_widget(steps: Steps, fa: bool) -> QFrame:
    frame, box = block_frame()
    frame.setObjectName("AnswerSteps")
    box.addLayout(head_row(view_words(fa)("Next steps")))
    for index, item in enumerate(steps.items):
        row = QHBoxLayout()
        row.setSpacing(12)
        dot = StepDot(index + 1)
        dot.set_state("current")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        text = markdown_label(item, "answer")
        row.addWidget(text, 1)
        box.addLayout(row)
    return frame


def progress_widget(progress: Progress) -> QFrame:
    frame, box = block_frame()
    frame.setObjectName("AnswerProgress")
    for item in progress.items:
        top = QHBoxLayout()
        name = plain_label(item.label, "text", wrap=True)
        set_direction(name, item.label)
        top.addWidget(name, 1)
        value = plain_label(item.text, "mono", wrap=False)
        value.setLayoutDirection(LTR)
        value.setProperty("tone", "loss" if item.share >= 0.8 else "")
        top.addWidget(value)
        box.addLayout(top)
        meter = Meter()
        meter.set_share(item.share)
        box.addWidget(meter)
        if item.note:
            note = plain_label(item.note, "note", wrap=True)
            set_direction(note, item.note)
            box.addWidget(note)
    return frame


class ChartSlot(QWidget):
    """The place an `app-chart` block keeps for a chart card of the app's own data. A card
    that was hidden (a "Working..." card replaced by its chart) frees the place again."""

    def __init__(self, kind: str, symbol: str = "") -> None:
        super().__init__()
        self.kind = kind
        self.symbol = symbol
        self.setObjectName("AnswerChartSlot")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)
        self.card: QWidget | None = None
        self.setVisible(False)

    @property
    def free(self) -> bool:
        card = self.card
        if card is None:
            return True
        try:
            return card.isHidden()
        except RuntimeError:  # the card is gone
            return True

    def fill(self, card: QWidget) -> None:
        old = self.card
        if old is not None:
            with contextlib.suppress(RuntimeError):
                self.box.removeWidget(old)
        self.card = card
        self.box.addWidget(card)
        card.show()
        self.setVisible(True)


def rule_widget() -> QFrame:
    line = QFrame()
    line.setProperty("lab", "hr")
    line.setFixedHeight(1)
    return line


class AnswerView(QWidget):
    """The answer's blocks, one under the other."""

    def __init__(self, fa: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fa = fa
        self.tokens: ThemeTokens = DEFAULT
        self.markdown = ""
        self.blocks: list[Block] = []
        self.parts: list[QWidget] = []
        self.slots: list[ChartSlot] = []
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def text(self) -> str:
        return self.markdown

    def setText(self, text: str) -> None:  # noqa: N802 (the QLabel name the tests know)
        self.markdown = text
        for part in self.parts:
            self.box.removeWidget(part)
            part.deleteLater()
        self.parts = []
        self.slots = []
        self.blocks = parse(text)
        for block in self.blocks:
            widget = self._widget(block)
            self.box.addWidget(widget)
            self.parts.append(widget)
        apply_tree(self, self.tokens)

    def _widget(self, block: Block) -> QWidget:
        fa = self.fa
        if isinstance(block, Text):
            return markdown_label(block.markdown)
        if isinstance(block, Heading):
            label = plain_label(block.text, f"h{min(max(block.level, 1), 4)}")
            set_direction(label, block.text)
            return label
        if isinstance(block, Rule):
            return rule_widget()
        if isinstance(block, Table):
            return table_widget(block, fa)
        if isinstance(block, Callout):
            return callout_widget(block)
        if isinstance(block, Metrics):
            return metrics_widget(block)
        if isinstance(block, Chart):
            return chart_widget(block)
        if isinstance(block, Steps):
            return steps_widget(block, fa)
        if isinstance(block, Progress):
            return progress_widget(block)
        if isinstance(block, AppChart):
            slot = ChartSlot(block.kind, block.symbol)
            self.slots.append(slot)
            return slot
        return code_widget(block, fa)

    def free_slots(self) -> list[ChartSlot]:
        return [slot for slot in self.slots if slot.free]

    def kinds(self) -> list[str]:
        return [type(block).__name__ for block in self.blocks]

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        apply_tree(self, tokens)


def action_button(text: str, name: str) -> QPushButton:
    button = lab_button(text, "copy")
    button.setAccessibleName(name)
    button.setToolTip(name)
    return button
