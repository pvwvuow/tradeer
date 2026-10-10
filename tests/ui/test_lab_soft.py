"""The softer AI Lab (docs/AI_LAB_V3.md section 3, 0.44.2): rounder shapes that Qt parses,
a short fade for new messages and cards, then a faint shadow under a card, the AI's mark in
front of an answer and the welcome's suggestions as tiles."""

from __future__ import annotations

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from app.ui.ai_chat import ChatPanel, TurnView, chat_qss
from app.ui.lab_parts import LabCard, lab_qss
from app.ui.lab_soft import CARD_RADIUS, AiMark, fade_in, soft_qss
from app.ui.theme import DARK, LIGHT


def test_the_soft_rules_parse_and_round_the_cards(qtbot: QtBot) -> None:
    messages: list[str] = []

    def handler(_kind: QtMsgType, _context: object, text: str) -> None:
        messages.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        for tokens in (DARK, LIGHT):
            root = QWidget()
            qtbot.addWidget(root)
            card = LabCard("chat", "A card")
            card.setParent(root)
            tile = QPushButton("tile", root)
            tile.setProperty("lab", "tile")
            bubble = QLabel("hello", root)
            bubble.setProperty("lab", "bubble")
            root.setStyleSheet(lab_qss(tokens) + chat_qss(tokens))
            root.show()
            for child in root.findChildren(QWidget):
                child.ensurePolished()
    finally:
        qInstallMessageHandler(previous)
    assert not [text for text in messages if "Could not parse" in text]
    rules = soft_qss(DARK)
    assert f"border-radius: {CARD_RADIUS}px" in rules and 'QPushButton[lab="tile"]' in rules
    assert rules in chat_qss(DARK)


def test_a_new_card_fades_in_then_has_a_shadow(qtbot: QtBot) -> None:
    card = LabCard("chat", "A card")
    qtbot.addWidget(card)
    card.show()
    assert fade_in(card, DARK) is not None
    shadowed = lambda: isinstance(card.graphicsEffect(), QGraphicsDropShadowEffect)  # noqa: E731
    qtbot.waitUntil(shadowed, timeout=3000)
    assert fade_in(card, DARK) is None  # it keeps its shadow
    still = LabCard("chat", "Reduced motion")
    qtbot.addWidget(still)
    assert fade_in(still, DARK, motion=False) is None
    assert isinstance(still.graphicsEffect(), QGraphicsDropShadowEffect)
    plain = QFrame()
    qtbot.addWidget(plain)
    assert fade_in(plain, DARK, motion=False) is None and plain.graphicsEffect() is None


def test_the_welcome_has_tiles_and_answers_get_the_mark(qtbot: QtBot) -> None:
    chat = ChatPanel(lambda: "The AI connection is off.")
    qtbot.addWidget(chat)
    chat.resize(900, 700)
    chat.show()
    assert len(chat.suggestion_buttons) == 4
    assert all(button.property("lab") == "tile" for button in chat.suggestion_buttons)
    assert chat.suggestions.items == chat.suggestion_buttons
    view = TurnView("How did I do?")
    qtbot.addWidget(view)
    assert isinstance(view.mark, AiMark) and view.mark.isHidden()
    mark = AiMark()
    qtbot.addWidget(mark)
    assert not mark.grab().isNull()
