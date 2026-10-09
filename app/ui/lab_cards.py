"""The manual loop and the visual answers as cards in the AI Lab transcript (docs/NOCURVE_V2.md
20e2, 20e4), drawn like the owner's design.

- `export_card`: the three files of an export with their real sizes and the scope.
- `ask_card`: one Ask AI request: model, endpoint, the summary size against its cap, tokens,
  cost, and what is never sent.
- `PasteCard`: the checked answer: VALID or its problems, strategy / parameter / now /
  suggested, the AI's reason and expected effect, then Run comparison.
- `CompareCard`: the two full backtests: progress and Cancel, every metric, the three checks.
- `VerdictBox`: the verdict (BETTER or NOT BETTER) in words.
- `ActivateCard`: the four checks before Paper, Activate (with confirmation), Ignore, what is
  saved.
- `ChartCard`, `stats_card`, `note_card`: the charts, the KPI row and a plain message.

The cards only show; the page runs the existing code (`export_for_ai`, `llm_panel`,
`parse_suggestion`, `start_test`, `activate`) and fills them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ai.lab_files import size_text
from app.ui.lab_parts import (
    LTR,
    MIDDLE,
    CheckList,
    GridTable,
    LabCard,
    apply_tree,
    lab_button,
    lab_label,
    lab_words,
    mono_label,
)
from app.ui.theme import DEFAULT, ThemeTokens
from app.ui.v2 import Tag

Words = Callable[[str], str]
CARD_FA: dict[str, str] = {
    "Summary sent (cap {cap})": "خلاصه ارسال‌شده (سقف {cap})",
    "{count} chars": "{count} نویسه",
    "Tokens in / out": "توکن ورودی / خروجی",
    "Estimated cost": "هزینه تخمینی",
    "unknown (set the prices)": "نامعلوم (قیمت‌ها را وارد کنید)",
    "Time": "زمان",
    "one request": "یک درخواست",
    "Not sent: passwords, the API key, the trade list and the account number.": (
        "ارسال نشد: رمزها، کلید API، فهرست معاملات و شماره حساب."
    ),
    "prompt + stats + schemas": "prompt + stats + schemas",
    "two full backtests": "دو بک‌تست کامل",
    "Verdict": "حکم",
    "Not tested yet.": "هنوز آزموده نشده.",
    "Check again": "بررسی دوباره",
    "Run comparison": "Run comparison",
    "Cancel": "لغو",
    "Activate in Paper (with confirmation)": "فعال‌سازی در Paper (با تأیید)",
    "Ignore": "نادیده گرفتن",
    "Saved: strategies.json, one strategy_configs row with created_by = ai_suggestion and "
    "one audit_log row.": (
        "ذخیره می‌شود: strategies.json، یک ردیف strategy_configs با created_by = "
        "ai_suggestion و یک ردیف audit_log."
    ),
    "Copy the data": "کپی داده",
    "Enlarge": "بزرگ‌نمایی",
    "The data is on the clipboard.": "داده روی کلیپ‌بورد است.",
}
FILE_NOTES = {"report.md": "prompt + stats + schemas"}
PASTE_HEAD = ("STRATEGY", "PARAMETER", "NOW", "SUGGESTED")
PASTE_WIDTHS = (110, 120, 0, 0)
COMPARE_HEAD = ("METRIC", "CURRENT", "SUGGESTED")
COMPARE_WIDTHS = (0, 110, 110)
ENLARGED = (960, 560)


def card_words(fa: bool) -> Words:
    lab = lab_words(fa)

    def word(english: str) -> str:
        return CARD_FA.get(english, lab(english)) if fa else english

    return word


def export_card(paths: Sequence[Path], folder: str, scope: str, word: Words) -> LabCard:
    card = LabCard("file", word("Export for AI"), scope)
    card.setObjectName("AiExportCard")
    card.note.setLayoutDirection(LTR)
    card.add_tag(folder)
    for path in paths:
        try:
            size = size_text(path.stat().st_size)
        except OSError:
            size = "?"
        extra = FILE_NOTES.get(path.name)
        name = f"{path.name} ({word(extra)})" if extra else path.name
        card.add_row(name, size, mono_name=True)
    return card


def ask_card(
    *,
    model: str,
    host: str,
    sent_chars: int,
    cap: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    seconds: float,
    word: Words,
) -> LabCard:
    card = LabCard("chat", word("Ask AI"), word("one request"))
    card.setObjectName("AiAskCard")
    card.add_tag(model)
    card.add_tag(host)
    card.add_row(word("Summary sent (cap {cap})").format(cap=f"{cap:,}"), f"{sent_chars:,}")
    card.add_row(word("Tokens in / out"), f"{prompt_tokens:,} / {completion_tokens:,}")
    price = f"${cost:.4f}" if math.isfinite(cost) else word("unknown (set the prices)")
    card.add_row(word("Estimated cost"), price)
    card.add_row(word("Time"), f"{seconds:.1f} s")
    card.add_footer(
        word("Not sent: passwords, the API key, the trade list and the account number."),
    )
    return card


def note_card(icon: str, title: str, text: str, tone: str = "") -> LabCard:
    """A plain message as a card (why a chart or a step could not run)."""
    card = LabCard(icon, title)
    card.setObjectName("AiNoteCard")
    if tone:
        card.add_tag(tone.upper() if tone != "loss" else "REFUSED", tone)
    card.add_footer(text)
    return card


class PasteCard(LabCard):
    """The checked AI answer (one per page: checking again refills it)."""

    def __init__(self, word: Words) -> None:
        super().__init__("paste", word("Paste the answer"))
        self.setObjectName("AiPasteCard")
        self.count_tag = self.add_tag("changes[0]")
        self.state_tag = self.add_tag("VALID", "profit")
        self.diff = GridTable(PASTE_HEAD, PASTE_WIDTHS)
        self.diff.setObjectName("AiDiff")
        self.add_section(self.diff)
        self.status = lab_label("", "footer", wrap=True)
        self.status.setObjectName("AiCheckStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.add_section(self.status, (16, 12, 16, 12))
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.test_button = lab_button(word("Run comparison"), "primary")
        self.test_button.setObjectName("AiTest")
        self.check_button = lab_button(word("Check again"), "ghost")
        self.check_button.setObjectName("AiCheck")
        row.addWidget(self.test_button)
        row.addWidget(self.check_button)
        row.addStretch(1)
        self.add_section(actions, (16, 12, 16, 12))

    def show_result(self, rows: Sequence[Sequence[str]], changes: int, valid: bool) -> None:
        self.diff.set_rows(rows)
        self.count_tag.set(f"changes[{changes}]")
        self.state_tag.set("VALID" if valid else "INVALID", "profit" if valid else "loss")


class CompareCard(LabCard):
    def __init__(self, word: Words) -> None:
        super().__init__("compare", word("Run comparison"), word("two full backtests"))
        self.setObjectName("AiCompareCard")
        self.symbol_tag = self.add_tag("EURUSD")
        self.period_tag = self.add_tag("")
        top = QWidget()
        line = QVBoxLayout(top)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setProperty("lab", "progress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        line.addWidget(self.progress)
        row = QHBoxLayout()
        self.status = lab_label("", "footer", wrap=True)
        self.status.setObjectName("AiTestStatus")
        self.cancel_button = lab_button(word("Cancel"), "link")
        self.cancel_button.setObjectName("AiTestCancel")
        row.addWidget(self.status, 1)
        row.addWidget(self.cancel_button)
        line.addLayout(row)
        self.add_section(top, (16, 12, 16, 12))
        self.results = GridTable(COMPARE_HEAD, COMPARE_WIDTHS)
        self.results.setObjectName("AiResults")
        self.add_section(self.results)
        self.checks = CheckList()
        self.checks_frame = self.add_section(self.checks)
        self.checks_frame.hide()

    def set_scope(self, symbol: str, period: str) -> None:
        self.symbol_tag.set(symbol)
        self.period_tag.set(period)


class VerdictBox(QWidget):
    """The design's verdict box: "Verdict", the tag and the verdict in words."""

    def __init__(self, word: Words) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setProperty("lab", "verdict")
        self.frame.setProperty("tone", "")
        self.setObjectName("AiVerdictBox")
        box = QVBoxLayout(self.frame)
        box.setContentsMargins(22, 18, 22, 18)
        box.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(lab_label(word("Verdict"), "heading"), 0, MIDDLE)
        self.tag = Tag("NOT TESTED", "neutral")
        head.addWidget(self.tag, 0, MIDDLE)
        head.addStretch(1)
        box.addLayout(head)
        self.label = lab_label(word("Not tested yet."), "text", wrap=True)
        self.label.setObjectName("AiVerdict")
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.label)
        outer.addWidget(self.frame)

    def set_verdict(self, better: bool | None, text: str) -> None:
        if better is None:
            self.tag.set("NOT TESTED", "neutral")
            tone = ""
        else:
            self.tag.set("BETTER" if better else "NOT BETTER", "profit" if better else "warning")
            tone = "profit" if better else "warning"
        self.label.setText(text)
        if self.frame.property("tone") != tone:
            self.frame.setProperty("tone", tone)
            style = self.frame.style()
            style.unpolish(self.frame)
            style.polish(self.frame)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        apply_tree(self, tokens)


class ActivateCard(LabCard):
    def __init__(self, word: Words) -> None:
        super().__init__("play", word("Activate in Paper"))
        self.setObjectName("AiActivateCard")
        self.mode_tag = self.add_tag("PAPER MODE")
        self.checks = CheckList()
        self.add_section(self.checks)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.activate_button = lab_button(word("Activate in Paper (with confirmation)"), "primary")
        self.activate_button.setObjectName("AiActivate")
        self.ignore_button = lab_button(word("Ignore"), "ghost")
        self.ignore_button.setObjectName("AiIgnore")
        row.addWidget(self.activate_button)
        row.addWidget(self.ignore_button)
        row.addStretch(1)
        self.add_section(actions, (16, 12, 16, 12))
        self.status = lab_label("", "text", wrap=True)
        self.status.setObjectName("AiActivateStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_frame = self.add_section(self.status, (16, 0, 16, 12))
        self.add_footer(
            word(
                "Saved: strategies.json, one strategy_configs row with created_by = "
                "ai_suggestion and one audit_log row.",
            ),
        )


class ChartCard(LabCard):
    """A chart answer: the header with its tags, the chart, Copy the data and Enlarge."""

    def __init__(
        self,
        icon: str,
        title: str,
        chart: QWidget,
        *,
        word: Words,
        tags: Sequence[str] = (),
        note: str = "",
        data: Any = None,
        enlarge: Callable[[], QWidget] | None = None,
        footer: str = "",
    ) -> None:
        super().__init__(icon, title, note)
        self.setObjectName("AiChartCard")
        self.word = word
        self.data = data
        self.enlarge = enlarge
        for text in tags:
            self.add_tag(text)
        self.chart = chart
        self.copy_button = self._action(word("Copy the data"), self.copy_data)
        self.zoom_button = self._action(word("Enlarge"), self.show_large)
        self.copy_button.setEnabled(data is not None)
        self.zoom_button.setEnabled(enlarge is not None)
        self.add_section(chart, (12, 12, 12, 8))
        self.footer = self.add_footer(footer) if footer else None
        self.tokens: ThemeTokens = DEFAULT

    def _action(self, name: str, slot: Callable[[], object]) -> QPushButton:
        button = lab_button(name, "link")
        button.setToolTip(name)
        button.setAccessibleName(name)
        button.clicked.connect(slot)
        self.head.addWidget(button, 0, MIDDLE)
        return button

    def data_text(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=1, default=str)

    def copy_data(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None and self.data is not None:
            clipboard.setText(self.data_text())
            self.copy_button.setToolTip(self.word("The data is on the clipboard."))

    def show_large(self) -> QDialog | None:
        if self.enlarge is None:
            return None
        dialog = QDialog(self)
        dialog.setObjectName("AiChartDialog")
        dialog.setWindowTitle(self.title.text())
        layout = QVBoxLayout(dialog)
        chart = self.enlarge()
        apply_tree(dialog, self.tokens)
        apply = getattr(chart, "apply_tokens", None)
        if callable(apply):
            apply(self.tokens)
        layout.addWidget(chart)
        dialog.resize(*ENLARGED)
        dialog.show()
        return dialog

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        apply_tree(self, tokens)


def stats_card(cells: Sequence[tuple[str, str, str, str]], title: str, note: str) -> LabCard:
    """The KPI row: (caption, value, tone, under) per cell, e.g. ("NET", "+18.4%", ...)."""
    card = LabCard("bars", title, note)
    card.setObjectName("AiStatsCard")
    grid_box = QWidget()
    grid = QGridLayout(grid_box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(0)
    for column, (caption, value, tone, under) in enumerate(cells):
        cell = QWidget()
        box = QVBoxLayout(cell)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(6)
        cap = lab_label(caption, "cap")
        cap.setLayoutDirection(LTR)
        big = mono_label(value, tone)
        big.setProperty("size", "big")
        small = lab_label(under, "footer", wrap=True)
        box.addWidget(cap)
        box.addWidget(big)
        box.addWidget(small)
        grid.addWidget(cell, 0, column)
        grid.setColumnStretch(column, 1)
    card.add_section(grid_box)
    return card


def label_of(widget: QWidget) -> str:
    """The text of a card's footer (tests and the chat history read it)."""
    found = widget.findChildren(QLabel)
    return "\n".join(label.text() for label in found if label.text())
