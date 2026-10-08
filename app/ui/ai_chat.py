"""The AI Lab chat (docs/AI_LAB_AGENT.md sections 1 and 2, Phases 18b and 18c).

Ask in your own words; the agent looks things up with read-only tools and answers. While it
works, each step shows up as a line ("Read the trades (days 30)  0.2 s"); the answer is
Markdown, and under it one cost line. Stop ends the turn between two steps. Persian text
runs right to left. Nothing here changes a setting or trades.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ai.agent import Limits, Step, Tool, Turn, run_turn
from app.ai.transport import AiCallError, AiClient, Completion, Message, plain_text
from app.ui.pages import card_frame, styled_label

Job = Callable[[], None]
ClientFactory = Callable[[], AiClient | str]
WELCOME = (
    "Ask about your trades, strategies and settings, for example: \"Why did channel_breakout "
    "lose this week?\" or \"Which session is my best?\". The AI reads the app with read-only "
    "tools; it never changes a setting or trades."
)
EXAMPLES = (
    "How did each strategy do in the last 30 days?",
    "Which strategy should I turn off, and why?",
    "Explain my last losing trade.",
)


def _start_thread(job: Job) -> None:
    threading.Thread(target=job, name="ai-lab-agent", daemon=True).start()


def _no_tools() -> Sequence[Tool]:
    return ()


def _app_language() -> str:
    return ""


def persian(text: str) -> bool:
    """Mostly Persian or Arabic letters: the message runs right to left."""
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    rtl = sum(1 for char in letters if "\u0600" <= char <= "\u06ff")
    return rtl * 2 >= len(letters)


def _label(text: str, role: str, *, markdown: bool = False) -> QLabel:
    label = styled_label(text, role, wrap=True)
    label.setTextFormat(Qt.TextFormat.MarkdownText if markdown else Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if persian(text):
        label.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    return label


class TurnView(QFrame):
    """One question with its steps, its answer and its cost line."""

    def __init__(self, question: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatTurn")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.question = _label(f"You: {question}", "heading")
        layout.addWidget(self.question)
        self.steps = _label("\u2026 Thinking", "muted")
        self.steps.setObjectName("AiChatSteps")
        layout.addWidget(self.steps)
        self.answer = _label("", "body", markdown=True)
        self.answer.setObjectName("AiChatAnswer")
        self.answer.hide()
        layout.addWidget(self.answer)
        self.cost = _label("", "muted")
        self.cost.setObjectName("AiChatCost")
        self.cost.hide()
        layout.addWidget(self.cost)
        self.lines: list[str] = []

    def add_step(self, step: Step) -> None:
        self.lines.append(step.line())
        self.steps.setText("\n".join([*self.lines, "\u2026 Working"]))

    def finish(self, turn: Turn) -> None:
        count = turn.tool_steps
        head = f"Worked {count} step(s)" if count else "Answered directly"
        self.steps.setText("\n".join([head, *self.lines]) if self.lines else head)
        text = turn.answer or f"_{turn.stopped.capitalize()}._"
        self.answer.setText(text)
        if persian(text):
            self.answer.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.answer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.answer.show()
        self.cost.setText(turn.cost_line())
        self.cost.show()

    def fail(self, text: str) -> None:
        lines = [*self.lines, f"\u2717 {text}"]
        self.steps.setText("\n".join(lines))


class _Bridge(QObject):
    step = Signal(object)
    done = Signal(object)
    failed = Signal(str)


class ChatPanel(QWidget):
    def __init__(
        self,
        client: ClientFactory,
        tools: Callable[[], Sequence[Tool]] = _no_tools,
        language: Callable[[], str] = _app_language,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatPanel")
        self.client = client
        self.tools = tools
        self.language = language
        self.limits = Limits()
        self.start_job: Callable[[Job], None] = _start_thread
        self.turns: list[Turn] = []
        self.views: list[TurnView] = []
        self._cancel = threading.Event()
        self._busy = False
        self.bridge = _Bridge()
        self.bridge.step.connect(self.show_step, Qt.ConnectionType.QueuedConnection)
        self.bridge.done.connect(self.show_turn, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card, layout = card_frame()
        outer.addWidget(card, 1)
        self.transcript = QScrollArea()
        self.transcript.setObjectName("AiChatTranscript")
        self.transcript.setWidgetResizable(True)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setMinimumHeight(360)
        body = QWidget()
        self.messages = QVBoxLayout(body)
        self.messages.setSpacing(14)
        self.welcome = _label(WELCOME, "muted")
        self.messages.addWidget(self.welcome)
        examples = QHBoxLayout()
        self.example_buttons: list[QPushButton] = []
        for text in EXAMPLES:
            button = QPushButton(text)
            button.setObjectName("AiChatExample")
            button.clicked.connect(lambda _checked=False, value=text: self.ask(value))
            examples.addWidget(button)
            self.example_buttons.append(button)
        examples.addStretch(1)
        self.messages.addLayout(examples)
        self.messages.addStretch(1)
        self.transcript.setWidget(body)
        layout.addWidget(self.transcript, 1)
        self.input = QPlainTextEdit()
        self.input.setObjectName("AiChatInput")
        self.input.setPlaceholderText("Ask about trades, strategies, settings...")
        self.input.setFixedHeight(72)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("AiChatSend")
        self.send_button.setProperty("variant", "primary")
        self.send_button.clicked.connect(self.send)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("AiChatStop")
        self.stop_button.clicked.connect(self.stop)
        self.new_button = QPushButton("New chat")
        self.new_button.setObjectName("AiChatNew")
        self.new_button.clicked.connect(self.new_chat)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        buttons = QVBoxLayout()
        for button in (self.send_button, self.stop_button, self.new_button):
            buttons.addWidget(button)
        row.addLayout(buttons)
        layout.addLayout(row)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("AiChatStatus")
        layout.addWidget(self.status)
        self._update()

    @property
    def busy(self) -> bool:
        return self._busy

    def send(self) -> bool:
        text = self.input.toPlainText().strip()
        if not text:
            return False
        started = self.ask(text)
        if started:
            self.input.clear()
        return started

    def ask(self, question: str) -> bool:
        """Start one turn in the worker thread; False when it cannot start."""
        if self._busy or not question.strip():
            return False
        found = self.client()
        if isinstance(found, str):
            self.status.setText(found)
            return False
        client: AiClient = found
        view = TurnView(question)
        self.messages.insertWidget(self.messages.count() - 1, view)
        self.views.append(view)
        self.welcome.hide()
        self._busy = True
        self._cancel.clear()
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
            self.status.setText("Stopping after this step...")

    def new_chat(self) -> None:
        if self._busy:
            return
        for view in self.views:
            self.messages.removeWidget(view)
            view.deleteLater()
        self.views.clear()
        self.turns.clear()
        self.welcome.show()
        self.status.setText("New chat: the AI forgets the earlier questions.")
        self._update()

    def show_step(self, step: object) -> None:
        if isinstance(step, Step) and self.views:
            self.views[-1].add_step(step)
            self._scroll_down()

    def show_turn(self, turn: object) -> None:
        self._busy = False
        if isinstance(turn, Turn):
            self.turns.append(turn)
            if self.views:
                self.views[-1].finish(turn)
            self.status.setText(turn.stopped.capitalize() if turn.stopped else "")
        self._update()
        self._scroll_down()

    def show_failure(self, text: str) -> None:
        self._busy = False
        if self.views:
            self.views[-1].fail(text)
        self.status.setText(f"The AI request failed: {text}")
        self._update()

    def _scroll_down(self) -> None:
        bar = self.transcript.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _update(self) -> None:
        self.send_button.setEnabled(not self._busy)
        self.stop_button.setEnabled(self._busy)
        self.new_button.setEnabled(not self._busy)
        for button in self.example_buttons:
            button.setEnabled(not self._busy)
