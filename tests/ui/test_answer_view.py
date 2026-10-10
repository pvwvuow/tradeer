"""The answer drawn as a report and the ChatGPT-like chat (docs/AI_LAB_V4.md, 0.45.0): every
block becomes its own widget, the stylesheet parses, Copy puts the text on the clipboard, an
`app-chart` block holds the chart card the agent asked for, and Retry asks again."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from app.ui.ai_chat import chat_qss
from app.ui.answer_view import AnswerView, CopyButton, DonutChart
from app.ui.lab_parts import lab_qss
from app.ui.theme import DARK, LIGHT
from tests.ui.test_ai_chat import CHART, Scripted, lab_page
from tests.unit.test_answer_blocks import ANSWER


def test_every_block_has_its_widget_and_the_rules_parse(qtbot: QtBot) -> None:
    messages: list[str] = []

    def handler(_kind: QtMsgType, _context: object, text: str) -> None:
        messages.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        for tokens in (DARK, LIGHT):
            view = AnswerView()
            qtbot.addWidget(view)
            view.setStyleSheet(lab_qss(tokens) + chat_qss(tokens))
            view.apply_tokens(tokens)
            view.setText(ANSWER)
            view.resize(760, 1400)
            view.show()
            for child in view.findChildren(QWidget):
                child.ensurePolished()
            assert not view.grab().isNull()
    finally:
        qInstallMessageHandler(previous)
    assert not [text for text in messages if "Could not parse" in text]
    assert view.text() == ANSWER
    names = {child.objectName() for child in view.findChildren(QWidget)}
    for name in (
        "AnswerMetrics",
        "AnswerTable",
        "AnswerCallout",
        "AnswerChart",
        "AnswerSteps",
        "AnswerProgress",
        "AnswerCode",
        "AnswerChartSlot",
    ):
        assert name in names
    assert len(view.slots) == 1 and view.slots[0].kind == "candles" and view.free_slots()
    view.setText("Just **one** line.")
    assert view.kinds() == ["Text"] and view.slots == []
    donut = AnswerView()
    qtbot.addWidget(donut)
    donut.setText("```chart donut\nEURUSD | 3\nXAUUSD | 1\n```")
    assert donut.findChildren(DonutChart)


def test_copy_puts_the_text_on_the_clipboard(qtbot: QtBot) -> None:
    button = CopyButton(lambda: "| a | b |", name="Copy table")
    qtbot.addWidget(button)
    assert button.copy() == "| a | b |"
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None and clipboard.text() == "| a | b |"
    assert button.text().endswith("Copied")


def test_the_chart_card_stands_where_the_answer_put_it(qtbot: QtBot, tmp_path: Path) -> None:
    placed = json.dumps(
        {
            "thinking": "Enough.",
            "answer": "Here are your numbers.\n\n```app-chart\nstats\n```\n\nThat is all.",
        },
    )
    transport = Scripted(CHART, placed)
    page = lab_page(qtbot, tmp_path, transport)
    page.export_days.setValue(0)
    chat = page.chat
    assert chat.ask("How am I doing overall?") is True
    qtbot.waitUntil(lambda: not chat.busy, timeout=10_000)
    view = chat.views[-1]
    assert view.answer.kinds() == ["Text", "AppChart", "Text"]
    slot = view.answer.slots[0]
    assert slot.card is not None and slot.card.objectName() == "AiStatsCard"
    assert chat.extras[-1] is slot.card
    assert "```metrics" in transport.bodies[0]  # the agent knows the blocks
    view.copy_button.click()
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None and clipboard.text().startswith("Here are your numbers.")
    assert not view.actions.isHidden() and not view.retry_button.isHidden()
    view.retry_button.click()
    qtbot.waitUntil(lambda: not chat.busy and len(chat.views) == 2, timeout=10_000)
    assert chat.views[-1].question.text() == "How am I doing overall?"
    assert chat.down.objectName() == "AiChatDown"
