"""Phase 18a (docs/AI_LAB_AGENT.md section 9): every AI setting is in one AI Lab settings
window behind the gear at the top of the page; the card keeps a one-line status."""

from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from tests.ui.test_ai_connection import ProbeTransport
from tests.ui.test_llm_panel import page_with_llm, turn_on


def test_the_gear_opens_the_settings_window(qtbot: QtBot, tmp_path: Path) -> None:
    page, _rig = page_with_llm(qtbot, tmp_path, ProbeTransport())  # type: ignore[arg-type]
    panel = page.llm_panel
    dialog = panel.settings_dialog
    assert not dialog.isVisible()
    for widget in (panel.url, panel.model, panel.key, panel.preset, panel.test_button):
        assert dialog.isAncestorOf(widget)
    assert not dialog.isAncestorOf(panel.ask_button)
    assert panel.summary.text().startswith("Off. Turn it on in AI settings")
    page.show()
    qtbot.mouseClick(page.settings_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible() and dialog.windowTitle() == "AI Lab settings"
    dialog.close()
    qtbot.mouseClick(panel.settings_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    dialog.close()


def test_saving_in_the_window_updates_the_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, _rig = page_with_llm(qtbot, tmp_path, ProbeTransport())  # type: ignore[arg-type]
    panel = page.llm_panel
    panel.open_settings()
    panel.url.setText("https://api.x.ai/v1")
    panel.model.setText("grok-4.6")
    assert turn_on(panel)
    summary = panel.summary.text()
    assert summary.startswith("On: grok-4.6 at ") and summary.endswith(", key saved.")
    assert "x.ai" in summary
    assert panel.dialog_status.text() == panel.status.text()
    assert panel.status.text().startswith("On: grok-4.6")
    panel.settings_dialog.close()


def test_reopening_drops_unsaved_edits(qtbot: QtBot, tmp_path: Path) -> None:
    page, _rig = page_with_llm(qtbot, tmp_path, ProbeTransport())  # type: ignore[arg-type]
    panel = page.llm_panel
    panel.open_settings()
    panel.model.setText("typed-but-not-saved")
    panel.settings_dialog.close()
    panel.open_settings()
    assert panel.model.text() != "typed-but-not-saved"
    panel.settings_dialog.close()
