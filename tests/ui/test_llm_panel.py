"""The AI Lab's optional "Ask AI" card (spec C13): off until saved on, the key only in the
credential store, the answer only in step 2, failures without the key."""

import json
from collections.abc import Mapping
from pathlib import Path

from pytestqt.qtbot import QtBot

from app.analytics.llm_client import LLM_FILE_NAME, LlmContext, LlmError, LlmSettingsSource
from app.core.credentials import MemoryStore
from app.core.execution_settings import ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource
from app.core.ui_prefs import UiPrefs
from app.ui.ai_lab_page import AiLabContext, AiLabPage
from app.ui.llm_panel import LlmPanel
from app.ui.main_window import MainWindow
from tests.unit.test_ai_export import TRADES

KEY = "sk-panel-test-key-0123456789"
NAME = "default/llm-api-key"
ANSWER = (
    "Weak trends lose money.\n```json\n"
    '{"changes": [{"strategy": "trend_pullback", "params": {"min_adx_h1": 25}, '
    '"reason": "weak trends lose", "expected_impact": "fewer losers"}]}\n```'
)


class Transport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.bodies: list[bytes] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
        self.bodies.append(body)
        if self.error is not None:
            raise self.error
        payload = {
            "model": "test-model",
            "choices": [{"message": {"content": ANSWER}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
        }
        return json.dumps(payload).encode("utf-8")


class Rig:
    def __init__(self, tmp_path: Path, transport: Transport) -> None:
        self.store = MemoryStore()
        self.logs: list[str] = []
        self.audits: list[str] = []
        self.llm = LlmContext(
            source=LlmSettingsSource(tmp_path),
            credentials=self.store,
            key_name=NAME,
            login=lambda: "87654321",
            transport=transport,
            log=lambda level, message: self.logs.append(f"{level} {message}"),
            record=lambda action, before, after: self.audits.append(action),
        )


def page_with_llm(qtbot: QtBot, tmp_path: Path, transport: Transport) -> tuple[AiLabPage, Rig]:
    context = AiLabContext(
        trades=lambda: TRADES,
        strategies=StrategySettingsSource(tmp_path),
        execution=ExecutionSettingsSource(tmp_path),
        export_dir=tmp_path,
    )
    page = AiLabPage(context)
    qtbot.addWidget(page)
    rig = Rig(tmp_path, transport)
    page.attach_llm(rig.llm)
    page.llm_panel.start_job = lambda job: job()
    return page, rig


def turn_on(panel: LlmPanel) -> bool:
    panel.enabled_box.setChecked(True)
    panel.key.setText(KEY)
    panel.input_price.setValue(0.5)
    panel.output_price.setValue(2.0)
    return panel.save()


def test_without_a_connection_nothing_is_asked(qtbot: QtBot) -> None:
    panel = LlmPanel()
    qtbot.addWidget(panel)
    assert not panel.ask_button.isEnabled() and not panel.save_button.isEnabled()
    assert panel.ask_ai() is False and panel.save() is False
    assert "needs the local database" in panel.status.text()


def test_it_is_off_until_saved_on(qtbot: QtBot, tmp_path: Path) -> None:
    transport = Transport()
    page, rig = page_with_llm(qtbot, tmp_path, transport)
    panel = page.llm_panel
    assert panel.status.text().startswith("Off")
    assert not panel.enabled_box.isChecked() and panel.ask_button.isEnabled()
    assert panel.ask_ai() is False and "off" in panel.status.text()
    assert transport.bodies == []


def test_saving_keeps_the_key_in_the_credential_store(qtbot: QtBot, tmp_path: Path) -> None:
    page, rig = page_with_llm(qtbot, tmp_path, Transport())
    panel = page.llm_panel
    assert turn_on(panel) is True
    assert rig.store.get(NAME) == KEY
    saved = (tmp_path / LLM_FILE_NAME).read_text(encoding="utf-8")
    assert KEY not in saved and '"enabled": true' in saved
    assert panel.key.text() == "" and "saved" in panel.key.placeholderText()
    assert rig.audits == ["llm settings changed", "llm api key saved"]
    assert panel.status.text().startswith("On: gpt-4o-mini")
    assert panel.forget_key() is True and rig.store.get(NAME) is None
    assert rig.audits[-1] == "llm api key removed"


def test_a_plain_http_endpoint_is_refused(qtbot: QtBot, tmp_path: Path) -> None:
    page, rig = page_with_llm(qtbot, tmp_path, Transport())
    panel = page.llm_panel
    panel.enabled_box.setChecked(True)
    panel.url.setText("http://example.com/v1")
    assert panel.save() is False and "https" in panel.status.text()
    assert not (tmp_path / LLM_FILE_NAME).exists() and rig.audits == []


def test_the_answer_goes_to_step_two_and_is_checked(qtbot: QtBot, tmp_path: Path) -> None:
    transport = Transport()
    page, rig = page_with_llm(qtbot, tmp_path, transport)
    panel = page.llm_panel
    assert turn_on(panel)
    panel.question.setText("Why do I lose?")
    assert panel.ask_ai() is True
    qtbot.waitUntil(lambda: not panel.asking, timeout=10_000)
    body = transport.bodies[0].decode("utf-8")
    assert KEY not in body and "87654321" not in body
    assert "Strategy parameters" in body and "Why do I lose?" in body
    assert page.answer.toPlainText() == ANSWER
    assert page.suggestion is not None and page.suggestion.valid
    assert page.diff.rowCount() == 1 and page.verdict is None
    text = panel.status.text()
    assert "test-model" in text and "1200 in + 300 out tokens" in text
    assert "valid suggestion" in text and "Advice only" in text
    assert any("AI answer from test-model" in line for line in rig.logs)


def test_the_account_number_is_sent_only_when_ticked(qtbot: QtBot, tmp_path: Path) -> None:
    transport = Transport()
    page, _rig = page_with_llm(qtbot, tmp_path, transport)
    panel = page.llm_panel
    panel.login.setChecked(True)
    assert turn_on(panel)
    assert panel.ask_ai() is True
    qtbot.waitUntil(lambda: not panel.asking, timeout=10_000)
    assert "Account number: 87654321" in transport.bodies[0].decode("utf-8")


def test_a_failure_is_shown_without_the_key(qtbot: QtBot, tmp_path: Path) -> None:
    transport = Transport(LlmError(f"HTTP 401: invalid key {KEY}"))
    page, rig = page_with_llm(qtbot, tmp_path, transport)
    panel = page.llm_panel
    assert turn_on(panel)
    assert panel.ask_ai() is True
    qtbot.waitUntil(lambda: not panel.asking, timeout=10_000)
    assert panel.status.text().startswith("The AI request failed: HTTP 401")
    assert KEY not in panel.status.text()
    assert all(KEY not in line for line in rig.logs)
    assert page.answer.toPlainText() == "" and panel.ask_button.isEnabled()


def test_the_main_window_has_the_card(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(UiPrefs(), tmp_path)
    qtbot.addWidget(window)
    lab = window.findChild(AiLabPage)
    assert isinstance(lab, AiLabPage) and lab is window.ai_lab_page
    assert isinstance(lab.llm_panel, LlmPanel) and lab.llm_panel.llm is None
