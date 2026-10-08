"""Phase 17a in the AI card (docs/AI_DESK.md section 8): presets fill the fields, the API
style and reasoning effort are saved, Test connection sends one tiny fixed question (no
account data, nothing saved) and reports in plain words."""

import json
from collections.abc import Mapping
from pathlib import Path

from pytestqt.qtbot import QtBot

from app.analytics.llm_client import LlmError
from tests.ui.test_llm_panel import KEY, NAME, page_with_llm, turn_on


class ProbeTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
        self.calls.append((url, body.decode("utf-8")))
        if self.error is not None:
            raise self.error
        payload = {
            "model": "grok-4.6",
            "choices": [{"message": {"content": '{"ok": true, "model": "grok-4.6"}'}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52},
        }
        return json.dumps(payload).encode("utf-8")


def test_the_grok_preset_and_test_connection(qtbot: QtBot, tmp_path: Path) -> None:
    transport = ProbeTransport()
    page, rig = page_with_llm(qtbot, tmp_path, transport)  # type: ignore[arg-type]
    panel = page.llm_panel
    panel.preset.setCurrentIndex(panel.preset.findData("xai"))
    panel.apply_preset(panel.preset.currentIndex())
    assert panel.url.text() == "https://api.x.ai/v1" and panel.model.text() == "grok-4.6"
    assert panel.input_price.value() == 2.0 and panel.output_price.value() == 6.0
    assert "Responses API" in panel.status.text()
    panel.key.setText(KEY)
    assert panel.check_connection() is True
    qtbot.waitUntil(lambda: not panel.asking, timeout=10_000)
    text = panel.status.text()
    assert text.startswith("Connected: grok-4.6") and "JSON works" in text and "$0.024" in text
    url, body = transport.calls[0]
    assert url == "https://api.x.ai/v1/chat/completions"
    assert KEY not in body and "Strategy parameters" not in body and "health checks" in body
    assert rig.store.get(NAME) is None and rig.audits == []
    saved = json.loads((tmp_path / "ai_desk.json").read_text(encoding="utf-8"))
    assert saved["learned"]["https://api.x.ai/v1|grok-4.6"]["style"] == "chat"


def test_style_and_reasoning_are_saved_with_the_connection(qtbot: QtBot, tmp_path: Path) -> None:
    page, _rig = page_with_llm(qtbot, tmp_path, ProbeTransport())  # type: ignore[arg-type]
    panel = page.llm_panel
    panel.style_box.setCurrentIndex(panel.style_box.findData("responses"))
    panel.reasoning.setCurrentIndex(panel.reasoning.findData("low"))
    panel.cached_price.setValue(0.5)
    assert turn_on(panel)
    saved = json.loads((tmp_path / "ai_desk.json").read_text(encoding="utf-8"))
    assert saved["api_style"] == "responses" and saved["reasoning_effort"] == "low"
    assert saved["cached_input_price"] == 0.5 and saved["mode"] == "off"
    assert KEY not in json.dumps(saved)
    page.llm_panel.show_settings()
    assert panel.style_box.currentData() == "responses" and panel.reasoning.currentData() == "low"


def test_a_refused_key_is_explained_without_the_key(qtbot: QtBot, tmp_path: Path) -> None:
    transport = ProbeTransport(LlmError(f"HTTP 401: invalid key {KEY}"))
    page, rig = page_with_llm(qtbot, tmp_path, transport)  # type: ignore[arg-type]
    panel = page.llm_panel
    panel.key.setText(KEY)
    assert panel.check_connection() is True
    qtbot.waitUntil(lambda: not panel.asking, timeout=10_000)
    assert panel.status.text().startswith("The API key was refused")
    assert KEY not in panel.status.text() and all(KEY not in line for line in rig.logs)
    assert panel.test_button.isEnabled()


def test_no_key_means_no_request(qtbot: QtBot, tmp_path: Path) -> None:
    transport = ProbeTransport()
    page, _rig = page_with_llm(qtbot, tmp_path, transport)  # type: ignore[arg-type]
    assert page.llm_panel.check_connection() is False
    assert "API key" in page.llm_panel.status.text() and transport.calls == []
