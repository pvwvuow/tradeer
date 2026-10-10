"""The AI Lab chat (docs/AI_LAB_AGENT.md section 1, Phase 18b, docs/NOCURVE_V2.md 20e): ask,
watch the steps, read the answer and its cost; off until the connection is saved on; the key
never leaves the header; Stop and New chat; the chat is saved and opens again, its usage is
in the ledger and the agent can ask for a chart card."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.ui.ai_chat import ChatPanel, persian, worked_line
from app.ui.ai_lab_page import AiLabPage
from tests.ui.test_llm_panel import KEY, page_with_llm, turn_on


class Scripted:
    """A chat-completions service that answers with the given texts in turn."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.bodies: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
        self.bodies.append(body.decode("utf-8"))
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        payload = {
            "model": "grok-test",
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000},
        }
        return json.dumps(payload).encode("utf-8")


TOOL = json.dumps({"thinking": "I read the trades.", "tool": "trades", "args": {"days": 0}})
ANSWER = json.dumps({"thinking": "Enough.", "answer": "You won **8 of 12** trades."})
CHART = json.dumps({"thinking": "A chart helps.", "tool": "chart", "args": {"kind": "stats"}})


def lab_page(qtbot: QtBot, tmp_path: Path, transport: Scripted) -> AiLabPage:
    page, _rig = page_with_llm(qtbot, tmp_path, transport)  # type: ignore[arg-type]
    assert turn_on(page.llm_panel)
    page.chat.start_job = lambda job: job()
    return page


def chat_page(qtbot: QtBot, tmp_path: Path, transport: Scripted) -> ChatPanel:
    return lab_page(qtbot, tmp_path, transport).chat


def test_a_question_shows_the_steps_the_answer_and_the_cost(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    transport = Scripted(TOOL, ANSWER)
    chat = chat_page(qtbot, tmp_path, transport)
    chat.input.setPlainText("How did I do?")
    assert chat.send() is True and chat.input.toPlainText() == ""
    qtbot.waitUntil(lambda: not chat.busy, timeout=10_000)
    view = chat.views[-1]
    assert view.answer.text() == "You won **8 of 12** trades."
    steps = view.steps.text()
    assert steps.startswith("Worked 1 step(s)") and "Read the trades (days 0)" in steps
    assert view.cost.text().startswith("1 step(s) \u00b7 2,000 tokens")
    assert view.meta.text().startswith("Worked ") and view.steps.isHidden()
    view.meta.click()
    assert not view.steps.isHidden()
    assert len(transport.bodies) == 2 and all(KEY not in body for body in transport.bodies)
    assert "Result of trades" in transport.bodies[1] and "12 trades" in transport.bodies[1]
    assert chat.turns[-1].answer and chat.send_button.isEnabled()


def test_it_is_off_until_the_connection_is_saved(qtbot: QtBot, tmp_path: Path) -> None:
    transport = Scripted(ANSWER)
    page, _rig = page_with_llm(qtbot, tmp_path, transport)  # type: ignore[arg-type]
    assert page.chat.ask("Hello?") is False
    assert "off" in page.chat.status.text() and transport.bodies == []
    assert page.chat.views == []


def test_new_chat_forgets_and_persian_runs_right_to_left(qtbot: QtBot, tmp_path: Path) -> None:
    chat = chat_page(qtbot, tmp_path, Scripted(ANSWER))
    assert not chat.quick.isVisibleTo(chat)  # the welcome shows its own suggestions
    assert chat.ask("\u0686\u0637\u0648\u0631 \u0628\u0648\u062f\u061f") is True
    qtbot.waitUntil(lambda: not chat.busy, timeout=10_000)
    assert chat.quick.isVisibleTo(chat) and not chat.welcome.isVisibleTo(chat)
    assert chat.views[-1].question.layoutDirection().name == "RightToLeft"
    assert persian("\u0633\u0644\u0627\u0645 EURUSD") and not persian("Hello")
    chat.new_chat()
    assert chat.turns == [] and chat.views == [] and "forgets" in chat.status.text()
    assert chat.welcome.isVisibleTo(chat) and not chat.quick.isVisibleTo(chat)


def test_the_chat_is_saved_opens_again_and_is_in_the_ledger(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page = lab_page(qtbot, tmp_path, Scripted(TOOL, ANSWER))
    chat = page.chat
    assert chat.ask("How did I do?") is True
    qtbot.waitUntil(lambda: not chat.busy, timeout=10_000)
    assert page.chats is not None
    saved = page.chats.chats()
    assert len(saved) == 1 and saved[0].title == "How did I do?"
    raw = next((tmp_path / "ai_lab" / "ai_chats").glob("*.json")).read_text(encoding="utf-8")
    assert KEY not in raw and "Result of trades" not in raw
    assert page.usage is not None
    records = page.usage.records()
    assert len(records) == 1 and records[0].kind == "chat" and records[0].input_tokens == 1800
    chat.new_chat()
    assert chat.open_chat(saved[0].chat_id) is True
    assert [view.question.text() for view in chat.views] == ["How did I do?"]
    assert chat.turns[0].answer == "You won **8 of 12** trades."
    page.inspector.show_panel("history")
    assert list(page.history_panel.buttons) == [saved[0].chat_id]
    page.inspector.show_panel("usage")
    assert page.usage_panel.requests.text() == "1"


def test_the_agent_can_ask_for_a_chart_card(qtbot: QtBot, tmp_path: Path) -> None:
    page = lab_page(qtbot, tmp_path, Scripted(CHART, ANSWER))
    page.export_days.setValue(0)
    assert page.chat.ask("How am I doing overall?") is True
    qtbot.waitUntil(lambda: not page.chat.busy, timeout=10_000)
    assert page.chat.extras and page.chat.extras[-1].objectName() == "AiStatsCard"
    assert "Draw a chart (kind stats)" in page.chat.views[-1].steps.text()


def test_enter_sends_and_shift_enter_adds_a_line(qtbot: QtBot, tmp_path: Path) -> None:
    chat = chat_page(qtbot, tmp_path, Scripted(ANSWER))
    chat.input.setPlainText("Hello")
    qtbot.keyClick(chat.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert "\n" in chat.input.toPlainText() and chat.views == []
    qtbot.keyClick(chat.input, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not chat.busy, timeout=10_000)
    assert len(chat.views) == 1 and chat.input.toPlainText() == ""
    assert worked_line(11, 5, True) == "\u06f1\u06f1 ثانیه کار کردم \u00b7 \u06f5 مرحله"
