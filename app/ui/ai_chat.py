"""The AI Lab chat (docs/AI_LAB_AGENT.md sections 1 and 2, docs/UI_V2.md 19b).

Ask in your own words; the agent looks things up with read-only tools and answers. While it
works, each step shows up as a line ("Read the trades (days 30)  0.2 s"); when it is done
the steps fold into one thinking line ("Thought for 3.4 s \u00b7 2 tools \u00b7 6.1k
tokens") that opens on a click. The answer is Markdown, and under it one cost line. Stop
(or Esc) ends the turn between two steps; Ctrl+Enter sends. Persian text runs right to
left. Nothing here changes a setting or trades.

UI v2 (19b): a history rail on the left keeps every chat (`app.ai.chat_store`): new chat,
search, All / Pinned, date groups, pin and delete. A chat is saved after each answer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ai.agent import Limits, Step, Tool, Turn, run_turn
from app.ai.chat_store import Chat, ChatStore, grouped, start_chat
from app.ai.transport import AiCallError, AiClient, Completion, Message, plain_text
from app.ui.pages import card_frame, styled_label

Job = Callable[[], None]
ClientFactory = Callable[[], AiClient | str]
CHAT_ID = Qt.ItemDataRole.UserRole
RAIL_WIDTH = 260
WELCOME = (
    'Ask about your trades, strategies and settings, for example: "Why did channel_breakout '
    'lose this week?" or "Which session is my best?". The AI reads the app with read-only '
    "tools; it never changes a setting or trades."
)
EXAMPLES = (
    "How did each strategy do in the last 30 days?",
    "Which strategy should I turn off, and why?",
    "Explain my last losing trade.",
)
SHOW_STEPS = "\u25b8 Show steps"
HIDE_STEPS = "\u25be Hide steps"


def _start_thread(job: Job) -> None:
    threading.Thread(target=job, name="ai-lab-agent", daemon=True).start()


def _no_tools() -> Sequence[Tool]:
    return ()


def _app_language() -> str:
    return ""


def persian(text: str) -> bool:
    """The first letter is Persian or Arabic: the message runs right to left (the rule of
    the first strong character, as in Unicode bidi)."""
    for char in text:
        if char.isalpha():
            return "\u0600" <= char <= "\u06ff"
    return False


def thinking_line(turn: Turn) -> str:
    """The folded steps of a finished turn, e.g. "Thought for 3.4 s · 2 tools · 6.1k tokens"."""
    seconds = sum(step.seconds for step in turn.steps)
    tools = turn.tool_steps
    usage = turn.usage
    tokens = usage.input_tokens + usage.output_tokens
    parts = [f"Thought for {seconds:.1f} s" if seconds >= 0.05 else "Answered"]
    parts.append(f"{tools} tool" if tools == 1 else f"{tools} tools")
    parts.append(f"{tokens / 1000:.1f}k tokens")
    return " \u00b7 ".join(parts)


def chat_line(chat: Chat) -> str:
    """The second line of a chat in the rail: questions and cost."""
    count = len(chat.turns)
    text = f"{count} question" if count == 1 else f"{count} questions"
    if chat.cost > 0:
        text += f" \u00b7 ${chat.cost:.3f}"
    return text


def _label(text: str, role: str, *, markdown: bool = False, source: str = "") -> QLabel:
    label = styled_label(text, role, wrap=True)
    label.setTextFormat(Qt.TextFormat.MarkdownText if markdown else Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if persian(source or text):
        label.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    return label


class TurnView(QFrame):
    """One question with its thinking line, its steps, its answer and its cost line."""

    def __init__(self, question: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatTurn")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.question = _label(question, "heading", source=question)
        layout.addWidget(self.question)
        head = QHBoxLayout()
        self.head = _label("\u2026 Thinking", "muted")
        self.head.setObjectName("AiChatThinking")
        head.addWidget(self.head, 1)
        self.toggle = QPushButton(HIDE_STEPS)
        self.toggle.setObjectName("AiChatStepsToggle")
        self.toggle.setProperty("variant", "ghost")
        self.toggle.setToolTip("Show or hide what the AI looked up")
        self.toggle.clicked.connect(self.toggle_steps)
        self.toggle.hide()
        head.addWidget(self.toggle)
        layout.addLayout(head)
        self.steps = _label("", "muted")
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

    @property
    def steps_open(self) -> bool:
        return not self.steps.isHidden()

    def add_step(self, step: Step) -> None:
        self.lines.append(step.line())
        self.steps.setText("\n".join(self.lines))
        tools = sum(1 for line in self.lines if not line.startswith("\u2026"))
        self.head.setText(f"\u2026 Working \u00b7 {tools} step(s)")

    def toggle_steps(self) -> None:
        shown = not self.steps_open
        self.steps.setVisible(shown)
        self.toggle.setText(HIDE_STEPS if shown else SHOW_STEPS)

    def finish(self, turn: Turn) -> None:
        if not self.lines:
            self.lines = [step.line() for step in turn.steps]
        self.head.setText(thinking_line(turn))
        self.steps.setText("\n".join(self.lines))
        self.steps.setVisible(False)
        self.toggle.setText(SHOW_STEPS)
        self.toggle.setVisible(bool(self.lines))
        text = turn.answer or f"_{turn.stopped.capitalize()}._"
        self.answer.setText(text)
        if persian(text):
            self.answer.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.answer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.answer.show()
        self.cost.setText(turn.cost_line())
        self.cost.show()

    def fail(self, text: str) -> None:
        self.head.setText(f"\u2717 {text}")
        self.steps.setText("\n".join(self.lines))


class _Bridge(QObject):
    step = Signal(object)
    done = Signal(object)
    failed = Signal(str)


class HistoryRail(QFrame):
    """The saved chats: new chat, search, All / Pinned, date groups, pin and delete."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatRail")
        self.setProperty("role", "card")
        self.setFixedWidth(RAIL_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.new_button = QPushButton("+  New chat")
        self.new_button.setObjectName("AiChatNew")
        self.new_button.setProperty("variant", "primary")
        layout.addWidget(self.new_button)
        self.search = QLineEdit()
        self.search.setObjectName("AiChatSearch")
        self.search.setPlaceholderText("Search chats")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        filters.setSpacing(4)
        self.filter_group = QButtonGroup(self)
        self.all_button = QPushButton("All")
        self.pinned_button = QPushButton("Pinned")
        for name, button in (("AiChatAll", self.all_button), ("AiChatPinned", self.pinned_button)):
            button.setObjectName(name)
            button.setCheckable(True)
            self.filter_group.addButton(button)
            filters.addWidget(button)
        self.all_button.setChecked(True)
        filters.addStretch(1)
        layout.addLayout(filters)
        self.list = QListWidget()
        self.list.setObjectName("AiChatHistory")
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)
        actions = QHBoxLayout()
        self.pin_button = QPushButton("Pin")
        self.pin_button.setObjectName("AiChatPin")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("AiChatDelete")
        self.delete_button.setProperty("variant", "danger")
        actions.addWidget(self.pin_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.empty = styled_label("No saved chats yet.", "muted", wrap=True)
        self.empty.setObjectName("AiChatHistoryEmpty")
        layout.addWidget(self.empty)

    @property
    def pinned_only(self) -> bool:
        return self.pinned_button.isChecked()

    def selected_id(self) -> str:
        item = self.list.currentItem()
        found = item.data(CHAT_ID) if item is not None else None
        return found if isinstance(found, str) else ""

    def chat_ids(self) -> list[str]:
        """The chats shown, top to bottom (the group headers are skipped)."""
        found: list[str] = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            value = item.data(CHAT_ID) if item is not None else None
            if isinstance(value, str):
                found.append(value)
        return found

    def show_chats(self, chats: Sequence[Chat], now: float, current: str) -> None:
        wanted = self.search.text()
        shown = [c for c in chats if c.matches(wanted) and (c.pinned or not self.pinned_only)]
        self.list.blockSignals(True)
        self.list.clear()
        for name, members in grouped(shown, now):
            header = QListWidgetItem(name.upper())
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(header)
            for chat in members:
                mark = "\u25c6 " if chat.pinned else ""
                item = QListWidgetItem(f"{mark}{chat.title}\n{chat_line(chat)}")
                item.setData(CHAT_ID, chat.chat_id)
                item.setToolTip(chat.title)
                self.list.addItem(item)
                if chat.chat_id == current:
                    self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.empty.setVisible(not chats)
        self.empty.setText("No saved chats yet." if not chats else "")


class ChatPanel(QWidget):
    def __init__(
        self,
        client: ClientFactory,
        tools: Callable[[], Sequence[Tool]] = _no_tools,
        language: Callable[[], str] = _app_language,
        parent: QWidget | None = None,
        store: ChatStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatPanel")
        self.client = client
        self.tools = tools
        self.language = language
        self.store = store
        self.clock = clock
        self.limits = Limits()
        self.start_job: Callable[[Job], None] = _start_thread
        self.confirm: Callable[[str], bool] = self._ask
        self.chat: Chat = start_chat(clock())
        self.turns: list[Turn] = []
        self.views: list[TurnView] = []
        self._cancel = threading.Event()
        self._busy = False
        self.bridge = _Bridge()
        self.bridge.step.connect(self.show_step, Qt.ConnectionType.QueuedConnection)
        self.bridge.done.connect(self.show_turn, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.rail = HistoryRail()
        self.rail.setVisible(store is not None)
        outer.addWidget(self.rail)
        card, layout = card_frame()
        outer.addWidget(card, 1)
        self.title = styled_label("New chat", "heading")
        self.title.setObjectName("AiChatTitle")
        layout.addWidget(self.title)
        self.transcript = QScrollArea()
        self.transcript.setObjectName("AiChatTranscript")
        self.transcript.setWidgetResizable(True)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setMinimumHeight(360)
        body = QWidget()
        self.messages = QVBoxLayout(body)
        self.messages.setSpacing(18)
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
        self.input.setPlaceholderText("Ask about trades, strategies, settings... (Ctrl+Enter)")
        self.input.setFixedHeight(72)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("AiChatSend")
        self.send_button.setProperty("variant", "primary")
        self.send_button.clicked.connect(self.send)
        self.stop_button = QPushButton("Stop  Esc")
        self.stop_button.setObjectName("AiChatStop")
        self.stop_button.clicked.connect(self.stop)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        buttons = QVBoxLayout()
        for button in (self.send_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        layout.addLayout(row)
        footer = QHBoxLayout()
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("AiChatStatus")
        footer.addWidget(self.status, 1)
        self.budget = styled_label("", "status")
        self.budget.setObjectName("AiChatBudget")
        footer.addWidget(self.budget)
        layout.addLayout(footer)
        self.new_button = self.rail.new_button
        self.new_button.clicked.connect(self.new_chat)
        self.rail.search.textChanged.connect(lambda _text: self.refresh_history())
        self.rail.filter_group.buttonClicked.connect(lambda _button: self.refresh_history())
        self.rail.list.itemClicked.connect(self._open_item)
        self.rail.pin_button.clicked.connect(self.toggle_pin)
        self.rail.delete_button.clicked.connect(self.delete_selected)
        send_keys = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        send_keys.setContext(Qt.ShortcutContext.WidgetShortcut)
        send_keys.activated.connect(self.send)
        stop_keys = QShortcut(QKeySequence("Escape"), self)
        stop_keys.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        stop_keys.activated.connect(self.stop)
        self.refresh_history()
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
        self._clear_views()
        self.chat = start_chat(self.clock())
        self.welcome.show()
        self.status.setText("New chat: the AI forgets the earlier questions.")
        self.refresh_history()
        self._update()

    def open_chat(self, chat_id: str) -> bool:
        """Show a saved chat; its questions are the history of the next question."""
        if self._busy or self.store is None:
            return False
        found = self.store.load(chat_id)
        if found is None:
            self.status.setText("That chat could not be read.")
            self.refresh_history()
            return False
        self._clear_views()
        self.chat = found
        for turn in found.turns:
            view = TurnView(turn.question)
            view.finish(turn)
            self.messages.insertWidget(self.messages.count() - 1, view)
            self.views.append(view)
            self.turns.append(turn)
        self.welcome.setVisible(not found.turns)
        self.status.setText("")
        self.refresh_history()
        self._update()
        return True

    def toggle_pin(self) -> None:
        chat_id = self.rail.selected_id() or self.chat.chat_id
        if self.store is None:
            return
        saved = self.store.load(chat_id)
        if saved is None:
            self.status.setText("Pin works on saved chats: ask a question first.")
            return
        changed = self.store.set_pinned(chat_id, not saved.pinned)
        if changed is not None and changed.chat_id == self.chat.chat_id:
            self.chat = changed
        self.refresh_history()

    def delete_selected(self) -> bool:
        chat_id = self.rail.selected_id()
        if self.store is None or not chat_id or self._busy:
            return False
        saved = self.store.load(chat_id)
        title = saved.title if saved is not None else "this chat"
        if not self.confirm(f'Delete "{title}"? This cannot be undone.'):
            return False
        self.store.delete(chat_id)
        if chat_id == self.chat.chat_id:
            self.new_chat()
        self.refresh_history()
        return True

    def refresh_history(self) -> None:
        store = self.store
        chats = store.chats() if store is not None else []
        self.rail.show_chats(chats, self.clock(), self.chat.chat_id)
        chosen = self.rail.selected_id()
        selected = next((chat for chat in chats if chat.chat_id == chosen), None)
        self.rail.pin_button.setText("Unpin" if selected is not None and selected.pinned else "Pin")
        self.title.setText(self.chat.title)
        self._show_budget()

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
            self.chat = self.chat.with_turn(turn, self.clock())
            self._save()
        self.refresh_history()
        self._update()
        self._scroll_down()

    def show_failure(self, text: str) -> None:
        self._busy = False
        if self.views:
            self.views[-1].fail(text)
        self.status.setText(f"The AI request failed: {text}")
        self._update()

    def _save(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save(self.chat)
        except OSError as error:
            self.status.setText(f"The chat could not be saved: {error}")

    def _open_item(self, item: QListWidgetItem) -> None:
        chat_id = item.data(CHAT_ID)
        if isinstance(chat_id, str) and chat_id != self.chat.chat_id:
            self.open_chat(chat_id)

    def _clear_views(self) -> None:
        for view in self.views:
            self.messages.removeWidget(view)
            view.deleteLater()
        self.views.clear()
        self.turns.clear()

    def _show_budget(self) -> None:
        cap = self.limits.max_usd
        limit = f"limit ${cap:.2f} / question" if cap > 0 else "no cost limit"
        spent = self.chat.cost
        self.budget.setText(f"${spent:.3f} this chat \u00b7 {limit}" if spent else limit)

    def _ask(self, text: str) -> bool:
        answer = QMessageBox.question(self, "AI Lab", text)
        return answer == QMessageBox.StandardButton.Yes

    def _scroll_down(self) -> None:
        bar = self.transcript.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _update(self) -> None:
        self.send_button.setEnabled(not self._busy)
        self.stop_button.setEnabled(self._busy)
        self.new_button.setEnabled(not self._busy)
        self.rail.list.setEnabled(not self._busy)
        self.rail.delete_button.setEnabled(not self._busy)
        for button in self.example_buttons:
            button.setEnabled(not self._busy)
