"""The AI Lab chat (docs/AI_LAB_AGENT.md section 1, Phase 18b): ask, watch the steps, read
the answer and its cost; off until the connection is saved on; the key never leaves the
header; Stop and New chat."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pytestqt.qtbot import QtBot

from app.ui.ai_chat import ChatPanel, persian
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


def chat_page(qtbot: QtBot, tmp_path: Path, transport: Scripted) -> ChatPanel:
    page, _rig = page_with_llm(qtbot, tmp_path, transport)  # type: ignore[arg-type]
    assert turn_on(page.llm_panel)
    page.chat.start_job = lambda job: job()
    assert page.tabs.currentWidget() is page.chat
    return page.chat


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
    assert chat.ask("\u0686\u0637\u0648\u0631 \u0628\u0648\u062f\u061f") is True
    qtbot.waitUntil(lambda: not chat.busy, timeout=10_000)
    assert chat.views[-1].question.layoutDirection().name == "RightToLeft"
    assert persian("\u0633\u0644\u0627\u0645 EURUSD") and not persian("Hello")
    chat.new_chat()
    assert chat.turns == [] and chat.views == [] and "forgets" in chat.status.text()
