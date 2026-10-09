"""The AI Lab's inspector (docs/NOCURVE_V2.md 20e3): a 372 px aside, the panel and a 58 px
rail of six buttons, as in the owner's design.

- Settings: the export scope, the optional Ask AI connection, the comparison symbol and
  period (the page puts its controls in).
- History: the saved chats in date groups, search, a new chat; right-click a chat to pin,
  rename or delete it (delete asks first).
- Prompts: the saved prompts (a click puts one in the composer) and "Save the current
  prompt".
- Files: the Lab's exports with their real size and time (a click opens the file).
- Experiments: one row per checked suggestion with its verdict, and the equity of the current
  settings against the suggestion from its comparison.
- Usage: the tokens and cost of the month, today against the daily cap, the requests of the
  last 7 days and the model.

Every panel reads its store when it opens; nothing here sends a request.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai.chat_store import Chat, ChatStore, grouped
from app.ai.experiments import Experiment, ExperimentStore
from app.ai.lab_files import LabFile, inside_exports, lab_files, size_text
from app.ai.prompt_store import Prompt, PromptStore
from app.ai.usage_store import UsageStore, short_tokens
from app.ui.lab_charts import BarChart, Line, LineChart, Meter
from app.ui.lab_parts import (
    LTR,
    MIDDLE,
    RailButton,
    apply_tree,
    lab_button,
    lab_label,
    lab_words,
    mono_label,
    restyle,
)
from app.ui.theme import ThemeTokens
from app.ui.v2 import Tag

ASIDE_WIDTH = 372
RAIL_WIDTH = 58
DAY = 86_400
RAIL = (
    ("settings", "gear", "Settings"),
    ("history", "clock", "History"),
    ("prompts", "bookmark", "Prompts"),
    ("files", "file", "Files"),
    ("experiments", "flask", "Experiments"),
    ("usage", "bars", "Usage"),
)
INSPECTOR_FA: dict[str, str] = {
    "Chat history": "تاریخچه‌ی گفتگوها",
    "Search the chats...": "جستجو در گفتگوها...",
    "Search the history": "جستجو در تاریخچه",
    "No saved chats yet.": "هنوز گفتگویی ذخیره نشده.",
    "Pinned": "سنجاق‌شده",
    "Today": "امروز",
    "Yesterday": "دیروز",
    "This week": "این هفته",
    "Older": "قدیمی‌تر",
    "Pin": "سنجاق کردن",
    "Unpin": "برداشتن سنجاق",
    "Rename": "تغییر نام",
    "Delete": "حذف",
    "Delete this chat?": "این گفتگو حذف شود؟",
    "New title": "عنوان تازه",
    "Saved prompts": "پرامپت‌های ذخیره‌شده",
    "Saved prompts. A click puts one in the message box.": (
        "پرامپت‌های ذخیره‌شده. با کلیک وارد کادر پیام می‌شوند."
    ),
    "Save the current prompt": "ذخیره‌ی پرامپت فعلی",
    "Files and exports": "فایل‌ها و خروجی‌ها",
    "No export yet: press Export for AI.": "هنوز خروجی‌ای نیست: Export for AI را بزنید.",
    "Open the folder": "باز کردن پوشه",
    "Copy prompt": "Copy prompt",
    "Experiments": "آزمایش‌ها",
    "No experiment yet: paste an AI answer to check it.": (
        "هنوز آزمایشی نیست: پاسخ یک AI را بچسبانید تا بررسی شود."
    ),
    "Equity curve": "منحنی سرمایه",
    "same period": "همان بازه",
    "Current": "فعلی",
    "Suggested": "پیشنهاد",
    "Not compared yet.": "هنوز مقایسه نشده.",
    "Usage and cost": "مصرف و هزینه",
    "tokens this month": "توکن ماه",
    "Estimated cost this month": "هزینه‌ی تخمینی ماه",
    "Today against the daily cap": "امروز در برابر سقف روزانه",
    "Requests in the last 7 days": "درخواست‌ها در ۷ روز اخیر",
    "Model": "مدل",
    "Requests this month": "تعداد درخواست",
    "Every click is one request. Nothing is sent by itself.": (
        "هر کلیک یک درخواست است. چیزی خودکار ارسال نمی‌شود."
    ),
    "{count} without a price": "{count} بدون قیمت",
    "today {time}": "امروز {time}",
    "yesterday": "دیروز",
    "{days} days ago": "{days} روز پیش",
}
FA_DIGITS = str.maketrans("0123456789", "".join(chr(0x06F0 + digit) for digit in range(10)))


def inspector_words(fa: bool) -> Callable[[str], str]:
    lab = lab_words(fa)

    def word(english: str) -> str:
        return INSPECTOR_FA.get(english, lab(english)) if fa else english

    return word


def when_text(moment: float, now: float, fa: bool) -> str:
    """today 14:02, yesterday, 3 days ago, or the date."""
    word = inspector_words(fa)
    day = datetime.fromtimestamp(moment).date()
    age = (datetime.fromtimestamp(now).date() - day).days
    if age <= 0:
        clock = datetime.fromtimestamp(moment).strftime("%H:%M")
        return word("today {time}").format(time=clock)
    if age == 1:
        return word("yesterday")
    if age < 7:
        text = word("{days} days ago").format(days=age)
        return text.translate(FA_DIGITS) if fa else text
    return day.isoformat()


def panel(title: str) -> tuple[QWidget, QVBoxLayout]:
    """A panel's body: the bold title, then its content (padding 20 / 20 / 24)."""
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(20, 20, 20, 24)
    layout.setSpacing(12)
    heading = lab_label(title, "heading")
    heading.setObjectName("AiPanelTitle")
    layout.addWidget(heading)
    return body, layout


def section(layout: QVBoxLayout, title: str, tag: str = "") -> None:
    row = QHBoxLayout()
    row.setSpacing(10)
    row.addWidget(lab_label(title, "card_title"), 0, MIDDLE)
    if tag:
        row.addWidget(Tag(tag, "neutral"), 0, MIDDLE)
    row.addStretch(1)
    layout.addSpacing(10)
    layout.addLayout(row)


def field(layout: QVBoxLayout, label: str, widget: QWidget) -> None:
    layout.addWidget(lab_label(label, "label"))
    layout.addWidget(widget)


def clear(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()


class ItemButton(QPushButton):
    """A clickable row: a title (and a line under it) at the start, a tag or text at the end."""

    def __init__(
        self,
        title: str,
        under: str = "",
        end: str = "",
        *,
        mono: bool = False,
        tag: bool = False,
        start: str = "",
    ) -> None:
        super().__init__()
        self.setProperty("lab", "item")
        self.setProperty("current", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setToolTip(title)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        if start:
            first = mono_label(start)
            first.setFixedWidth(30)
            row.addWidget(first, 0, MIDDLE)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        main = mono_label(title) if mono else lab_label(title, "text")
        if mono:
            main.setProperty("size", "small")
        main.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        texts.addWidget(main)
        self.title_label = main
        if under:
            note = lab_label(under, "note")
            texts.addWidget(note)
        row.addLayout(texts, 1)
        self.end: QWidget | None = None
        if end:
            self.end = Tag(end, "neutral") if tag else lab_label(end, "note")
            row.addWidget(self.end, 0, MIDDLE)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumHeight(50 if under or start else 40)

    def set_current(self, current: bool) -> None:
        self.setProperty("current", "true" if current else "false")
        restyle(self)


class Inspector(QFrame):
    """The aside: the open panel and the rail."""

    opened = Signal(str)

    def __init__(self, fa: bool = False, enabled: Sequence[str] | None = None) -> None:
        super().__init__()
        self.setObjectName("AiLabInspector")
        self.setProperty("lab", "aside")
        self.setFixedWidth(ASIDE_WIDTH)
        self.fa = fa
        self.word = inspector_words(fa)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        rule = QFrame()
        rule.setProperty("lab", "vrule")
        rule.setFixedWidth(1)
        row.addWidget(rule)
        rail = QWidget()
        rail.setFixedWidth(RAIL_WIDTH - 1)
        column = QVBoxLayout(rail)
        column.setContentsMargins(8, 10, 8, 10)
        column.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, RailButton] = {}
        self.panels: dict[str, int] = {}
        for key, icon, name in RAIL:
            button = RailButton(icon, self.word(name))
            button.setObjectName(f"AiRail_{key}")
            button.clicked.connect(lambda _checked=False, value=key: self.show_panel(value))
            self.group.addButton(button)
            column.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            self.buttons[key] = button
            if enabled is not None and key not in enabled:
                button.setEnabled(False)
        column.addStretch(1)
        row.addWidget(rail)
        self.current = ""

    def add_panel(self, key: str, body: QWidget) -> None:
        scroll = QScrollArea()
        scroll.setObjectName(f"AiPanel_{key}")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.panels[key] = self.stack.addWidget(scroll)

    def show_panel(self, key: str) -> bool:
        index = self.panels.get(key)
        button = self.buttons.get(key)
        if index is None or button is None or not button.isEnabled():
            return False
        self.stack.setCurrentIndex(index)
        button.setChecked(True)
        self.current = key
        self.opened.emit(key)
        return True

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        apply_tree(self, tokens)


def _ask(parent: QWidget, text: str) -> bool:
    answer = QMessageBox.question(parent, "AI Lab", text)
    return answer == QMessageBox.StandardButton.Yes


class HistoryPanel(QWidget):
    open_chat = Signal(str)
    new_chat = Signal()
    changed = Signal()

    def __init__(self, store: ChatStore | None, fa: bool = False) -> None:
        super().__init__()
        self.store = store
        self.fa = fa
        word = inspector_words(fa)
        self.word = word
        self.confirm: Callable[[str], bool] = lambda text: _ask(self, text)
        self.rename_text: Callable[[str], str | None] = self._rename_text
        self.current = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = panel(word("Chat history"))
        outer.addWidget(body)
        self.new_button = lab_button(word("New chat"), "ghost")
        self.new_button.setObjectName("AiHistoryNew")
        self.new_button.clicked.connect(self.new_chat.emit)
        layout.addWidget(self.new_button)
        self.search = QLineEdit()
        self.search.setObjectName("AiHistorySearch")
        self.search.setProperty("lab", "in")
        self.search.setPlaceholderText(word("Search the chats..."))
        self.search.setAccessibleName(word("Search the history"))
        self.search.setLayoutDirection(Qt.LayoutDirection.RightToLeft if fa else LTR)
        self.search.textChanged.connect(lambda _text: self.refresh())
        layout.addWidget(self.search)
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        layout.addWidget(holder)
        layout.addStretch(1)
        self.buttons: dict[str, ItemButton] = {}

    def chats(self) -> list[Chat]:
        if self.store is None:
            return []
        wanted = self.search.text()
        return [chat for chat in self.store.chats() if chat.matches(wanted)]

    def refresh(self, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        clear(self.rows)
        self.buttons = {}
        found = self.chats()
        if not found:
            self.rows.addWidget(lab_label(self.word("No saved chats yet."), "note", wrap=True))
            return
        for name, chats in grouped(found, moment):
            cap = lab_label(self.word(name), "cap")
            self.rows.addWidget(cap)
            for chat in chats:
                title = ("\U0001f4cc " if chat.pinned else "") + chat.title
                row = ItemButton(title, when_text(chat.updated, moment, self.fa))
                row.setObjectName("AiHistoryRow")
                row.set_current(chat.chat_id == self.current)
                row.clicked.connect(lambda _c=False, value=chat.chat_id: self.open_chat.emit(value))
                row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                row.customContextMenuRequested.connect(
                    lambda point, value=chat.chat_id, widget=row: self._menu(value, widget, point),
                )
                self.rows.addWidget(row)
                self.buttons[chat.chat_id] = row

    def set_current(self, chat_id: str) -> None:
        self.current = chat_id
        for key, row in self.buttons.items():
            row.set_current(key == chat_id)

    def _menu(self, chat_id: str, widget: QWidget, point: QPoint) -> None:
        chat = self.store.load(chat_id) if self.store is not None else None
        if chat is None:
            return
        menu = QMenu(self)
        word = self.word
        menu.addAction(word("Unpin") if chat.pinned else word("Pin"), lambda: self.pin(chat_id))
        menu.addAction(word("Rename"), lambda: self.rename(chat_id))
        menu.addAction(word("Delete"), lambda: self.delete(chat_id))
        menu.popup(widget.mapToGlobal(point))

    def pin(self, chat_id: str) -> bool:
        chat = self.store.load(chat_id) if self.store is not None else None
        if chat is None or self.store is None:
            return False
        self.store.set_pinned(chat_id, not chat.pinned)
        self.refresh()
        return True

    def _rename_text(self, title: str) -> str | None:
        text, ok = QInputDialog.getText(self, "AI Lab", self.word("New title"), text=title)
        return text if ok else None

    def rename(self, chat_id: str) -> bool:
        chat = self.store.load(chat_id) if self.store is not None else None
        if chat is None or self.store is None:
            return False
        title = self.rename_text(chat.title)
        if title is None or self.store.rename(chat_id, title) is None:
            return False
        self.refresh()
        self.changed.emit()
        return True

    def delete(self, chat_id: str) -> bool:
        if self.store is None or not self.confirm(self.word("Delete this chat?")):
            return False
        done = self.store.delete(chat_id)
        self.refresh()
        self.changed.emit()
        return done


class PromptsPanel(QWidget):
    use_prompt = Signal(str)
    save_current = Signal()

    def __init__(self, store: PromptStore | None, fa: bool = False) -> None:
        super().__init__()
        self.store = store
        self.fa = fa
        word = inspector_words(fa)
        self.word = word
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = panel(word("Saved prompts"))
        outer.addWidget(body)
        note = "Saved prompts. A click puts one in the message box."
        layout.addWidget(lab_label(word(note), "note", wrap=True))
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        layout.addWidget(holder)
        self.save_button = lab_button(word("Save the current prompt"), "ghost")
        self.save_button.setObjectName("AiPromptSave")
        self.save_button.clicked.connect(self.save_current.emit)
        layout.addSpacing(14)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        self.shown: list[Prompt] = []

    def refresh(self) -> None:
        clear(self.rows)
        store = self.store
        self.shown = store.prompts(self.fa) if store is not None else []
        for prompt in self.shown:
            row = ItemButton(prompt.title, prompt.note)
            row.setObjectName("AiPromptRow")
            row.clicked.connect(lambda _c=False, value=prompt.text: self.use_prompt.emit(value))
            if not prompt.builtin:
                row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                row.customContextMenuRequested.connect(
                    lambda point, value=prompt.prompt_id, widget=row: self._menu(
                        value,
                        widget,
                        point,
                    ),
                )
            self.rows.addWidget(row)

    def _menu(self, prompt_id: str, widget: QWidget, point: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(self.word("Delete"), lambda: self.delete(prompt_id))
        menu.popup(widget.mapToGlobal(point))

    def delete(self, prompt_id: str) -> bool:
        if self.store is None or not self.store.delete(prompt_id):
            return False
        self.refresh()
        return True


class FilesPanel(QWidget):
    export = Signal()
    copy_prompt = Signal()

    def __init__(self, export_dir: Path | None, fa: bool = False) -> None:
        super().__init__()
        self.export_dir = export_dir
        self.fa = fa
        word = inspector_words(fa)
        self.word = word
        self.opener: Callable[[Path], bool] = self._open
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = panel(word("Files and exports"))
        outer.addWidget(body)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.export_button = lab_button(word("Export for AI"), "primary")
        self.export_button.setObjectName("AiFilesExport")
        self.export_button.clicked.connect(self.export.emit)
        self.copy_button = lab_button(word("Copy prompt"), "ghost")
        self.copy_button.setObjectName("AiFilesCopy")
        self.copy_button.clicked.connect(self.copy_prompt.emit)
        buttons.addWidget(self.export_button, 1)
        buttons.addWidget(self.copy_button)
        layout.addLayout(buttons)
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        layout.addWidget(holder)
        self.folder_button = lab_button(word("Open the folder"), "link")
        self.folder_button.clicked.connect(self.open_folder)
        layout.addWidget(self.folder_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.files: list[LabFile] = []
        if export_dir is None:
            for button in (self.export_button, self.copy_button, self.folder_button):
                button.setEnabled(False)

    def refresh(self, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        clear(self.rows)
        self.files = lab_files(self.export_dir) if self.export_dir is not None else []
        if not self.files:
            text = self.word("No export yet: press Export for AI.")
            self.rows.addWidget(lab_label(text, "note", wrap=True))
            return
        for item in self.files:
            meta = f"{size_text(item.size)} \u00b7 {when_text(item.modified, moment, self.fa)}"
            row = ItemButton(item.name, meta, mono=True)
            row.setObjectName("AiFileRow")
            row.setToolTip(str(item.path))
            row.clicked.connect(lambda _c=False, path=item.path: self.opener(path))
            self.rows.addWidget(row)

    def _open(self, path: Path) -> bool:
        if self.export_dir is None or not inside_exports(self.export_dir, path):
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_folder(self) -> bool:
        if self.export_dir is None:
            return False
        folder = self.export_dir / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _metric(values: Mapping[str, float | None], key: str, digits: int, unit: str) -> str:
    value = values.get(key)
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}{unit}"


class ExperimentsPanel(QWidget):
    def __init__(self, store: ExperimentStore | None, fa: bool = False) -> None:
        super().__init__()
        self.store = store
        self.fa = fa
        word = inspector_words(fa)
        self.word = word
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = panel(word("Experiments"))
        outer.addWidget(body)
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        layout.addWidget(holder)
        head = QHBoxLayout()
        head.addWidget(lab_label(word("Equity curve"), "card_title"), 0, MIDDLE)
        self.period_tag = Tag(word("same period"), "neutral", mono=not fa)
        head.addWidget(self.period_tag, 0, MIDDLE)
        head.addStretch(1)
        layout.addSpacing(12)
        layout.addLayout(head)
        self.chart = LineChart(130, word("Equity curve"))
        self.chart.setObjectName("AiExperimentChart")
        layout.addWidget(self.chart)
        self.detail = lab_label("", "note", wrap=True)
        self.detail.setLayoutDirection(LTR)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        self.items: list[Experiment] = []
        self.selected = 0
        self.buttons: dict[int, ItemButton] = {}

    def refresh(self) -> None:
        clear(self.rows)
        self.buttons = {}
        self.items = self.store.experiments() if self.store is not None else []
        if not self.items:
            text = self.word("No experiment yet: paste an AI answer to check it.")
            self.rows.addWidget(lab_label(text, "note", wrap=True))
            self.show_experiment(None)
            return
        for item in self.items:
            row = ItemButton(
                item.title,
                ", ".join(item.strategies),
                item.status,
                tag=True,
                start=f"#{item.number}",
            )
            row.setObjectName("AiExperimentRow")
            row.clicked.connect(lambda _c=False, value=item.number: self.select(value))
            self.rows.addWidget(row)
            self.buttons[item.number] = row
        wanted = self.selected if self.selected in self.buttons else self.items[0].number
        self.select(wanted)

    def select(self, number: int) -> None:
        self.selected = number
        for key, row in self.buttons.items():
            row.set_current(key == number)
        found = next((item for item in self.items if item.number == number), None)
        self.show_experiment(found)

    def show_experiment(self, item: Experiment | None) -> None:
        word = self.word
        if item is None or not item.current_curve:
            self.chart.set_lines([])
            self.detail.setText(word("Not compared yet.") if item is not None else "")
            return
        self.chart.set_lines(
            [
                Line(item.current_curve, word("Current"), "ink"),
                Line(item.proposed_curve, word("Suggested"), "muted", dashed=True),
            ],
        )
        now, new = item.current, item.proposed
        lines = [
            f"{item.symbol} {item.period}",
            f"trades {_metric(now, 'trades', 0, '')} -> {_metric(new, 'trades', 0, '')}",
            f"R {_metric(now, 'expectancy_r', 3, '')} -> {_metric(new, 'expectancy_r', 3, '')}",
            f"max DD {_metric(now, 'max_drawdown_percent', 2, '%')} -> "
            f"{_metric(new, 'max_drawdown_percent', 2, '%')}",
        ]
        self.detail.setText("\n".join(lines))


class UsagePanel(QWidget):
    def __init__(
        self,
        store: UsageStore | None,
        cap: Callable[[], float],
        fa: bool = False,
    ) -> None:
        super().__init__()
        self.store = store
        self.cap = cap
        self.fa = fa
        word = inspector_words(fa)
        self.word = word
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = panel(word("Usage and cost"))
        outer.addWidget(body)
        boxes = QHBoxLayout()
        boxes.setSpacing(10)
        self.input_value = mono_label("0")
        self.output_value = mono_label("0")
        for caption, value in (("IN", self.input_value), ("OUT", self.output_value)):
            frame = QFrame()
            frame.setProperty("lab", "card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(14, 12, 14, 12)
            cap_label = lab_label(caption, "cap")
            cap_label.setLayoutDirection(LTR)
            value.setProperty("size", "big")
            box.addWidget(cap_label)
            box.addWidget(value)
            box.addWidget(lab_label(word("tokens this month"), "note"))
            boxes.addWidget(frame, 1)
        layout.addLayout(boxes)
        self.month_cost = self._row(layout, word("Estimated cost this month"))
        self.today = self._row(layout, word("Today against the daily cap"))
        self.meter = Meter()
        self.meter.setObjectName("AiUsageMeter")
        layout.addWidget(self.meter)
        cap_text = lab_label(word("Requests in the last 7 days"), "cap")
        layout.addWidget(cap_text)
        self.days = BarChart(90, word("Requests in the last 7 days"))
        self.days.setObjectName("AiUsageDays")
        layout.addWidget(self.days)
        self.model = self._row(layout, word("Model"))
        self.requests = self._row(layout, word("Requests this month"))
        note = "Every click is one request. Nothing is sent by itself."
        layout.addWidget(lab_label(word(note), "note", wrap=True))
        layout.addStretch(1)

    @staticmethod
    def _row(layout: QVBoxLayout, name: str) -> QLabel:
        row = QHBoxLayout()
        row.addWidget(lab_label(name, "muted"))
        row.addStretch(1)
        value = mono_label("")
        row.addWidget(value)
        layout.addLayout(row)
        return value

    def refresh(self, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        if self.store is None:
            summary = None
        else:
            summary = self.store.summary(moment, self.fa)
        cap = self.cap()
        if summary is None:
            for label in (self.input_value, self.output_value):
                label.setText("0")
            self.month_cost.setText("$0.0000")
            self.today.setText(f"$0.0000 / ${cap:.2f}")
            self.days.set_bars([])
            self.meter.set_share(0.0)
            self.model.setText("")
            self.requests.setText("0")
            return
        self.input_value.setText(short_tokens(summary.month_input))
        self.output_value.setText(short_tokens(summary.month_output))
        cost = f"${summary.month_cost:.4f}"
        if summary.unknown_cost:
            unknown = self.word("{count} without a price").format(count=summary.unknown_cost)
            cost += f" (+{unknown})"
        self.month_cost.setText(cost)
        self.today.setText(f"${summary.today_cost:.4f} / ${cap:.2f}")
        share = min(summary.today_cost / cap, 1.0) if cap > 0 else 0.0
        self.meter.set_share(share)
        labels = [name for name, _ in summary.days]
        counts = [float(count) for _, count in summary.days]
        self.days.set_bars(counts, labels, signed=False)
        self.model.setText(summary.model or "-")
        self.requests.setText(str(summary.month_requests))
