"""The AI Lab workspace, laid out like the owner's UI v2 design (AiLab.jsx, docs/UI_V2.md 19e).

Three columns. On the start side the **history**: the page's place ("ANALYZE / 09"), the
gear for the AI settings, "New chat", search, All / Pinned / With proposals and the saved
chats in date groups, each with pin, rename and delete. In the middle the **chat**: its
title, Chat / Manual, Export (Markdown) and the side panel switch; an empty chat offers six
starting questions; every turn shows the question, the analyst's thinking line that opens on
a click (the plan, the thinking and every tool call with its result), the Markdown answer, a
row of actions (copy, again, branch from here, add to memory, good, bad) and the follow-ups.
The composer takes @ for a symbol, strategy or the risk settings and / for a command, has a
Plan mode (the AI first writes its plan and waits for "Run the plan"), the model, the money
spent today and Send / Stop. On the end side the **inspector**: the proposals of all chats,
the context the agent sees, the memory notes it gets in every chat and the scheduled reviews.

Everything is real: the chats and notes are files in the data folder (`app.ai.chat_store`),
the agent only reads (`app.ai.agent`), a proposal is the JSON change an answer ends with and
opens in Manual, where it is checked, backtested and activated only in Paper. Nothing here
changes a setting or trades. With Persian chosen the workspace is Persian, right to left.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai.agent import Limits, Step, Tool, Turn, run_turn
from app.ai.chat_store import (
    MAX_NOTES,
    Chat,
    ChatStore,
    grouped,
    load_auto,
    load_notes,
    save_auto,
    save_notes,
    start_chat,
)
from app.ai.cost import Usage
from app.ai.transport import AiCallError, AiClient, Completion, Message, plain_text
from app.strategies.registry import STRATEGIES
from app.ui.shell import Segmented, persian_digits
from app.ui.theme import DARK, ThemeTokens, number_family
from app.ui.v2 import Meter, Tag, hairline, v2_button, v2_label

Job = Callable[[], None]
ClientFactory = Callable[[], AiClient | str]
Proposal = tuple[str, str, str, str]  # strategy, parameter, now, suggested
Proposals = Callable[[str], Sequence[Proposal]]
RAIL_WIDTH = 268
INSPECTOR_WIDTH = 300
CHAT_WIDTH = 820
DAY = 86_400
SPIN = "\u25d0\u25d3\u25d1\u25d2"
SHOW_STEPS = "\u25be"
HIDE_STEPS = "\u25b4"
MODES = ("chat", "manual")
FILTERS = ("all", "pin", "prop")
TABS = ("props", "ctx", "mem", "auto")
AUTO_JOBS = ("daily", "streak", "weekly")
AUTO_HOUR = 8
LOSING_STREAK = 3

EXAMPLES: tuple[tuple[str, str], ...] = (
    ("Why did my last losing trade lose?", "trade, level, the 30 trades before"),
    ("Best hours to trade", "hour by day of the week"),
    ("Which filter rejects the most signals?", "the signal funnel"),
    ("App health and yesterday's errors", "checks and logs"),
    ("Compare the strategies", "expected R and sample size"),
    ("Full review of the app", "9 fixed checks"),
)
COMMANDS: tuple[tuple[str, str], ...] = (
    ("/review", "Full review of the app"),
    ("/compare", "Compare the strategies"),
    ("/hours", "Best hours to trade"),
    ("/filters", "Which filter rejects the most signals?"),
    ("/health", "App health and yesterday's errors"),
)
FOLLOW_UPS = (
    "Show the same for the last 90 days",
    "Only the losing trades",
    "Split it by strategy",
)
AUTO_QUESTIONS = {
    "daily": "Full review of the app",
    "streak": "Explain my last three losing trades and what they have in common.",
    "weekly": "Weekly report: compare the strategies over the last 7 days.",
}
PLAN_NOTE = (
    "Plan mode: reply only with a short numbered plan of the steps and tools you would use "
    "to answer. Do not call any tool yet."
)
RUN_PLAN = "Run the plan above."
PROPOSAL_NOTE = (
    "When you suggest changing a strategy parameter, end the answer with a fenced json block "
    'like {"changes": [{"strategy": "<name>", "params": {"<param>": <value>}, "reason": '
    '"<why>", "expected_impact": "<what changes>"}]} so the app can check and backtest it.'
)
WELCOME = (
    "I read the trades, signals, settings and logs myself and answer with tables. I only "
    "suggest changes; applying them is up to you."
)

FA: dict[str, str] = {
    "AI Lab": "آزمایشگاه AI",
    "New chat": "گفتگوی تازه",
    "Search chats": "جستجو در گفتگوها",
    "All": "همه",
    "Pinned": "پین",
    "With proposals": "با پیشنهاد",
    "Today": "امروز",
    "Yesterday": "دیروز",
    "This week": "این هفته",
    "Older": "قدیمی‌تر",
    "Pinned chats": "پین‌شده",
    "No proposals": "بدون پیشنهاد",
    "{count} proposals": "{count} پیشنهاد",
    "No chat found.": "گفتگویی پیدا نشد.",
    "No saved chats yet.": "هنوز گفتگویی ذخیره نشده.",
    "Chats stay on this computer and are not synced.": (
        "گفتگوها روی همین کامپیوتر می‌مانند و همگام نمی‌شوند."
    ),
    "Chat": "گفتگو",
    "Manual": "دستی",
    "Manual mode": "حالت دستی",
    "Export": "خروجی",
    "What should we look into?": "چه چیزی را بررسی کنیم؟",
    WELCOME: (
        "معاملات، سیگنال‌ها، تنظیمات و لاگ‌ها را خودم می‌خوانم و با جدول جواب می‌دهم. "
        "تغییرها را فقط پیشنهاد می‌دهم؛ اعمالش با شماست."
    ),
    "Why did my last losing trade lose?": "چرا آخرین معامله‌ی بازنده ضرر داد؟",
    "trade, level, the 30 trades before": "معامله، سطح، ۳۰ معامله‌ی قبل",
    "Best hours to trade": "بهترین ساعت‌های معامله",
    "hour by day of the week": "ساعت × روز هفته",
    "Which filter rejects the most signals?": "کدام فیلتر بیشتر سیگنال رد می‌کند؟",
    "the signal funnel": "قیف سیگنال‌ها",
    "App health and yesterday's errors": "سلامت برنامه و خطاهای دیروز",
    "checks and logs": "بررسی‌ها و لاگ",
    "Compare the strategies": "مقایسه‌ی استراتژی‌ها",
    "expected R and sample size": "انتظار R و نمونه",
    "Full review of the app": "بررسی کامل برنامه",
    "9 fixed checks": "۹ بررسی ثابت",
    "Show the same for the last 90 days": "همین را برای ۹۰ روز اخیر نشان بده",
    "Only the losing trades": "فقط معاملات بازنده",
    "Split it by strategy": "به تفکیک استراتژی",
    "Analyst": "تحلیل‌گر",
    "Thinking": "در حال فکر کردن",
    "Running the tools": "در حال اجرای ابزارها",
    "Esc to stop": "Esc برای توقف",
    "Stopped. The tools that ran are kept.": "متوقف شد. ابزارهای اجراشده نگه داشته شدند.",
    "Run the plan": "اجرای برنامه",
    "Edit": "ویرایش",
    "Copy": "کپی",
    "Again": "دوباره",
    "New chat from here": "شاخه‌ی تازه از این‌جا",
    "Add to memory": "افزودن به حافظه",
    "Good": "خوب بود",
    "Bad": "بد بود",
    "Ask; @ adds a symbol or strategy, / for commands": (
        "بپرس، @ برای افزودن نماد یا استراتژی، / برای دستورها"
    ),
    "Plan mode": "حالت برنامه",
    "Stop": "توقف",
    "Send": "ارسال",
    "Enter": "Enter",
    "send": "ارسال",
    "new line": "خط تازه",
    "stop": "توقف",
    "The agent only reads and suggests.": "عامل فقط می‌خواند و پیشنهاد می‌دهد.",
    "Proposals": "پیشنهادها",
    "Context": "زمینه",
    "Memory": "حافظه",
    "Automatic": "خودکار",
    "Every proposal of every chat in one place.": "همه‌ی پیشنهادهای گفتگوها در یک جا.",
    "No proposals yet. An answer that suggests a change lists it here.": (
        "هنوز پیشنهادی نیست. هر پاسخی که تغییری پیشنهاد کند این‌جا می‌آید."
    ),
    "Waiting": "منتظر",
    "Applied": "اعمال شد",
    "Test in Manual": "آزمون در حالت دستی",
    "The agent sees only this. Every tool only reads.": (
        "عامل فقط این‌ها را می‌بیند. همه‌ی ابزارها فقط خواندنی‌اند."
    ),
    "Period": "بازه",
    "30 days": "۳۰ روز",
    "Strategies": "استراتژی‌ها",
    "Mode": "حالت",
    "Trade rows": "ردیف معاملات",
    "Logs": "لاگ‌ها",
    "Account number": "شماره‌ی حساب",
    "Today's budget": "بودجه‌ی امروز",
    "Notes given to the agent in every chat ({count} of 10).": (
        "یادداشت‌هایی که در هر گفتگو به عامل داده می‌شود ({count} از ۱۰)."
    ),
    "+ New note and Enter": "+ یادداشت تازه و Enter",
    "The agent starts a chat by itself and saves it here. Nothing is applied automatically.": (
        "عامل خودش گفتگو می‌سازد و این‌جا ذخیره می‌کند. هیچ تغییری خودکار اعمال نمی‌شود."
    ),
    "Full review every day at 08:00": "بررسی کامل هر روز ساعت ۰۸:۰۰",
    "Analysis after 3 losses in a row": "تحلیل بعد از ۳ باخت پیاپی",
    "Weekly strategy report, Monday": "گزارش هفتگی استراتژی‌ها، دوشنبه",
    "Last run: {when}": "آخرین اجرا: {when}",
    "never": "هرگز",
    "Runs only while the app is open.": "فقط وقتی برنامه باز است اجرا می‌شود.",
    "New chat: the AI forgets the earlier questions.": (
        "گفتگوی تازه: AI سؤال‌های قبلی را فراموش می‌کند."
    ),
    "Copied.": "کپی شد.",
    "Thanks.": "ممنون.",
    "Noted.": "ثبت شد.",
    "Added to memory.": "به حافظه اضافه شد.",
    "Memory is full (10 notes).": "حافظه پر است (۱۰ یادداشت).",
    "Saved as {name}.": "ذخیره شد: {name}",
    "Nothing to export yet.": "هنوز چیزی برای خروجی نیست.",
    "An automatic review was saved.": "یک بررسی خودکار ذخیره شد.",
    "Delete chat": "حذف گفتگو",
    'Delete "{title}"? This cannot be undone.': "«{title}» حذف شود؟ برنمی‌گردد.",
    "Rename": "تغییر نام",
    "Pin": "پین",
    "Unpin": "برداشتن پین",
    "Delete": "حذف",
    "AI settings": "تنظیمات AI",
    "Side panel": "پنل کناری",
    "the risk settings": "تنظیمات ریسک",
    "strategy": "استراتژی",
    "symbol": "نماد",
}


def _start_thread(job: Job) -> None:
    threading.Thread(target=job, name="ai-lab-agent", daemon=True).start()


def _no_tools() -> Sequence[Tool]:
    return ()


def _app_language() -> str:
    return ""


def _no_proposals(_text: str) -> Sequence[Proposal]:
    return ()


def _no_symbols() -> Sequence[str]:
    return ()


def _no_cap() -> float:
    return 1.0


def _no_mode() -> str:
    return ""


def _no_losses() -> int:
    return 0


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


def thinking_line_fa(turn: Turn) -> str:
    seconds = sum(step.seconds for step in turn.steps)
    tokens = (turn.usage.input_tokens + turn.usage.output_tokens) / 1000
    head = f"{seconds:.1f} ثانیه کار کرد" if seconds >= 0.05 else "پاسخ داد"
    return f"{head} \u00b7 {persian_digits(str(turn.tool_steps))} ابزار \u00b7 {tokens:.1f}k tokens"


def meta_line(turn: Turn) -> str:
    """The quiet line under an answer: "2 tools · 6,100 tokens · $0.004"."""
    usage = turn.usage
    tokens = usage.input_tokens + usage.output_tokens
    cost = f"${turn.cost:.3f}" if turn.cost > 0 else "cost unknown"
    tools = "1 tool" if turn.tool_steps == 1 else f"{turn.tool_steps} tools"
    return f"{tools} \u00b7 {tokens:,} tokens \u00b7 {cost}"


def spent_today(chats: Sequence[Chat], now: float) -> float:
    """The money of the chats changed today (local day), the budget bar and the composer."""
    today = datetime.fromtimestamp(now).date()
    return sum(c.cost for c in chats if datetime.fromtimestamp(c.updated).date() == today)


def chat_markdown(chat: Chat) -> str:
    """A chat as Markdown, for Export."""
    lines = [f"# {chat.title}", ""]
    for turn in chat.turns:
        lines += [f"## {turn.question}", "", turn.answer or f"_{turn.stopped}_", ""]
        lines += [f"<sub>{turn.cost_line()}</sub>", ""]
    return "\n".join(lines)


def notes_turn(notes: Sequence[str]) -> Turn | None:
    """The memory notes and the proposal format as one earlier exchange of the chat."""
    text = PROPOSAL_NOTE
    if notes:
        text = "Notes about me and this app:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" + text
    return Turn(text, "Understood.", (), Usage(), 0.0, 0, "")


def due_jobs(
    auto: Mapping[str, object],
    now: float,
    losses_in_a_row: int,
) -> list[str]:
    """The scheduled reviews to run now: daily after 08:00, Monday's weekly report, and the
    loss review once per streak of three."""
    moment = datetime.fromtimestamp(now)
    today = moment.date().isoformat()
    found: list[str] = []
    after_eight = moment.hour >= AUTO_HOUR
    if auto.get("daily") is True and after_eight and auto.get("daily_ran") != today:
        found.append("daily")
    monday = moment.weekday() == 0
    if auto.get("weekly") is True and monday and after_eight and auto.get("weekly_ran") != today:
        found.append("weekly")
    streak = losses_in_a_row >= LOSING_STREAK
    if auto.get("streak") is True and streak and auto.get("streak_seen") != losses_in_a_row:
        found.append("streak")
    return found


def lab_qss(tokens: ThemeTokens, rtl: bool = False) -> str:
    """The workspace's own stylesheet (the roles of AiLab.jsx), set on the panel."""
    t = tokens
    start, end = ("right", "left") if rtl else ("left", "right")
    mono = number_family()
    return f"""
QFrame[v2="rail"] {{
    background-color: {t.surface};
    border: none;
    border-{end}: 1px solid {t.border};
}}
QFrame[v2="inspector"] {{
    background-color: {t.bg};
    border: none;
    border-{start}: 1px solid {t.border};
}}
QFrame[v2="chat_head"] {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {t.border};
}}
QFrame[v2="hist"] {{
    background-color: transparent;
    border: none;
    border-radius: 5px;
}}
QFrame[v2="hist"]:hover {{
    background-color: {t.hover};
}}
QFrame[v2="hist"][current="true"] {{
    background-color: {t.hover};
    border-{start}: 2px solid {t.text};
}}
QFrame[v2="steps"] {{
    background-color: transparent;
    border: none;
    border-{start}: 1px solid {t.border_strong};
}}
QFrame[v2="tool"] {{
    background-color: transparent;
    border: 1px solid {t.border};
    border-radius: 5px;
}}
QFrame[v2="popup"] {{
    background-color: {t.card};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
}}
QFrame[v2="composer"] {{
    background-color: {t.card};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
}}
QPlainTextEdit[v2="composer_input"] {{
    background-color: transparent;
    border: none;
    padding: 2px;
    font-size: 11pt;
}}
QWidget[v2="irow"] {{
    border: none;
    border-top: 1px solid {t.border};
}}
QLabel[v2="h_rail"] {{
    font-size: 13.5pt;
    font-weight: 600;
}}
QLabel[v2="h_welcome"] {{
    font-size: 22pt;
    font-weight: 600;
}}
QLabel[v2="chat_title"] {{
    font-size: 11.25pt;
    font-weight: 600;
}}
QLabel[v2="bubble"] {{
    background-color: {t.hover};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 9px 14px;
    font-size: 11pt;
}}
QLabel[v2="answer"] {{
    font-size: 11.25pt;
}}
QLabel[v2="thinking"] {{
    color: {t.text_secondary};
    font-size: 9.75pt;
    font-style: italic;
}}
QLabel[v2="warn"] {{
    color: {t.warning};
    font-size: 9.75pt;
}}
QLabel[v2="who"] {{
    font-size: 10pt;
    font-weight: 600;
}}
QLabel[v2="mark"] {{
    border: 1px solid {t.text};
    border-radius: 4px;
    font-size: 9pt;
}}
QLabel[v2="kbd"] {{
    color: {t.text_secondary};
    border: 1px solid {t.border_strong};
    border-radius: 4px;
    padding: 0px 5px;
    font-family: {mono};
    font-size: 8.25pt;
}}
QLabel[v2="mono"] {{
    font-family: {mono};
    font-size: 9pt;
}}
QLabel[v2="mono_muted"] {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: 9pt;
}}
QPushButton[v2="primary"] {{
    background-color: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    border-radius: 6px;
    padding: 0px 12px;
    min-height: 30px;
    font-size: 9.75pt;
    font-weight: 600;
}}
QPushButton[v2="primary"]:disabled {{
    background-color: {t.border_strong};
    border-color: {t.border_strong};
}}
QPushButton[v2="danger"] {{
    background-color: {t.loss_soft};
    color: {t.loss};
    border: 1px solid {t.loss};
    border-radius: 6px;
    padding: 0px 12px;
    min-height: 30px;
    font-size: 9.75pt;
}}
QPushButton[v2="ghost"]:checked {{
    background-color: {t.accent};
    color: {t.accent_text};
}}
QPushButton[v2="icon"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 0px;
    font-size: 10.5pt;
}}
QPushButton[v2="icon"]:hover, QPushButton[v2="icon"]:checked {{
    color: {t.text};
    background-color: {t.hover};
}}
QPushButton[v2="box"] {{
    background-color: transparent;
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    padding: 10px 14px;
    text-align: {start};
    font-size: 10pt;
}}
QPushButton[v2="box"]:hover {{
    background-color: {t.hover};
}}
QPushButton[v2="follow"], QPushButton[v2="pal"], QPushButton[v2="link"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-radius: 5px;
    padding: 0px 8px;
    min-height: 32px;
    text-align: {start};
    font-size: 9.75pt;
}}
QPushButton[v2="follow"]:hover, QPushButton[v2="pal"]:hover, QPushButton[v2="link"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[v2="chip"] {{
    background-color: {t.hover};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 4px;
    padding: 0px 6px;
    min-height: 20px;
    font-family: {mono};
    font-size: 8.25pt;
}}
QPushButton[v2="tab"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 0px 8px;
    min-height: 32px;
    font-size: 9.75pt;
}}
QPushButton[v2="tab"]:checked {{
    color: {t.text};
    border-bottom-color: {t.text};
}}
"""


class Switch(QAbstractButton):
    """The design's toggle: a small rounded track with a knob."""

    def __init__(self, on: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tokens: ThemeTokens = DARK
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return QSize(34, 20)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        t = self.tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        on = self.isChecked()
        painter.setPen(QColor(t.accent if on else t.border_strong))
        painter.setBrush(QColor(t.accent) if on else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, 33, 19), 9.5, 9.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.accent_text if on else t.text_secondary))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        left = (17.0 if on else 3.0) if not rtl else (3.0 if on else 17.0)
        painter.drawEllipse(QRectF(left, 3, 14, 14))
        painter.end()


def _text(text: str, role: str, wrap: bool = False, *, selectable: bool = False) -> QLabel:
    label = v2_label(text, role, wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if persian(text):
        label.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    return label


def _icon(glyph: str, name: str) -> QPushButton:
    button = v2_button(glyph, "icon")
    button.setAccessibleName(name)
    button.setToolTip(name)
    button.setFixedSize(30, 30)
    return button


class ChatRow(QFrame):
    """One saved chat in the rail: its title, its proposals and pin, rename and delete."""

    opened = Signal(str)
    pinned = Signal(str)
    renamed = Signal(str, str)
    deleted = Signal(str)

    def __init__(self, chat: Chat, proposals: int, words: Callable[[str], str]) -> None:
        super().__init__()
        self.chat_id = chat.chat_id
        self.setProperty("v2", "hist")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 4, 6)
        layout.setSpacing(4)
        text = QVBoxLayout()
        text.setSpacing(4)
        mark = "\u2726 " if chat.auto else ""
        self.title = _text(mark + chat.title, "row")
        self.title.setToolTip(chat.title)
        self.title.setMinimumWidth(40)
        text.addWidget(self.title)
        if proposals:
            label = words("{count} proposals").format(count=proposals)
            self.sub: QWidget = Tag(label, "warning", mono=False)
        else:
            self.sub = _text(words("No proposals"), "muted")
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.sub)
        sub_row.addStretch(1)
        text.addLayout(sub_row)
        layout.addLayout(text, 1)
        self.editor = QLineEdit(chat.title)
        self.editor.setVisible(False)
        self.editor.editingFinished.connect(self._finish_rename)
        layout.addWidget(self.editor, 1)
        self.tools = QWidget()
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(0)
        pin_name = words("Unpin" if chat.pinned else "Pin")
        self.pin_button = _icon("\u25c6" if chat.pinned else "\u25c7", pin_name)
        self.rename_button = _icon("\u270e", words("Rename"))
        self.delete_button = _icon("\u2715", words("Delete"))
        for button in (self.pin_button, self.rename_button, self.delete_button):
            button.setFixedSize(24, 24)
            tools.addWidget(button)
        self.tools.setVisible(False)
        layout.addWidget(self.tools, 0, Qt.AlignmentFlag.AlignTop)
        self.pin_button.clicked.connect(lambda: self.pinned.emit(self.chat_id))
        self.rename_button.clicked.connect(self.start_rename)
        self.delete_button.clicked.connect(lambda: self.deleted.emit(self.chat_id))

    def set_current(self, current: bool) -> None:
        self.setProperty("current", current)
        self.style().unpolish(self)
        self.style().polish(self)

    def start_rename(self) -> None:
        self.title.setVisible(False)
        self.sub.setVisible(False)
        self.editor.setVisible(True)
        self.editor.setFocus()
        self.editor.selectAll()

    def _finish_rename(self) -> None:
        if not self.editor.isVisible():
            return
        self.editor.setVisible(False)
        self.title.setVisible(True)
        self.sub.setVisible(True)
        self.renamed.emit(self.chat_id, self.editor.text())

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt name)
        self.tools.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt name)
        self.tools.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt name)
        self.opened.emit(self.chat_id)
        super().mousePressEvent(event)


class HistoryRail(QFrame):
    """The saved chats: the page's place, settings, new chat, search, filters and groups."""

    def __init__(self, words: Callable[[str], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.words = words
        self.setObjectName("AiChatRail")
        self.setProperty("v2", "rail")
        self.setFixedWidth(RAIL_WIDTH)
        self.rows: list[ChatRow] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QVBoxLayout()
        top.setContentsMargins(14, 16, 14, 8)
        top.setSpacing(10)
        head = QHBoxLayout()
        self.crumb = v2_label("ANALYZE / 09", "cap")
        head.addWidget(self.crumb)
        head.addStretch(1)
        self.settings_button = _icon("\u2699", words("AI settings"))
        self.settings_button.setObjectName("AiLabSettings")
        head.addWidget(self.settings_button)
        top.addLayout(head)
        self.heading = v2_label(words("AI Lab"), "h_rail")
        top.addWidget(self.heading)
        self.new_button = v2_button("+  " + words("New chat"), "primary")
        self.new_button.setObjectName("AiChatNew")
        top.addWidget(self.new_button)
        self.search = QLineEdit()
        self.search.setObjectName("AiChatSearch")
        self.search.setPlaceholderText("\u2315  " + words("Search chats"))
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search)
        labels = [words("All"), words("Pinned"), words("With proposals")]
        self.filters = Segmented(labels)
        self.filters.choose(0)
        self.all_button, self.pinned_button, self.proposals_button = self.filters.buttons
        top.addWidget(self.filters)
        outer.addLayout(top)
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("AiChatHistory")
        self.list = QVBoxLayout(body)
        self.list.setContentsMargins(8, 0, 8, 10)
        self.list.setSpacing(0)
        self.list.addStretch(1)
        self.history_scroll.setWidget(body)
        outer.addWidget(self.history_scroll, 1)
        self.empty = _text(words("No saved chats yet."), "note", True)
        self.empty.setObjectName("AiChatHistoryEmpty")
        self.empty.setContentsMargins(14, 8, 14, 8)
        outer.addWidget(self.empty)
        outer.addWidget(hairline())
        self.footer = _text(words("Chats stay on this computer and are not synced."), "note", True)
        self.footer.setContentsMargins(14, 10, 14, 10)
        outer.addWidget(self.footer)

    @property
    def mode(self) -> str:
        return FILTERS[max(0, self.filters.group.checkedId())]

    @property
    def pinned_only(self) -> bool:
        return self.mode == "pin"

    def chat_ids(self) -> list[str]:
        """The chats shown, top to bottom."""
        return [row.chat_id for row in self.rows]

    def row(self, chat_id: str) -> ChatRow | None:
        return next((row for row in self.rows if row.chat_id == chat_id), None)

    def _keeps(self, chat: Chat, proposals: Callable[[Chat], int]) -> bool:
        """The search and the filter: All, Pinned or With proposals."""
        if not chat.matches(self.search.text()):
            return False
        mode = self.mode
        if mode == "pin":
            return chat.pinned
        return mode != "prop" or proposals(chat) > 0

    def show_chats(
        self,
        chats: Sequence[Chat],
        now: float,
        current: str,
        proposals: Callable[[Chat], int],
    ) -> list[ChatRow]:
        shown = [chat for chat in chats if self._keeps(chat, proposals)]
        while self.list.count() > 1:
            item = self.list.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.rows = []
        for name, members in grouped(shown, now):
            group = "Pinned chats" if name == "Pinned" else name
            header = _text(self.words(group), "muted")
            header.setContentsMargins(8, 12, 8, 4)
            self.list.insertWidget(self.list.count() - 1, header)
            for chat in members:
                row = ChatRow(chat, proposals(chat), self.words)
                row.set_current(chat.chat_id == current)
                self.list.insertWidget(self.list.count() - 1, row)
                self.rows.append(row)
        self.empty.setVisible(not shown)
        self.empty.setText(self.words("No chat found." if chats else "No saved chats yet."))
        return self.rows


class TurnView(QFrame):
    """One question: the bubble, the analyst's thinking line with the plan, thinking and
    tool calls inside, the Markdown answer, the actions and (last turn) the follow-ups."""

    def __init__(
        self,
        question: str,
        words: Callable[[str], str],
        fa: bool,
        model: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.words = words
        self.fa = fa
        self.setObjectName("AiChatTurn")
        self.started = time.monotonic()
        self.turn: Turn | None = None
        self.lines: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bubble_row = QHBoxLayout()
        bubble_row.addStretch(1)
        self.question = _text(question, "bubble", True, selectable=True)
        self.question.setMaximumWidth(int(CHAT_WIDTH * 0.76))
        bubble_row.addWidget(self.question)
        layout.addLayout(bubble_row)
        layout.addSpacing(16)
        who = QHBoxLayout()
        who.setSpacing(8)
        mark = v2_label("\u2726", "mark")
        mark.setFixedSize(22, 22)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        who.addWidget(mark)
        who.addWidget(v2_label(words("Analyst"), "who"))
        self.model = v2_label(model, "cap")
        who.addWidget(self.model)
        who.addStretch(1)
        layout.addLayout(who)
        layout.addSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.toggle = v2_button(SHOW_STEPS, "ghost")
        self.toggle.setObjectName("AiChatStepsToggle")
        self.toggle.setToolTip("Show or hide what the AI looked up")
        self.toggle.clicked.connect(self.toggle_steps)
        self.toggle.hide()
        self.head = v2_label(f"{SPIN[0]}  {words('Thinking')}", "muted")
        self.head.setObjectName("AiChatThinking")
        head.addWidget(self.head)
        head.addWidget(self.toggle)
        head.addStretch(1)
        self.esc = v2_label(words("Esc to stop"), "kbd")
        head.addWidget(self.esc)
        layout.addLayout(head)
        self.details = QFrame()
        self.details.setProperty("v2", "steps")
        details = QVBoxLayout(self.details)
        details.setContentsMargins(14, 10, 14, 4)
        details.setSpacing(8)
        self.thinking_cap = v2_label("THINKING", "cap")
        details.addWidget(self.thinking_cap)
        self.thinking = _text("", "thinking", True, selectable=True)
        details.addWidget(self.thinking)
        self.tools_box = QVBoxLayout()
        self.tools_box.setSpacing(6)
        details.addLayout(self.tools_box)
        self.steps = self.details
        layout.addWidget(self.details)
        self.answer = _text("", "answer", True, selectable=True)
        self.answer.setObjectName("AiChatAnswer")
        self.answer.setTextFormat(Qt.TextFormat.MarkdownText)
        self.answer.setOpenExternalLinks(False)
        self.answer.hide()
        layout.addSpacing(14)
        layout.addWidget(self.answer)
        self.stopped = _text(words("Stopped. The tools that ran are kept."), "warn", True)
        self.stopped.hide()
        layout.addWidget(self.stopped)
        self.plan_row = QWidget()
        plan = QHBoxLayout(self.plan_row)
        plan.setContentsMargins(0, 8, 0, 0)
        self.run_plan_button = v2_button(words("Run the plan"), "primary")
        self.edit_plan_button = v2_button(words("Edit"), "outline")
        plan.addWidget(self.run_plan_button)
        plan.addWidget(self.edit_plan_button)
        plan.addStretch(1)
        self.plan_row.hide()
        layout.addWidget(self.plan_row)
        self.action_row = QWidget()
        actions = QHBoxLayout(self.action_row)
        actions.setContentsMargins(0, 14, 0, 0)
        actions.setSpacing(2)
        self.copy_button = _icon("\u29c9", words("Copy"))
        self.again_button = _icon("\u21bb", words("Again"))
        self.branch_button = _icon("\u2387", words("New chat from here"))
        self.memory_button = _icon("\u2691", words("Add to memory"))
        self.good_button = _icon("\u25b2", words("Good"))
        self.bad_button = _icon("\u25bc", words("Bad"))
        for button in (
            self.copy_button,
            self.again_button,
            self.branch_button,
            self.memory_button,
            self.good_button,
            self.bad_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        self.cost = v2_label("", "cap")
        self.cost.setObjectName("AiChatCost")
        actions.addWidget(self.cost)
        self.action_row.hide()
        layout.addWidget(self.action_row)
        self.follow = QWidget()
        follow = QVBoxLayout(self.follow)
        follow.setContentsMargins(0, 14, 0, 0)
        follow.setSpacing(0)
        follow.addWidget(hairline())
        follow.addSpacing(8)
        follow.addWidget(v2_label("FOLLOW-UP", "cap"))
        self.follow_buttons: list[QPushButton] = []
        for text in FOLLOW_UPS:
            button = v2_button("\u21b3  " + words(text), "follow")
            button.setProperty("question", words(text))
            follow.addWidget(button)
            self.follow_buttons.append(button)
        self.follow.hide()
        layout.addWidget(self.follow)
        self.details.setVisible(False)

    @property
    def steps_open(self) -> bool:
        return not self.details.isHidden()

    def steps_text(self) -> str:
        return "\n".join(self.lines)

    def tick(self, frame: int) -> None:
        if self.turn is not None:
            return
        seconds = time.monotonic() - self.started
        tools = sum(1 for line in self.lines if not line.startswith("\u2026"))
        word = self.words("Running the tools" if tools else "Thinking")
        self.head.setText(f"{SPIN[frame % len(SPIN)]}  {word}   {seconds:.0f}s")

    def add_step(self, step: Step) -> None:
        self.lines.append(step.line())
        self.details.setVisible(True)
        if step.kind == "thinking":
            old = self.thinking.text()
            self.thinking.setText(f"{old}\n{step.title}".strip())
            return
        self._add_tool(step, sum(1 for line in self.lines if not line.startswith("\u2026")))

    def _add_tool(self, step: Step, number: int) -> None:
        card = QFrame()
        card.setProperty("v2", "tool")
        card.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 6, 10, 6)
        box.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        mark = "\u25cf" if step.ok else "\u2715"
        args = f"({step.detail})" if step.detail else ""
        top.addWidget(v2_label(f"[{number}]  {mark}  {step.title}  {args}", "mono"))
        top.addStretch(1)
        top.addWidget(v2_label(f"{step.seconds:.1f}s", "mono_muted"))
        box.addLayout(top)
        result = "ok" if step.ok else "failed"
        box.addWidget(v2_label(f"     \u23bf {result}", "mono_muted"))
        self.tools_box.addWidget(card)

    def toggle_steps(self) -> None:
        shown = not self.steps_open
        self.details.setVisible(shown)
        self.toggle.setText(HIDE_STEPS if shown else SHOW_STEPS)

    def finish(self, turn: Turn, *, plan: bool = False) -> None:
        self.turn = turn
        if not self.lines:
            for step in turn.steps:
                self.add_step(step)
        self.head.setText(thinking_line_fa(turn) if self.fa else thinking_line(turn))
        self.esc.hide()
        self.details.setVisible(False)
        self.thinking_cap.setVisible(bool(self.thinking.text()))
        self.toggle.setText(SHOW_STEPS)
        self.toggle.setVisible(bool(self.lines))
        if turn.model:
            self.model.setText(turn.model)
        if turn.answer:
            self.answer.setText(turn.answer)
            if persian(turn.answer):
                self.answer.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
                self.answer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            self.answer.show()
            self.action_row.show()
            self.plan_row.setVisible(plan)
        else:
            self.stopped.setText(
                self.words("Stopped. The tools that ran are kept.")
                if turn.stopped == "stopped by you"
                else turn.stopped.capitalize(),
            )
            self.stopped.show()
        self.cost.setText(meta_line(turn))
        self.cost.setToolTip(turn.cost_line())

    def fail(self, text: str) -> None:
        self.head.setText(f"\u2715  {text}")
        self.esc.hide()


class Composer(QFrame):
    """The input card: context tags, the text, @ and /, Plan mode, model, budget, Send."""

    submitted = Signal()

    def __init__(self, words: Callable[[str], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.words = words
        self.mentions: list[str] = []
        self.options: Callable[[], Sequence[tuple[str, str]]] = lambda: ()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self.popup = QFrame()
        self.popup.setProperty("v2", "popup")
        self.popup_list = QVBoxLayout(self.popup)
        self.popup_list.setContentsMargins(5, 5, 5, 5)
        self.popup_list.setSpacing(0)
        self.popup.hide()
        outer.addWidget(self.popup)
        self.card = QFrame()
        self.card.setProperty("v2", "composer")
        card = QVBoxLayout(self.card)
        card.setContentsMargins(12, 10, 12, 10)
        card.setSpacing(6)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.chips.addStretch(1)
        card.addLayout(self.chips)
        self.input = QPlainTextEdit()
        self.input.setObjectName("AiChatInput")
        self.input.setProperty("v2", "composer_input")
        self.input.setPlaceholderText(words("Ask; @ adds a symbol or strategy, / for commands"))
        self.input.setFixedHeight(58)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._typed)
        card.addWidget(self.input)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.at_button = _icon("@", "Add context")
        self.slash_button = _icon("/", "Commands")
        self.plan_button = v2_button("\u2630  " + words("Plan mode"), "ghost")
        self.plan_button.setObjectName("AiChatPlan")
        self.plan_button.setCheckable(True)
        self.plan_button.setToolTip("First show the plan and wait for my go")
        self.model = QComboBox()
        self.model.setObjectName("AiChatModel")
        self.model.setProperty("v2", "select")
        self.model.setToolTip("The model is set in the AI settings (the gear)")
        tools.addWidget(self.at_button)
        tools.addWidget(self.slash_button)
        tools.addWidget(self.plan_button)
        tools.addWidget(self.model)
        tools.addStretch(1)
        self.budget = v2_label("", "cap")
        self.budget.setObjectName("AiChatBudget")
        tools.addWidget(self.budget)
        self.send_button = v2_button("\u2191", "primary")
        self.send_button.setObjectName("AiChatSend")
        self.send_button.setAccessibleName(words("Send"))
        self.send_button.setFixedSize(32, 32)
        self.stop_button = v2_button("\u25a0  " + words("Stop"), "danger")
        self.stop_button.setObjectName("AiChatStop")
        tools.addWidget(self.send_button)
        tools.addWidget(self.stop_button)
        card.addLayout(tools)
        outer.addWidget(self.card)
        self.at_button.clicked.connect(lambda: self._insert("@"))
        self.slash_button.clicked.connect(lambda: self.input.setPlainText("/"))

    def text(self) -> str:
        return self.input.toPlainText()

    def question(self) -> str:
        """The text with the @ tags, as the agent reads it."""
        text = self.text().strip()
        if not self.mentions:
            return text
        return f"{text}\n\nAbout: {', '.join(self.mentions)}" if text else ", ".join(self.mentions)

    def clear(self) -> None:
        self.input.clear()
        self.mentions = []
        self._show_chips()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt name)
        enter = (
            watched is self.input
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        )
        if enter and isinstance(event, QKeyEvent):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                return False
            if not self._pick_first():
                self.submitted.emit()
            return True
        return super().eventFilter(watched, event)

    def _insert(self, text: str) -> None:
        current = self.text()
        joiner = " " if current and not current.endswith(" ") else ""
        self.input.setPlainText(current + joiner + text)
        self.input.setFocus()
        cursor = self.input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.input.setTextCursor(cursor)

    def suggestions(self) -> list[tuple[str, str, str]]:
        """(shown key, its kind, what picking it does) for the word being typed."""
        text = self.text()
        word = text.split()[-1] if text.split() and not text.endswith(" ") else ""
        if text.startswith("/") and " " not in text:
            return [(k, self.words(d), "command") for k, d in COMMANDS if k.startswith(text)]
        if word.startswith("@"):
            return [(k, d, "mention") for k, d in self.options() if k.startswith(word)]
        return []

    def _typed(self) -> None:
        while self.popup_list.count():
            item = self.popup_list.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        found = self.suggestions()[:8]
        for key, detail, kind in found:
            button = v2_button(f"{key}    {detail}", "pal")
            button.clicked.connect(lambda _c=False, k=key, k2=kind: self.pick(k, k2))
            self.popup_list.addWidget(button)
        self.popup.setVisible(bool(found))

    def _pick_first(self) -> bool:
        found = self.suggestions()
        if not found:
            return False
        key, _detail, kind = found[0]
        self.pick(key, kind)
        return True

    def pick(self, key: str, kind: str) -> None:
        if kind == "command":
            question = next((d for k, d in COMMANDS if k == key), "")
            self.input.setPlainText(self.words(question))
            self.popup.hide()
            self.submitted.emit()
            return
        text = self.text()
        self.input.setPlainText(text[: len(text) - len(text.split()[-1])])
        if key not in self.mentions:
            self.mentions.append(key)
        self._show_chips()

    def _show_chips(self) -> None:
        while self.chips.count() > 1:
            item = self.chips.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for key in self.mentions:
            chip = v2_button(f"{key}  \u2715", "chip")
            chip.setToolTip("Remove")
            chip.clicked.connect(lambda _c=False, k=key: self._remove(k))
            self.chips.insertWidget(self.chips.count() - 1, chip)

    def _remove(self, key: str) -> None:
        self.mentions = [k for k in self.mentions if k != key]
        self._show_chips()


class Inspector(QFrame):
    """The side panel: proposals, context, memory notes and scheduled reviews."""

    def __init__(self, words: Callable[[str], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.words = words
        self.setObjectName("AiInspector")
        self.setProperty("v2", "inspector")
        self.setFixedWidth(INSPECTOR_WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)
        names = [words("Proposals"), words("Context"), words("Memory"), words("Automatic")]
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tab_group = QButtonGroup(self)
        self.tab_buttons: list[QPushButton] = []
        for index, name in enumerate(names):
            button = v2_button(name, "tab")
            button.setCheckable(True)
            self.tab_group.addButton(button, index)
            tabs.addWidget(button)
            self.tab_buttons.append(button)
        tabs.addStretch(1)
        outer.addLayout(tabs)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.proposals = self._page(words("Every proposal of every chat in one place."))
        self.context = self._page(words("The agent sees only this. Every tool only reads."))
        self.memory = self._page("")
        self.auto = self._page(
            words(
                "The agent starts a chat by itself and saves it here. "
                "Nothing is applied automatically.",
            ),
        )
        self.tab_group.idClicked.connect(self.pages.setCurrentIndex)
        self.tab_buttons[0].setChecked(True)
        self.context_values: dict[str, QLabel] = {}
        for name in ("Period", "Strategies", "Mode"):
            value = v2_label("", "muted")
            self._row(self.context, words(name), value)
            self.context_values[name] = value
        self.context_values["Period"].setText(words("30 days"))
        self.context_values["Strategies"].setText(words("All"))
        self.switches: dict[str, Switch] = {}
        for name in ("Trade rows", "Logs", "Account number"):
            switch = Switch(name != "Account number")
            switch.setAccessibleName(words(name))
            self._row(self.context, words(name), switch)
            self.switches[name] = switch
        account = self.switches["Account number"]
        account.setEnabled(False)
        account.setToolTip("The account number is never given to the agent.")
        budget_row = QHBoxLayout()
        budget_row.addWidget(v2_label(words("Today's budget"), "row"))
        budget_row.addStretch(1)
        self.budget = v2_label("", "cap")
        budget_row.addWidget(self.budget)
        self.context.addSpacing(14)
        self.context.addLayout(budget_row)
        self.budget_bar = Meter()
        self.context.addWidget(self.budget_bar)
        self.limits_note = _text("", "note", True)
        self.context.addWidget(self.limits_note)
        self.notes_box = QVBoxLayout()
        self.notes_box.setSpacing(0)
        self.memory.addLayout(self.notes_box)
        self.note_input = QLineEdit()
        self.note_input.setObjectName("AiMemoryInput")
        self.note_input.setPlaceholderText(words("+ New note and Enter"))
        self.memory.addWidget(self.note_input)
        self.auto_switches: dict[str, Switch] = {}
        texts = {
            "daily": "Full review every day at 08:00",
            "streak": "Analysis after 3 losses in a row",
            "weekly": "Weekly strategy report, Monday",
        }
        for key in AUTO_JOBS:
            switch = Switch(False)
            switch.setAccessibleName(words(texts[key]))
            self._row(self.auto, words(texts[key]), switch, wrap=True)
            self.auto_switches[key] = switch
        self.auto_note = _text("", "note", True)
        self.auto.addWidget(self.auto_note)
        self.proposal_box = QVBoxLayout()
        self.proposal_box.setSpacing(0)
        self.proposals.addLayout(self.proposal_box)
        for page in (self.proposals, self.context, self.memory, self.auto):
            page.addStretch(1)

    def _page(self, note: str) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        label = _text(note, "note", True)
        label.setContentsMargins(0, 0, 0, 8)
        layout.addWidget(label)
        scroll.setWidget(body)
        self.pages.addWidget(scroll)
        return layout

    def _row(self, page: QVBoxLayout, name: str, value: QWidget, *, wrap: bool = False) -> None:
        row = QWidget()
        row.setProperty("v2", "irow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 8, 0, 8)
        line.addWidget(_text(name, "row", wrap), 1)
        line.addWidget(value)
        page.addWidget(row)


def _clear(layout: QVBoxLayout | QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()


class _Bridge(QObject):
    step = Signal(object)
    done = Signal(object)
    failed = Signal(str)
    auto_done = Signal(object)
    auto_failed = Signal(str)


class ChatPanel(QWidget):
    """The whole AI Lab workspace: history, chat (or Manual) and the inspector."""

    def __init__(
        self,
        client: ClientFactory,
        tools: Callable[[], Sequence[Tool]] = _no_tools,
        language: Callable[[], str] = _app_language,
        parent: QWidget | None = None,
        store: ChatStore | None = None,
        clock: Callable[[], float] = time.time,
        proposals: Proposals = _no_proposals,
        symbols: Callable[[], Sequence[str]] = _no_symbols,
        daily_cap: Callable[[], float] = _no_cap,
        mode: Callable[[], str] = _no_mode,
        losses_in_a_row: Callable[[], int] = _no_losses,
        fa: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AiChatPanel")
        self.fa = fa
        self.client = client
        self.tools = tools
        self.language = language
        self.store = store
        self.clock = clock
        self.proposals_of = proposals
        self.symbols = symbols
        self.daily_cap = daily_cap
        self.operating_mode = mode
        self.losses_in_a_row = losses_in_a_row
        self.limits = Limits()
        self.start_job: Callable[[Job], None] = _start_thread
        self.confirm: Callable[[str], bool] = self._ask
        self.on_proposal: Callable[[str], None] = lambda _text: None
        self.on_feedback: Callable[[str, Turn], None] = lambda _kind, _turn: None
        self.chat: Chat = start_chat(clock())
        self.turns: list[Turn] = []
        self.views: list[TurnView] = []
        self.plan_turns: set[int] = set()
        self.notes: list[str] = load_notes(store.folder) if store is not None else []
        self.auto: dict[str, object] = dict(load_auto(store.folder)) if store else {}
        self.tokens: ThemeTokens = DARK
        self._cancel = threading.Event()
        self._busy = False
        self._auto_busy = False
        self._frame = 0
        self.bridge = _Bridge()
        self.bridge.step.connect(self.show_step, Qt.ConnectionType.QueuedConnection)
        self.bridge.done.connect(self.show_turn, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        self.bridge.auto_done.connect(self.save_auto_chat, Qt.ConnectionType.QueuedConnection)
        self.bridge.auto_failed.connect(self.auto_failed, Qt.ConnectionType.QueuedConnection)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.rail = HistoryRail(self.t)
        self.rail.setVisible(store is not None)
        outer.addWidget(self.rail)
        center = QWidget()
        center.setObjectName("AiChatCenter")
        middle = QVBoxLayout(center)
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        outer.addWidget(center, 1)
        self.inspector = Inspector(self.t)
        outer.addWidget(self.inspector)
        self._build_header(middle)
        self.stack = QStackedWidget()
        middle.addWidget(self.stack, 1)
        self._build_conversation()
        self.manual_holder = QScrollArea()
        self.manual_holder.setWidgetResizable(True)
        self.manual_holder.setFrameShape(QFrame.Shape.NoFrame)
        self.stack.addWidget(self.manual_holder)
        self._connect()
        self.spin_timer = QTimer(self)
        self.spin_timer.setInterval(250)
        self.spin_timer.timeout.connect(self._spin)
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(60_000)
        self.auto_timer.timeout.connect(self.run_due_reviews)
        self.auto_timer.start()
        self.show_notes()
        self.show_auto()
        self.setStyleSheet(lab_qss(self.tokens, fa))
        self.refresh_history()
        self._update()

    # Words -------------------------------------------------------------------------------
    def t(self, english: str) -> str:
        return FA.get(english, english) if self.fa else english

    # Building ----------------------------------------------------------------------------
    def _build_header(self, middle: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setProperty("v2", "chat_head")
        bar.setMinimumHeight(52)
        row = QHBoxLayout(bar)
        row.setContentsMargins(22, 10, 22, 10)
        row.setSpacing(10)
        self.title = v2_label(self.t("New chat"), "chat_title")
        self.title.setObjectName("AiChatTitle")
        row.addWidget(self.title, 1)
        self.mode_switch = Segmented([self.t("Chat"), self.t("Manual")])
        self.mode_switch.choose(0)
        row.addWidget(self.mode_switch)
        self.export_button = v2_button("\u2913  " + self.t("Export"), "ghost")
        self.export_button.setObjectName("AiChatExport")
        row.addWidget(self.export_button)
        self.panel_button = _icon("\u25eb", self.t("Side panel"))
        self.panel_button.setCheckable(True)
        self.panel_button.setChecked(True)
        row.addWidget(self.panel_button)
        middle.addWidget(bar)

    def _build_conversation(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.transcript = QScrollArea()
        self.transcript.setObjectName("AiChatTranscript")
        self.transcript.setWidgetResizable(True)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        centered = QHBoxLayout(body)
        centered.setContentsMargins(26, 26, 26, 10)
        column = QWidget()
        column.setMaximumWidth(CHAT_WIDTH)
        centered.addStretch(1)
        centered.addWidget(column, 100)
        centered.addStretch(1)
        self.messages = QVBoxLayout(column)
        self.messages.setContentsMargins(0, 0, 0, 0)
        self.messages.setSpacing(34)
        self.welcome = self._build_welcome()
        self.messages.addWidget(self.welcome)
        self.messages.addStretch(1)
        self.transcript.setWidget(body)
        layout.addWidget(self.transcript, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(26, 8, 26, 14)
        holder = QWidget()
        holder.setMaximumWidth(CHAT_WIDTH)
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        self.composer = Composer(self.t)
        self.composer.options = self.mention_options
        box.addWidget(self.composer)
        hints = QHBoxLayout()
        hints.setSpacing(14)
        for key, word in (("Enter", "send"), ("Shift Enter", "new line"), ("Esc", "stop")):
            hints.addWidget(v2_label(f"{key}  {self.t(word)}", "note"))
        hints.addStretch(1)
        self.status = _text("", "note")
        self.status.setObjectName("AiChatStatus")
        hints.addWidget(self.status)
        self.read_only = v2_label(self.t("The agent only reads and suggests."), "note")
        hints.addWidget(self.read_only)
        box.addLayout(hints)
        bottom.addStretch(1)
        bottom.addWidget(holder, 100)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        self.input = self.composer.input
        self.send_button = self.composer.send_button
        self.stop_button = self.composer.stop_button
        self.budget = self.composer.budget
        self.stack.addWidget(page)

    def _build_welcome(self) -> QWidget:
        welcome = QWidget()
        welcome.setMaximumWidth(640)
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(0, 40, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(v2_label("AI ANALYST \u00b7 READ-ONLY", "cap"))
        layout.addWidget(v2_label(self.t("What should we look into?"), "h_welcome"))
        layout.addWidget(_text(self.t(WELCOME), "muted", True))
        layout.addSpacing(12)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.example_buttons: list[QPushButton] = []
        for index, (question, detail) in enumerate(EXAMPLES):
            button = v2_button(f"{self.t(question)}\n{self.t(detail)}", "box")
            button.setProperty("question", self.t(question))
            button.setObjectName("AiChatExample")
            button.setMinimumHeight(58)
            button.clicked.connect(lambda _c=False, q=self.t(question): self.ask(q))
            grid.addWidget(button, index // 2, index % 2)
            self.example_buttons.append(button)
        layout.addLayout(grid)
        return welcome

    def _connect(self) -> None:
        rail = self.rail
        self.new_button = rail.new_button
        self.new_button.clicked.connect(self.new_chat)
        rail.search.textChanged.connect(lambda _text: self.refresh_history())
        rail.filters.group.idClicked.connect(lambda _index: self.refresh_history())
        self.composer.submitted.connect(self.send)
        self.send_button.clicked.connect(self.send)
        self.stop_button.clicked.connect(self.stop)
        self.export_button.clicked.connect(self.export_markdown)
        self.panel_button.toggled.connect(self._show_inspector)
        self.mode_switch.group.idClicked.connect(lambda index: self.show_mode(MODES[index]))
        self.inspector.note_input.returnPressed.connect(self._add_note_from_input)
        for key, switch in self.inspector.auto_switches.items():
            switch.toggled.connect(lambda on, k=key: self.set_auto(k, on))
        send_keys = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        send_keys.setContext(Qt.ShortcutContext.WidgetShortcut)
        send_keys.activated.connect(self.send)
        stop_keys = QShortcut(QKeySequence("Escape"), self)
        stop_keys.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        stop_keys.activated.connect(self.stop)

    # Mode and panels ---------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return MODES[self.stack.currentIndex()]

    def set_manual(self, widget: QWidget) -> None:
        """The Manual flow (export, paste, backtest, activate) behind the Manual switch."""
        widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.manual_holder.setWidget(widget)

    def show_mode(self, mode: str) -> None:
        index = MODES.index(mode) if mode in MODES else 0
        self.stack.setCurrentIndex(index)
        self.mode_switch.choose(index)
        manual = index == 1
        self.inspector.setVisible(not manual and self.panel_button.isChecked())
        self.export_button.setVisible(not manual)
        self.title.setText(self.t("Manual mode") if manual else self._chat_title())

    def _show_inspector(self, shown: bool) -> None:
        self.inspector.setVisible(shown and self.mode == "chat")

    def _chat_title(self) -> str:
        return self.chat.title if self.chat.turns else self.t("New chat")

    # Asking ------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def send(self) -> bool:
        question = self.composer.question()
        if not question:
            return False
        started = self.ask(question, plan=self.composer.plan_button.isChecked())
        if started:
            self.composer.clear()
        return started

    def ask(self, question: str, *, plan: bool = False) -> bool:
        """Start one turn in the worker thread; False when it cannot start."""
        if self._busy or not question.strip():
            return False
        found = self.client()
        if isinstance(found, str):
            self.status.setText(found)
            return False
        client: AiClient = found
        self.show_model(client.settings.model)
        self._add_view(question, client.settings.model)
        if plan:
            self.plan_turns.add(len(self.views) - 1)
        self.welcome.hide()
        self._busy = True
        self._cancel.clear()
        self.status.setText("")
        self._update()
        self.spin_timer.start()
        tools = self._allowed_tools()
        memory = notes_turn(self.notes)
        history = [*self.turns[-5:], *([memory] if memory is not None else [])]
        limits = self.limits
        language = "fa" if self.fa and not self.language() else self.language()
        bridge = self.bridge
        cancel = self._cancel
        asked = f"{question}\n\n{PLAN_NOTE}" if plan else question

        def complete(messages: Sequence[Message]) -> Completion:
            return client.complete(messages, max_tokens=limits.max_tokens)

        def job() -> None:
            try:
                turn = run_turn(
                    complete,
                    [] if plan else tools,
                    asked,
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
            bridge.done.emit(replace(turn, question=question))

        self.start_job(job)
        self._scroll_down()
        return True

    def show_model(self, model: str) -> None:
        """The model in the composer (it is chosen in the AI settings, behind the gear)."""
        combo = self.composer.model
        if model and combo.findText(model) < 0:
            combo.addItem(model)
        if model:
            combo.setCurrentText(model)

    def _allowed_tools(self) -> list[Tool]:
        found = list(self.tools())
        switches = self.inspector.switches
        if not switches["Trade rows"].isChecked():
            found = [tool for tool in found if tool.name != "trades"]
        if not switches["Logs"].isChecked():
            found = [tool for tool in found if "log" not in tool.name]
        return found

    def _add_view(self, question: str, model: str = "") -> TurnView:
        view = TurnView(question, self.t, self.fa, model)
        index = len(self.views)
        view.copy_button.clicked.connect(lambda: self._copy(index))
        view.again_button.clicked.connect(lambda: self._again(index))
        view.branch_button.clicked.connect(lambda: self.branch(index))
        view.memory_button.clicked.connect(lambda: self._remember(index))
        view.good_button.clicked.connect(lambda: self._feedback(index, "good"))
        view.bad_button.clicked.connect(lambda: self._feedback(index, "bad"))
        view.run_plan_button.clicked.connect(lambda: self.ask(self.t(RUN_PLAN)))
        view.edit_plan_button.clicked.connect(lambda: self.input.setFocus())
        for button in view.follow_buttons:
            text = str(button.property("question"))
            button.clicked.connect(lambda _c=False, q=text: self.ask(q))
        self.messages.insertWidget(self.messages.count() - 1, view)
        self.views.append(view)
        return view

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
        self.show_mode("chat")
        self.status.setText(self.t("New chat: the AI forgets the earlier questions."))
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
            view = self._add_view(turn.question, turn.model)
            view.finish(turn)
            self.show_model(turn.model)
            self.turns.append(turn)
        self._show_follow_ups()
        self.welcome.setVisible(not found.turns)
        self.show_mode("chat")
        self.status.setText("")
        self.refresh_history()
        self._update()
        return True

    def branch(self, index: int) -> bool:
        """A new chat with the turns up to this one (the old chat stays as it was)."""
        if self._busy or index >= len(self.turns):
            return False
        kept = self.turns[: index + 1]
        chat = start_chat(self.clock())
        for turn in kept:
            chat = chat.with_turn(turn, self.clock())
        self.chat = replace(chat, title=f"{chat.title} \u2387")
        self._save()
        return self.open_chat(self.chat.chat_id) if self.store is not None else True

    def toggle_pin(self, chat_id: str = "") -> None:
        wanted = chat_id or self.chat.chat_id
        if self.store is None:
            return
        saved = self.store.load(wanted)
        if saved is None:
            self.status.setText("Pin works on saved chats: ask a question first.")
            return
        changed = self.store.set_pinned(wanted, not saved.pinned)
        if changed is not None and changed.chat_id == self.chat.chat_id:
            self.chat = changed
        self.refresh_history()

    def rename(self, chat_id: str, title: str) -> bool:
        if self.store is None:
            return False
        changed = self.store.rename(chat_id, title)
        if changed is not None and changed.chat_id == self.chat.chat_id:
            self.chat = changed
        self.refresh_history()
        return changed is not None

    def delete_selected(self, chat_id: str = "") -> bool:
        wanted = chat_id or self.chat.chat_id
        if self.store is None or self._busy:
            return False
        saved = self.store.load(wanted)
        if saved is None:
            return False
        text = self.t('Delete "{title}"? This cannot be undone.').format(title=saved.title)
        if not self.confirm(text):
            return False
        self.store.delete(wanted)
        if wanted == self.chat.chat_id:
            self.new_chat()
        self.refresh_history()
        return True

    def refresh_history(self) -> None:
        store = self.store
        chats = store.chats() if store is not None else []
        rows = self.rail.show_chats(chats, self.clock(), self.chat.chat_id, self.proposal_count)
        for row in rows:
            row.opened.connect(self._open_row)
            row.pinned.connect(self.toggle_pin)
            row.renamed.connect(self.rename)
            row.deleted.connect(self.delete_selected)
        if self.mode == "chat":
            self.title.setText(self._chat_title())
        self.show_proposals(chats)
        self._show_budget(chats)
        self._tint()

    # Results from the worker -------------------------------------------------------------
    def show_step(self, step: object) -> None:
        if isinstance(step, Step) and self.views:
            self.views[-1].add_step(step)
            self._scroll_down()

    def show_turn(self, turn: object) -> None:
        self._busy = False
        self.spin_timer.stop()
        if isinstance(turn, Turn):
            self.turns.append(turn)
            if self.views:
                plan = len(self.views) - 1 in self.plan_turns
                self.views[-1].finish(turn, plan=plan)
            self.status.setText(turn.stopped.capitalize() if turn.stopped else "")
            self.chat = self.chat.with_turn(turn, self.clock())
            self._save()
        self._show_follow_ups()
        self.refresh_history()
        self._update()
        self._scroll_down()

    def show_failure(self, text: str) -> None:
        self._busy = False
        self.spin_timer.stop()
        if self.views:
            self.views[-1].fail(text)
        self.status.setText(f"The AI request failed: {text}")
        self._update()

    # Proposals, memory and the scheduled reviews -----------------------------------------
    def chat_proposals(self, chat: Chat) -> list[Proposal]:
        found: list[Proposal] = []
        for turn in chat.turns:
            found += list(self.proposals_of(turn.answer)) if turn.answer else []
        return found

    def proposal_count(self, chat: Chat) -> int:
        return len(self.chat_proposals(chat))

    def show_proposals(self, chats: Sequence[Chat]) -> None:
        box = self.inspector.proposal_box
        _clear(box)
        shown = 0
        for chat in chats:
            for turn in chat.turns:
                for strategy, param, now, suggested in self.proposals_of(turn.answer or ""):
                    box.addWidget(self._proposal_row(chat, turn, strategy, param, now, suggested))
                    shown += 1
        if not shown:
            box.addWidget(
                _text(
                    self.t("No proposals yet. An answer that suggests a change lists it here."),
                    "note",
                    True,
                ),
            )

    def _proposal_row(
        self,
        chat: Chat,
        turn: Turn,
        strategy: str,
        param: str,
        now: str,
        suggested: str,
    ) -> QWidget:
        row = QFrame()
        row.setProperty("v2", "irow")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(3)
        path = v2_label(f"{strategy}.{param}", "mono_muted")
        path.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout.addWidget(path)
        line = QHBoxLayout()
        line.addWidget(v2_label(f"{now} \u2192 {suggested}", "mono"))
        line.addStretch(1)
        applied = now == suggested
        tag_text = self.t("Applied" if applied else "Waiting")
        tag_tone = "profit" if applied else "warning"
        line.addWidget(Tag(tag_text, tag_tone, mono=False))
        layout.addLayout(line)
        actions = QHBoxLayout()
        open_button = v2_button(chat.title, "link")
        open_button.clicked.connect(lambda _c=False, c=chat.chat_id: self.open_chat(c))
        test_button = v2_button(self.t("Test in Manual"), "outline")
        test_button.clicked.connect(lambda _c=False, a=turn.answer: self.test_proposal(a))
        actions.addWidget(open_button, 1)
        actions.addWidget(test_button)
        layout.addLayout(actions)
        return row

    def test_proposal(self, answer: str) -> None:
        """Open the answer's change in Manual: checked, then backtest and activate there."""
        self.on_proposal(answer)
        self.show_mode("manual")

    def show_notes(self) -> None:
        inspector = self.inspector
        _clear(inspector.notes_box)
        head = self.t("Notes given to the agent in every chat ({count} of 10).")
        count = persian_digits(str(len(self.notes))) if self.fa else str(len(self.notes))
        inspector.notes_box.addWidget(_text(head.format(count=count), "note", True))
        for index, note in enumerate(self.notes):
            row = QFrame()
            row.setProperty("v2", "irow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 6, 0, 6)
            line.addWidget(v2_label("\u2691", "muted"))
            line.addWidget(_text(note, "row", True), 1)
            remove = _icon("\u2715", self.t("Delete"))
            remove.setFixedSize(24, 24)
            remove.clicked.connect(lambda _c=False, i=index: self.remove_note(i))
            line.addWidget(remove)
            inspector.notes_box.addWidget(row)
        inspector.note_input.setEnabled(len(self.notes) < MAX_NOTES)

    def add_note(self, text: str) -> bool:
        clean = " ".join(text.split())
        if not clean:
            return False
        if len(self.notes) >= MAX_NOTES:
            self.status.setText(self.t("Memory is full (10 notes)."))
            return False
        self.notes = self._store_notes([*self.notes, clean])
        self.show_notes()
        return True

    def remove_note(self, index: int) -> None:
        self.notes = self._store_notes([n for i, n in enumerate(self.notes) if i != index])
        self.show_notes()

    def _store_notes(self, notes: Sequence[str]) -> list[str]:
        if self.store is None:
            return list(notes)[:MAX_NOTES]
        try:
            return save_notes(self.store.folder, notes)
        except OSError as error:
            self.status.setText(f"The notes could not be saved: {error}")
            return list(notes)[:MAX_NOTES]

    def _add_note_from_input(self) -> None:
        if self.add_note(self.inspector.note_input.text()):
            self.inspector.note_input.clear()

    def show_auto(self) -> None:
        for key, switch in self.inspector.auto_switches.items():
            switch.blockSignals(True)
            switch.setChecked(self.auto.get(key) is True)
            switch.blockSignals(False)
        last = self.auto.get("last_run")
        when = self.t("never")
        if isinstance(last, int | float) and last > 0:
            when = datetime.fromtimestamp(float(last)).strftime("%Y-%m-%d %H:%M")
        cost = self.auto.get("last_cost")
        if isinstance(cost, int | float) and cost > 0:
            when += f" \u00b7 ${float(cost):.3f}"
        text = self.t("Last run: {when}").format(when=when)
        self.inspector.auto_note.setText(f"{text}\n{self.t('Runs only while the app is open.')}")

    def set_auto(self, key: str, on: bool) -> None:
        self.auto[key] = on
        self._save_auto()
        self.show_auto()

    def _save_auto(self) -> None:
        if self.store is None:
            return
        try:
            save_auto(self.store.folder, self.auto)
        except OSError as error:
            self.status.setText(f"The schedule could not be saved: {error}")

    def run_due_reviews(self, now: float | None = None) -> list[str]:
        """Start the scheduled reviews that are due (one at a time, never while asking)."""
        if self._busy or self._auto_busy or self.store is None:
            return []
        moment = self.clock() if now is None else now
        losses = self.losses_in_a_row()
        due = due_jobs(self.auto, moment, losses)
        if not due:
            return []
        found = self.client()
        if isinstance(found, str):
            return []
        key = due[0]
        today = datetime.fromtimestamp(moment).date().isoformat()
        if key == "streak":
            self.auto["streak_seen"] = losses
        else:
            self.auto[f"{key}_ran"] = today
        self._save_auto()
        client: AiClient = found
        question = self.t(AUTO_QUESTIONS[key])
        tools = self._allowed_tools()
        memory = notes_turn(self.notes)
        limits = self.limits
        language = "fa" if self.fa and not self.language() else self.language()
        bridge = self.bridge
        self._auto_busy = True

        def complete(messages: Sequence[Message]) -> Completion:
            return client.complete(messages, max_tokens=limits.max_tokens)

        def job() -> None:
            try:
                turn = run_turn(
                    complete,
                    tools,
                    question,
                    history=[memory] if memory is not None else [],
                    limits=limits,
                    language=language,
                )
            except Exception as error:
                bridge.auto_failed.emit(f"{type(error).__name__}: {error}")
                return
            bridge.auto_done.emit(turn)

        self.start_job(job)
        return due[:1]

    def auto_failed(self, text: str) -> None:
        self._auto_busy = False
        self.status.setText(f"The automatic review failed: {text}")

    def save_auto_chat(self, turn: object) -> None:
        self._auto_busy = False
        if not isinstance(turn, Turn) or self.store is None:
            return
        now = self.clock()
        chat = replace(start_chat(now).with_turn(turn, now), auto=True)
        try:
            self.store.save(chat)
        except OSError as error:
            self.status.setText(f"The automatic review could not be saved: {error}")
            return
        self.auto["last_run"] = now
        self.auto["last_cost"] = turn.cost
        self._save_auto()
        self.show_auto()
        self.status.setText(self.t("An automatic review was saved."))
        self.refresh_history()

    # Actions on a turn -------------------------------------------------------------------
    def _copy(self, index: int) -> None:
        if index < len(self.turns):
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(self.turns[index].answer)
            self.status.setText(self.t("Copied."))

    def _again(self, index: int) -> None:
        if index < len(self.turns):
            self.ask(self.turns[index].question)

    def _remember(self, index: int) -> None:
        if index < len(self.turns):
            first = self.turns[index].answer.strip().splitlines()
            if first and self.add_note(first[0].strip("#*_ ")):
                self.status.setText(self.t("Added to memory."))

    def _feedback(self, index: int, kind: str) -> None:
        if index < len(self.turns):
            self.on_feedback(kind, self.turns[index])
            self.status.setText(self.t("Thanks." if kind == "good" else "Noted."))

    def export_markdown(self) -> Path | None:
        """Save the open chat as Markdown next to the chats; None when it is empty."""
        if not self.chat.turns or self.store is None:
            self.status.setText(self.t("Nothing to export yet."))
            return None
        path = self.store.folder / "exports" / f"{self.chat.chat_id}.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(chat_markdown(self.chat).encode("utf-8"))
        except OSError as error:
            self.status.setText(f"The chat could not be exported: {error}")
            return None
        self.status.setText(self.t("Saved as {name}.").format(name=path.name))
        return path

    def mention_options(self) -> list[tuple[str, str]]:
        found = [(f"@{symbol}", self.t("symbol")) for symbol in self.symbols()]
        found += [(f"@{name}", self.t("strategy")) for name in STRATEGIES]
        found.append(("@risk", self.t("the risk settings")))
        return found

    # Theme -------------------------------------------------------------------------------
    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.setStyleSheet(lab_qss(tokens, self.fa))
        self._tint()

    def _tint(self) -> None:
        """The painted pieces (tags, switches, the budget bar) in the current colors."""
        for widget in self.findChildren(QWidget):
            apply = getattr(widget, "apply_tokens", None)
            if callable(apply):
                apply(self.tokens)

    # Helpers -----------------------------------------------------------------------------
    def _show_follow_ups(self) -> None:
        for index, view in enumerate(self.views):
            last = index == len(self.views) - 1
            view.follow.setVisible(last and view.turn is not None and bool(view.turn.answer))

    def _save(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save(self.chat)
        except OSError as error:
            self.status.setText(f"The chat could not be saved: {error}")

    def _open_row(self, chat_id: str) -> None:
        if chat_id != self.chat.chat_id:
            self.open_chat(chat_id)

    def _clear_views(self) -> None:
        for view in self.views:
            self.messages.removeWidget(view)
            view.deleteLater()
        self.views.clear()
        self.turns.clear()
        self.plan_turns.clear()

    def _show_budget(self, chats: Sequence[Chat]) -> None:
        cap = self.daily_cap()
        spent = spent_today(chats, self.clock())
        text = f"${spent:.2f} / ${cap:.2f}" if cap > 0 else f"${spent:.2f}"
        per = self.limits.max_usd
        tip = f"Spent today on all chats. Limit ${per:.2f} per question, {self.limits.max_steps}"
        self.budget.setText(text)
        self.budget.setToolTip(f"{tip} steps.")
        self.inspector.budget.setText(text)
        self.inspector.budget_bar.set_share(spent / cap if cap > 0 else 0.0)
        mode = self.operating_mode()
        self.inspector.context_values["Mode"].setText(mode or "\u2014")
        limits = (
            f"At most {self.limits.max_steps} steps and ${per:.2f} per message. The order tool, "
            "Go-Live, the kill switch and the passwords are out of the agent's reach."
        )
        if self.fa:
            limits = (
                f"حداکثر {persian_digits(str(self.limits.max_steps))} گام و ${per:.2f} در هر "
                "پیام. ابزار سفارش، Go-Live، کلید توقف و رمزها در دسترس عامل نیست."
            )
        self.inspector.limits_note.setText(limits)

    def _ask(self, text: str) -> bool:
        answer = QMessageBox.question(self, self.t("Delete chat"), text)
        return answer == QMessageBox.StandardButton.Yes

    def _scroll_down(self) -> None:
        bar = self.transcript.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _spin(self) -> None:
        self._frame += 1
        if self.views:
            self.views[-1].tick(self._frame)

    def _update(self) -> None:
        busy = self._busy
        self.send_button.setVisible(not busy)
        self.send_button.setEnabled(not busy)
        self.stop_button.setVisible(busy)
        self.stop_button.setEnabled(busy)
        self.new_button.setEnabled(not busy)
        self.rail.history_scroll.setEnabled(not busy)
        for button in self.example_buttons:
            button.setEnabled(not busy)
