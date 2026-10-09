"""The AI Lab chat, drawn like the owner's No Curve v2 design (Phases 18b, 18c and 20e).

Ask in your own words; the agent looks things up with read-only tools and answers. The
transcript is one centred column (760 px at most): your question in a bubble, under it a
folded line ("Worked 11 s · 5 steps", click it for each step), the loading bars while the
AI works, then the Markdown answer and one cost line. There is no fake streaming: the
answer shows when it is really there. Stop ends the turn between two steps.

The composer glows while you type and breathes while the AI works; Enter sends and
Shift+Enter starts a new line. The page can take a message first (`intercept`): a pasted
AI answer, "Export for AI" or "Compare the suggestion" run the manual loop as cards in the
transcript (`add_card`). Persian runs right to left. Nothing here changes a setting or
trades.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QFont, QFontMetrics, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ai.agent import Limits, Step, Tool, Turn, run_turn
from app.ai.chat_store import Chat, ChatStore, start_chat
from app.ai.transport import AiCallError, AiClient, Completion, Message, plain_text
from app.ui.lab_parts import (
    CHAT_WIDTH,
    LTR,
    RTL,
    TYPING_MS,
    ComposerBox,
    FlowBox,
    LabIcon,
    SendButton,
    Skeleton,
    apply_tree,
    lab_button,
    lab_label,
    lab_words,
)
from app.ui.theme import DEFAULT, ThemeTokens, px
from app.ui.v2 import Tag

Job = Callable[[], None]
ClientFactory = Callable[[], AiClient | str]
Intercept = Callable[[str], bool]
TEXT_FONT = "Vazirmatn"  # the design's face for words (bundled by scripts/ci/build.ps1)
BUBBLE_WIDTH = 520
BUBBLE_PADDING = 16 * 2 + 2 + 12  # the bubble's padding, border and a little air
ARROW_SIZE = 30
SUGGESTIONS = (
    "Build the report for the AI",
    "Compare the suggestion",
    "EUR/USD candle chart",
    "Equity curve now and with the AI",
)
QUICK_PROMPTS = (
    "EUR/USD candle chart",
    "Equity curve now and with the AI",
    "Parameter sensitivity map",
    "Run the Monte Carlo",
    "Distribution of R results",
    "Trade table",
    "Check the app's health",
    "Which filter rejects the most?",
    "Recent trades",
)
EXAMPLES = QUICK_PROMPTS
CHAT_FA: dict[str, str] = {
    "Build the report for the AI": "گزارش را برای AI بساز",
    "Compare the suggestion": "پیشنهاد را مقایسه کن",
    "EUR/USD candle chart": "نمودار کندلی EUR/USD",
    "Equity curve now and with the AI": "منحنی سرمایه فعلی و AI",
    "Parameter sensitivity map": "نقشه‌ی حساسیت پارامتر",
    "Run the Monte Carlo": "مونت‌کارلو را اجرا کن",
    "Distribution of R results": "توزیع نتایج R",
    "Trade table": "جدول معاملات",
    "Check the app's health": "بررسی سلامت برنامه",
    "Which filter rejects the most?": "کدام فیلتر بیشتر رد می‌کند؟",
    "Recent trades": "معاملات اخیر",
    "Working...": "در حال کار...",
    "Stopping after this step...": "بعد از همین مرحله متوقف می‌شود...",
    "New chat: the AI forgets the earlier questions.": (
        "گفتگوی جدید: AI سؤال‌های قبلی را فراموش می‌کند."
    ),
}
FA_DIGITS = str.maketrans("0123456789", "".join(chr(0x06F0 + digit) for digit in range(10)))


def chat_qss(tokens: ThemeTokens) -> str:
    """The chat's and the inspector's rules, after `lab_qss`: the word face, the rounded
    question bubble, the arrows of the quick prompts, the search field and the list rows."""
    t = tokens
    return f"""
QLabel, QPushButton, QPlainTextEdit, QLineEdit {{
    font-family: "{TEXT_FONT}";
}}
QLabel[lab="bubble"] {{
    background-color: {t.hover};
    border: 1px solid {t.border};
    border-radius: 14px;
    padding: 10px 16px;
    font-size: {px(15):g}pt;
}}
QPushButton[lab="arrow"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 0px;
    min-height: 0px;
    font-size: {px(16):g}pt;
}}
QPushButton[lab="arrow"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[lab="arrow"]:disabled {{
    color: {t.border_strong};
}}
QLineEdit[lab="search"] {{
    min-height: 38px;
    max-height: 40px;
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    background: transparent;
    font-size: {px(13):g}pt;
    padding: 0px 12px;
}}
QFrame[lab="item"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QFrame[lab="item"]:hover {{
    background-color: {t.hover};
}}
QFrame[lab="item"][current="true"] {{
    background-color: {t.hover};
    border-color: {t.border_strong};
}}
QFrame[lab="item"] QLabel {{
    background: transparent;
}}
"""


def _start_thread(job: Job) -> None:
    threading.Thread(target=job, name="ai-lab-agent", daemon=True).start()


def _no_tools() -> Sequence[Tool]:
    return ()


def _app_language() -> str:
    return ""


def _no_intercept(text: str) -> bool:
    return False


def persian(text: str) -> bool:
    """The first letter is Persian or Arabic: the message runs right to left (the rule of
    the first strong character, as in Unicode bidi)."""
    for char in text:
        if char.isalpha():
            return "\u0600" <= char <= "\u06ff"
    return False


def fa_digits(text: str) -> str:
    return text.translate(FA_DIGITS)


def chat_words(fa: bool) -> Callable[[str], str]:
    """The design's word for an English text: the chat's own words, then the Lab's."""
    lab = lab_words(fa)

    def word(english: str) -> str:
        return CHAT_FA.get(english, lab(english)) if fa else english

    return word


def worked_line(seconds: float, steps: int, fa: bool) -> str:
    """The folded line over an answer: how long the AI worked and in how many steps."""
    if fa:
        return fa_digits(f"{seconds:.0f} ثانیه کار کردم \u00b7 {steps} مرحله")
    return f"Worked {seconds:.0f} s \u00b7 {steps} step(s)"


def _text(text: str, role: str, *, markdown: bool = False, source: str = "") -> QLabel:
    label = lab_label(text, role, wrap=True)
    label.setTextFormat(Qt.TextFormat.MarkdownText if markdown else Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    direction(label, source or text)
    return label


def direction(label: QLabel, text: str) -> None:
    """Persian text right to left and right aligned; anything else left to right."""
    if persian(text):
        label.setLayoutDirection(RTL)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    else:
        label.setLayoutDirection(LTR)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)


def bubble(text: str) -> QWidget:
    """The design's question bubble (520 px at most) at the reading start of the column."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    label = _text(text, "bubble")
    label.setObjectName("AiChatBubble")
    font = QFont(label.font())
    font.setFamily(TEXT_FONT)
    font.setPointSizeF(px(15))
    metrics = QFontMetrics(font)
    widest = max((metrics.horizontalAdvance(line) for line in text.split("\n")), default=0)
    label.setMaximumWidth(min(widest + BUBBLE_PADDING, BUBBLE_WIDTH))
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
    line.addWidget(label)
    line.addStretch(1)
    return row


class TurnView(QFrame):
    """One question: the bubble, the folded steps, the loading bars, the answer, the cost."""

    def __init__(self, question: str, parent: QWidget | None = None, fa: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatTurn")
        self.fa = fa
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.bubble = bubble(question)
        found = self.bubble.findChild(QLabel)
        self.question: QLabel = found if found is not None else QLabel(question)
        layout.addWidget(self.bubble)
        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.meta_icon = LabIcon("bars", 20)
        self.meta = lab_button(chat_words(fa)("Working..."), "link")
        self.meta.setObjectName("AiChatMeta")
        self.meta.setCheckable(True)
        self.meta.setToolTip("Show or hide the steps")
        self.meta.toggled.connect(self._fold)
        meta.addWidget(self.meta_icon)
        meta.addWidget(self.meta)
        meta.addStretch(1)
        layout.addLayout(meta)
        self.steps = _text("", "muted")
        self.steps.setObjectName("AiChatSteps")
        self.steps.setLayoutDirection(LTR)
        self.steps.hide()  # one line says the AI works: the folded line over the answer
        layout.addWidget(self.steps)
        self.skeleton = Skeleton(340)
        layout.addWidget(self.skeleton)
        self.skeleton.start()
        self.answer = _text("", "answer", markdown=True)
        self.answer.setObjectName("AiChatAnswer")
        self.answer.hide()
        layout.addWidget(self.answer)
        self.cost = _text("", "note")
        self.cost.setObjectName("AiChatCost")
        self.cost.setLayoutDirection(LTR)
        self.cost.hide()
        layout.addWidget(self.cost)
        self.lines: list[str] = []
        self.started = time.monotonic()
        self.done = False

    def _fold(self, shown: bool) -> None:
        self.steps.setVisible(shown and bool(self.steps.text()))

    def add_step(self, step: Step) -> None:
        self.lines.append(step.line())
        self.steps.setText("\n".join(self.lines))
        if not self.done:
            self.meta.setText(f"{chat_words(self.fa)('Working...')} \u00b7 {step.title}")
        self.steps.setVisible(self.meta.isChecked())

    def _stop_loading(self) -> None:
        self.done = True
        self.skeleton.stop()
        self.skeleton.hide()

    def finish(self, turn: Turn) -> None:
        self._stop_loading()
        count = turn.tool_steps
        head = f"Worked {count} step(s)" if count else "Answered directly"
        self.steps.setText("\n".join([head, *self.lines]) if self.lines else head)
        self.steps.setVisible(self.meta.isChecked())
        seconds = time.monotonic() - self.started
        self.meta.setText(worked_line(seconds, count, self.fa))
        text = turn.answer or f"_{turn.stopped.capitalize()}._"
        self.answer.setText(text)
        direction(self.answer, text)
        self.answer.show()
        self.cost.setText(turn.cost_line())
        self.cost.show()

    def fail(self, text: str) -> None:
        self._stop_loading()
        self.meta.setText("\u2717 " + text)
        self.steps.setText("\n".join([*self.lines, f"\u2717 {text}"]))
        self.steps.show()


class ChatInput(QPlainTextEdit):
    """The composer's text: Enter sends, Shift+Enter starts a new line."""

    submitted = Signal()
    focused = Signal(bool)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt name)
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if enter and not shift:
            event.accept()
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (Qt name)
        super().focusInEvent(event)
        self.focused.emit(True)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (Qt name)
        super().focusOutEvent(event)
        self.focused.emit(False)


class PromptStrip(QWidget):
    """The quick prompts in one row that slides: the arrows move it by about a page."""

    def __init__(self, spacing: int = 8) -> None:
        super().__init__()
        self.setObjectName("AiChatQuick")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.back = lab_button("\u2039", "arrow")
        self.forward = lab_button("\u203a", "arrow")
        for button, name in ((self.back, "Earlier prompts"), (self.forward, "More prompts")):
            button.setObjectName("AiChatQuickArrow")
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setFixedSize(ARROW_SIZE, ARROW_SIZE)
        self.area = QScrollArea()
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        holder = QWidget()
        self.line = QHBoxLayout(holder)
        self.line.setContentsMargins(0, 0, 0, 0)
        self.line.setSpacing(spacing)
        self.line.addStretch(1)
        self.area.setWidget(holder)
        row.addWidget(self.back)
        row.addWidget(self.area, 1)
        row.addWidget(self.forward)
        self.items: list[QPushButton] = []
        self.back.clicked.connect(lambda: self.slide(-1))
        self.forward.clicked.connect(lambda: self.slide(1))
        bar = self.area.horizontalScrollBar()
        if bar is not None:
            bar.rangeChanged.connect(lambda _low, _high: self._arrows())
            bar.valueChanged.connect(lambda _value: self._arrows())
        self._arrows()

    def add(self, button: QPushButton) -> None:
        self.line.insertWidget(len(self.items), button)
        self.items.append(button)
        height = max(item.sizeHint().height() for item in self.items)
        self.area.setFixedHeight(max(height, ARROW_SIZE) + 2)
        self._arrows()

    def slide(self, direction: int) -> None:
        bar = self.area.horizontalScrollBar()
        viewport = self.area.viewport()
        if bar is None or viewport is None:
            return
        bar.setValue(bar.value() + direction * max(viewport.width() - 60, 80))

    def _arrows(self) -> None:
        bar = self.area.horizontalScrollBar()
        more = bar is not None and bar.maximum() > bar.minimum()
        self.back.setVisible(more)
        self.forward.setVisible(more)
        if bar is not None:
            self.back.setEnabled(bar.value() > bar.minimum())
            self.forward.setEnabled(bar.value() < bar.maximum())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._arrows()


class _Bridge(QObject):
    step = Signal(object)
    done = Signal(object)
    failed = Signal(str)


def centred(widget: QWidget) -> QWidget:
    """`widget` in the middle, 760 px at most, shrinking with the window."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    widget.setMaximumWidth(CHAT_WIDTH)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    row.addStretch(0)
    row.addWidget(widget, 1)
    row.addStretch(0)
    return holder


class ChatPanel(QWidget):
    turn_done = Signal(object)  # the finished Turn (the page records its usage and charts)
    chat_changed = Signal(str)  # the saved chat's id ("" for a new, unsaved chat)

    def __init__(
        self,
        client: ClientFactory,
        tools: Callable[[], Sequence[Tool]] = _no_tools,
        language: Callable[[], str] = _app_language,
        parent: QWidget | None = None,
        *,
        fa: bool = False,
        store: ChatStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatPanel")
        self.client = client
        self.tools = tools
        self.language = language
        self.fa = fa
        self.store = store
        self.chat: Chat | None = None
        self.clock: Callable[[], float] = time.time
        self.word = chat_words(fa)
        self.tokens: ThemeTokens = DEFAULT
        self.limits = Limits()
        self.start_job: Callable[[Job], None] = _start_thread
        self.intercept: Intercept = _no_intercept
        self.turns: list[Turn] = []
        self.views: list[TurnView] = []
        self.cards: list[QWidget] = []
        self.extras: list[QWidget] = []
        self._cancel = threading.Event()
        self._busy = False
        self.bridge = _Bridge()
        self.bridge.step.connect(self.show_step, Qt.ConnectionType.QueuedConnection)
        self.bridge.done.connect(self.show_turn, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        word = self.word
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.transcript = QScrollArea()
        self.transcript.setObjectName("AiChatTranscript")
        self.transcript.setWidgetResizable(True)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.setMinimumHeight(240)
        body = QWidget()
        body.setObjectName("AiChatBody")
        holder = QVBoxLayout(body)
        holder.setContentsMargins(0, 6, 0, 24)
        column = QWidget()
        self.messages = QVBoxLayout(column)
        self.messages.setContentsMargins(0, 0, 0, 0)
        self.messages.setSpacing(28)
        self.messages.addStretch(1)  # the column sits at the bottom, as in the design
        self.welcome = self._build_welcome()
        self.messages.addWidget(self.welcome)
        holder.addWidget(centred(column), 1)
        self.transcript.setWidget(body)
        outer.addWidget(self.transcript, 1)
        bottom = QWidget()
        lower = QVBoxLayout(bottom)
        lower.setContentsMargins(0, 0, 0, 0)
        lower.setSpacing(10)
        top = QHBoxLayout()
        self.status = lab_label("", "note", wrap=True)
        self.status.setObjectName("AiChatStatus")
        self.new_button = lab_button(word("New chat"), "link")
        self.new_button.setObjectName("AiChatNew")
        self.new_button.clicked.connect(self.new_chat)
        top.addWidget(self.status, 1)
        top.addWidget(self.new_button)
        lower.addLayout(top)
        self.quick = PromptStrip(8)
        self.example_buttons: list[QPushButton] = []
        for text in QUICK_PROMPTS:
            label = word(text)
            button = lab_button(label, "sg")
            button.setObjectName("AiChatExample")
            button.clicked.connect(lambda _checked=False, value=label: self.set_text(value))
            self.quick.add(button)
            self.example_buttons.append(button)
        lower.addWidget(self.quick)
        self.quick.hide()  # the welcome has its own suggestions; the strip comes after it
        lower.addWidget(self._build_composer())
        self.disclaimer = lab_label(
            word("The AI only advises. No real order is ever sent from here."),
            "footer",
            wrap=True,
        )
        self.disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lower.addWidget(self.disclaimer)
        outer.addWidget(centred(bottom))
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(TYPING_MS)
        self._typing.timeout.connect(self._rest)
        self._update()

    # Building --------------------------------------------------------------------------
    def _build_welcome(self) -> QWidget:
        word = self.word
        box = QWidget()
        box.setObjectName("AiChatWelcome")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        cap = lab_label("NEW CHAT", "cap")
        cap.setLayoutDirection(LTR)
        layout.addWidget(cap)
        layout.addWidget(lab_label(word("What shall we look at?"), "welcome", wrap=True))
        sub = "From charts and tables to the comparison of an AI suggestion. Pick one or write."
        layout.addWidget(lab_label(word(sub), "sub", wrap=True))
        self.suggestions = FlowBox(10)
        self.suggestion_buttons: list[QPushButton] = []
        for text in SUGGESTIONS:
            label = word(text)
            button = lab_button(label, "sg")
            button.setObjectName("AiChatSuggestion")
            button.clicked.connect(lambda _checked=False, value=label: self.set_text(value))
            self.suggestions.add(button)
            self.suggestion_buttons.append(button)
        layout.addSpacing(8)
        layout.addWidget(self.suggestions)
        return box

    def _build_composer(self) -> QWidget:
        word = self.word
        self.composer = ComposerBox()
        self.composer.setObjectName("AiChatComposer")
        box = QVBoxLayout(self.composer)
        box.setContentsMargins(14, 14, 14, 10)
        box.setSpacing(6)
        self.input = ChatInput()
        self.input.setObjectName("AiChatInput")
        self.input.setProperty("lab", "input")
        self.input.setPlaceholderText(
            word("Ask, ask for a chart, or paste the AI's answer here..."),
        )
        self.input.setAccessibleName(word("Your message"))
        self.input.setFixedHeight(72)
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.submitted.connect(self.send)
        self.input.textChanged.connect(self._typed)
        self.input.focused.connect(self._focus)
        box.addWidget(self.input)
        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.attach = LabIcon("clip", 18)
        self.attach.setToolTip(word("Attaching files comes later."))
        self.attach.setAccessibleName(word("Attach a file"))
        self.paper_tag = Tag("PAPER ONLY", "neutral")
        self.stop_button = lab_button(word("Stop"), "link")
        self.stop_button.setObjectName("AiChatStop")
        self.stop_button.clicked.connect(self.stop)
        self.send_button = SendButton()
        self.send_button.setObjectName("AiChatSend")
        self.send_button.setToolTip(word("Send"))
        self.send_button.setAccessibleName(word("Send"))
        self.send_button.clicked.connect(self._send_or_stop)
        tools.addWidget(self.attach)
        tools.addWidget(self.paper_tag)
        tools.addStretch(1)
        tools.addWidget(self.stop_button)
        tools.addWidget(self.send_button)
        box.addLayout(tools)
        return self.composer

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        apply_tree(self, tokens)

    # The composer ----------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        """A suggestion or a quick prompt: into the composer, not sent."""
        self.input.setPlainText(text)
        self.input.setFocus()
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)

    def _show_welcome(self, shown: bool) -> None:
        """The welcome with its suggestions, or the quick prompts once the chat began."""
        self.welcome.setVisible(shown)
        self.quick.setVisible(not shown)

    def _typed(self) -> None:
        if self._busy:
            return
        length = len(self.input.toPlainText())
        if length:
            self.composer.set_state("typing", length)
            self._typing.start()
        else:
            self._rest()
        self._update()

    def _rest(self) -> None:
        if not self._busy:
            self.composer.set_state("focus" if self.input.hasFocus() else "idle")

    def _focus(self, focused: bool) -> None:
        if not self._busy and self.composer.state != "typing":
            self.composer.set_state("focus" if focused else "idle")

    def _send_or_stop(self) -> None:
        if self._busy:
            self.stop()
        else:
            self.send()

    # Turns -----------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def send(self) -> bool:
        text = self.input.toPlainText().strip()
        if not text or self._busy:
            return False
        if self.intercept(text):
            self.input.clear()
            return True
        started = self.ask(text)
        if started:
            self.input.clear()
        return started

    def _append(self, widget: QWidget) -> None:
        if self.messages.indexOf(widget) >= 0:
            self.messages.removeWidget(widget)
        self.messages.addWidget(widget)
        widget.show()
        self._show_welcome(False)
        self._scroll_down()

    def add_bubble(self, text: str) -> None:
        """A message of the user that the page answers itself (no AI call)."""
        shown = text if len(text) <= 400 else text[:400] + " \u2026"
        row = bubble(shown)
        self.extras.append(row)
        self._append(row)

    def add_extra(self, widget: QWidget) -> None:
        """A one-off card (an export, an Ask AI request, a chart): gone with a new chat."""
        self.extras.append(widget)
        self._append(widget)

    def add_card(self, card: QWidget) -> None:
        """A card of the manual loop at the end of the transcript (moved there if shown)."""
        if card not in self.cards:
            self.cards.append(card)
        self._append(card)
        card.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def remove_card(self, card: QWidget) -> None:
        if self.messages.indexOf(card) >= 0:
            self.messages.removeWidget(card)
        card.hide()

    def shown(self, card: QWidget) -> bool:
        return self.messages.indexOf(card) >= 0

    def ask(self, question: str) -> bool:
        """Start one turn in the worker thread; False when it cannot start."""
        if self._busy or not question.strip():
            return False
        found = self.client()
        if isinstance(found, str):
            self.status.setText(found)
            return False
        client: AiClient = found
        view = TurnView(question, fa=self.fa)
        apply_tree(view, self.tokens)
        self.views.append(view)
        self._append(view)
        self._busy = True
        self._cancel.clear()
        self.composer.set_state("busy")
        self.status.setText(f"Asking {client.settings.model}...")
        self._update()
        tools = list(self.tools())
        history = list(self.turns)
        limits = self.limits
        language = self.language()
        bridge = self.bridge
        cancel = self._cancel

        def complete(messages: Sequence[Message]) -> Completion:
            return client.complete(messages, max_tokens=limits.max_tokens)

        def job() -> None:
            try:
                turn = run_turn(
                    complete,
                    tools,
                    question,
                    history=history,
                    limits=limits,
                    language=language,
                    on_step=bridge.step.emit,
                    cancelled=cancel.is_set,
                )
            except AiCallError as error:
                bridge.failed.emit(plain_text(error, client.settings.base_url))
                return
            except Exception as error:
                bridge.failed.emit(f"{type(error).__name__}: {error}")
                return
            bridge.done.emit(turn)

        self.start_job(job)
        return True

    def stop(self) -> None:
        if self._busy:
            self._cancel.set()
            self.status.setText(self.word("Stopping after this step..."))

    def new_chat(self) -> None:
        if self._busy:
            return
        self._clear()
        self.chat = None
        self._show_welcome(True)
        self.status.setText(self.word("New chat: the AI forgets the earlier questions."))
        self.chat_changed.emit("")
        self._update()

    def open_chat(self, chat_id: str) -> bool:
        """A saved chat back in the transcript; the AI remembers its questions again."""
        if self._busy or self.store is None:
            return False
        chat = self.store.load(chat_id)
        if chat is None:
            return False
        self._clear()
        self.chat = chat
        for turn in chat.turns:
            view = TurnView(turn.question, fa=self.fa)
            for step in turn.steps:
                view.add_step(step)
            view.finish(turn)
            seconds = sum(step.seconds for step in turn.steps)
            view.meta.setText(worked_line(seconds, turn.tool_steps, self.fa))
            apply_tree(view, self.tokens)
            self.views.append(view)
            self._append(view)
        self.turns = list(chat.turns)
        self._show_welcome(not chat.turns)
        self.status.setText("")
        self.chat_changed.emit(chat.chat_id)
        self._update()
        return True

    def _clear(self) -> None:
        for view in self.views:
            self.messages.removeWidget(view)
            view.deleteLater()
        for extra in self.extras:
            self.messages.removeWidget(extra)
            extra.deleteLater()
        for card in self.cards:
            self.remove_card(card)
        self.views.clear()
        self.extras.clear()
        self.turns.clear()

    def show_step(self, step: object) -> None:
        if isinstance(step, Step) and self.views:
            self.views[-1].add_step(step)
            self._scroll_down()

    def _idle(self) -> None:
        self._busy = False
        self.composer.set_state("focus" if self.input.hasFocus() else "idle")

    def show_turn(self, turn: object) -> None:
        self._idle()
        if isinstance(turn, Turn):
            self.turns.append(turn)
            if self.views:
                self.views[-1].finish(turn)
            self.status.setText(turn.stopped.capitalize() if turn.stopped else "")
            self._save(turn)
            self.turn_done.emit(turn)
        self._update()
        self._scroll_down()

    def _save(self, turn: Turn) -> None:
        """The finished turn into the saved chat (question, steps, answer, tokens; no key)."""
        if self.store is None:
            return
        now = self.clock()
        chat = (self.chat or start_chat(now)).with_turn(turn, now)
        try:
            self.store.save(chat)
        except OSError as error:
            self.status.setText(f"The chat was not saved: {error}")
            return
        self.chat = chat
        self.chat_changed.emit(chat.chat_id)

    def show_failure(self, text: str) -> None:
        self._idle()
        if self.views:
            self.views[-1].fail(text)
        self.status.setText(f"The AI request failed: {text}")
        self._update()

    def _scroll_down(self) -> None:
        QTimer.singleShot(0, self._to_bottom)

    def _to_bottom(self) -> None:
        bar = self.transcript.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _update(self) -> None:
        busy = self._busy
        self.send_button.set_look(bool(self.input.toPlainText().strip()), busy)
        self.send_button.setToolTip(self.word("Stop") if busy else self.word("Send"))
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.new_button.setEnabled(not busy)
        for button in [*self.example_buttons, *self.suggestion_buttons]:
            button.setEnabled(not busy)
