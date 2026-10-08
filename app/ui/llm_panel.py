"""The AI Lab's optional "Ask AI" card (spec C13): the user's own OpenAI-compatible endpoint.

Off by default. Saving stores the endpoint, model and prices in the profile and the API key
in Windows Credential Manager (never in a file or a log). Asking sends only the compact
summary from `app.analytics.llm_client`; the answer goes to the AI Lab's answer box, so the
usual check, backtest and Paper-only activation decide what happens with it.

Phase 17a (docs/AI_DESK.md section 8) adds the provider presets (xAI Grok first), the API
style, the reasoning effort, the cached-input price and Test connection: one tiny fixed
question that shows the model, the latency, whether JSON works and what a desk cycle costs.

Phase 18a (docs/AI_LAB_AGENT.md section 9): every setting lives in the AI Lab settings
window (the gear on the page); the card itself keeps one status line, the question and
Ask AI.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ai.probe import ProbeResult, probe
from app.ai.providers import (
    CUSTOM,
    PRESETS,
    REASONING_EFFORTS,
    STYLE_NAMES,
    find_preset,
    matching_preset,
)
from app.ai.settings import (
    AiSettings,
    AiSettingsSource,
    connection_of,
    learned_options,
    with_learned,
)
from app.ai.transport import AiClient, CallOptions, host_of
from app.analytics.ai_export import ExportData
from app.analytics.llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_QUESTION,
    LlmAnswer,
    LlmContext,
    LlmError,
    LlmSettings,
    ask,
    build_messages,
    compact_summary,
    local_endpoint,
    redact,
    url_problem,
    usage_text,
)
from app.core.credentials import CredentialError, read_password, save_password
from app.ui.pages import card_frame, styled_label

NOTE = (
    "Off by default. Uses your own API key with any OpenAI-compatible endpoint (or a local "
    "server). Only a short summary is sent: statistics, breakdowns, costs and the strategy "
    "settings, never a password, the API key or the trade list. The answer is advice: it "
    "goes to step 2 and nothing changes until you test and activate it."
)
NO_CONTEXT = "The AI connection needs the local database and the settings."
SETTINGS_TITLE = "AI Lab settings"
SETTINGS_TIP = "Provider, model, API key, prices, reasoning and Test connection"
Job = Callable[[], None]


def _start_thread(job: Job) -> None:
    threading.Thread(target=job, name="ai-lab-llm", daemon=True).start()


def _no_data(now: float) -> ExportData | None:
    return None


def _ignore(text: str) -> str:
    return ""


def _price_box(name: str, tip: str = "USD per one million tokens (0 = unknown)") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setObjectName(name)
    box.setRange(0.0, 1000.0)
    box.setDecimals(3)
    box.setPrefix("$")
    box.setToolTip(tip)
    return box


def _combo(name: str, accessible: str, items: list[tuple[str, str]]) -> QComboBox:
    box = QComboBox()
    box.setObjectName(name)
    box.setAccessibleName(accessible)
    for text, value in items:
        box.addItem(text, value)
    return box


def _select(box: QComboBox, value: str) -> None:
    index = box.findData(value)
    box.setCurrentIndex(index if index >= 0 else 0)


def _data(box: QComboBox) -> str:
    value = box.currentData()
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class _Tested:
    result: ProbeResult
    learned: dict[str, CallOptions]


class _Bridge(QObject):
    answered = Signal(object)
    failed = Signal(str)
    tested = Signal(object)


class LlmPanel(QWidget):
    def __init__(
        self,
        data: Callable[[float], ExportData | None] = _no_data,
        deliver: Callable[[str], str] = _ignore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data = data
        self.deliver = deliver
        self.llm: LlmContext | None = None
        self.ai_source: AiSettingsSource | None = None
        self.start_job: Callable[[Job], None] = _start_thread
        self._asking = False
        self.bridge = _Bridge()
        self.bridge.answered.connect(self.show_answer, Qt.ConnectionType.QueuedConnection)
        self.bridge.failed.connect(self.show_failure, Qt.ConnectionType.QueuedConnection)
        self.bridge.tested.connect(self.show_test, Qt.ConnectionType.QueuedConnection)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card, layout = card_frame()
        outer.addWidget(card)
        layout.addWidget(styled_label("Or ask your own AI from here (optional)", "heading"))
        self.summary = styled_label(NO_CONTEXT, "muted", wrap=True)
        self.summary.setObjectName("AiLlmSummary")
        layout.addWidget(self.summary)
        self.question = QLineEdit()
        self.question.setObjectName("AiLlmQuestion")
        self.question.setPlaceholderText(DEFAULT_QUESTION)
        self.ask_button = QPushButton("Ask AI")
        self.ask_button.setObjectName("AiLlmAsk")
        self.ask_button.setProperty("variant", "accent")
        self.ask_button.clicked.connect(self.ask_ai)
        self.settings_button = QPushButton("AI settings")
        self.settings_button.setObjectName("AiLlmOpenSettings")
        self.settings_button.setToolTip(SETTINGS_TIP)
        self.settings_button.clicked.connect(self.open_settings)
        self._row(
            layout,
            styled_label("Question", "muted"),
            self.question,
            self.ask_button,
            self.settings_button,
        )
        self.status = styled_label(NO_CONTEXT, "muted", wrap=True)
        self.status.setObjectName("AiLlmStatus")
        layout.addWidget(self.status)
        self.settings_dialog = self._build_dialog()
        self.show_settings()

    def _build_dialog(self) -> QDialog:
        """Every connection setting, in one window opened from the page's gear."""
        dialog = QDialog(self)
        dialog.setObjectName("AiLabSettingsDialog")
        dialog.setWindowTitle(SETTINGS_TITLE)
        dialog.setMinimumWidth(760)
        dialog_layout = QVBoxLayout(dialog)
        card, layout = card_frame()
        dialog_layout.addWidget(card)
        layout.addWidget(styled_label("AI connection", "heading"))
        layout.addWidget(styled_label(NOTE, "muted", wrap=True))
        presets = [("Custom", CUSTOM), *((preset.name, preset.key) for preset in PRESETS)]
        self.preset = _combo("AiLlmPreset", "Provider preset", presets)
        self.preset.activated.connect(self.apply_preset)
        self.enabled_box = QCheckBox("Use my AI endpoint")
        self.enabled_box.setObjectName("AiLlmEnabled")
        self.url = QLineEdit()
        self.url.setObjectName("AiLlmUrl")
        self.url.setPlaceholderText(DEFAULT_BASE_URL)
        self.model = QLineEdit()
        self.model.setObjectName("AiLlmModel")
        self.model.setPlaceholderText(DEFAULT_MODEL)
        self._row(
            layout,
            self.enabled_box,
            styled_label("Preset", "muted"),
            self.preset,
            styled_label("Endpoint", "muted"),
            self.url,
            styled_label("Model", "muted"),
            self.model,
        )
        self.key = QLineEdit()
        self.key.setObjectName("AiLlmKey")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_price = _price_box("AiLlmInputPrice")
        self.output_price = _price_box("AiLlmOutputPrice")
        self.login = QCheckBox("Send the account number")
        self.login.setObjectName("AiLlmLogin")
        self._row(
            layout,
            styled_label("API key", "muted"),
            self.key,
            styled_label("Price in / out per 1M tokens", "muted"),
            self.input_price,
            self.output_price,
            self.login,
        )
        styles = [(name, style.value) for style, name in STYLE_NAMES.items()]
        self.style_box = _combo("AiLlmStyle", "API style", styles)
        self.style_box.setToolTip(
            "Auto tries Chat Completions and moves to Responses when the model needs it.",
        )
        efforts = [(effort.capitalize() or "Default", effort) for effort in REASONING_EFFORTS]
        self.reasoning = _combo("AiLlmReasoning", "Reasoning effort", efforts)
        self.reasoning.setToolTip("For models that think first; low keeps calls fast and cheap.")
        self.cached_price = _price_box(
            "AiLlmCachedPrice",
            "USD per one million cached input tokens (0 = the input price)",
        )
        self._row(
            layout,
            styled_label("API style", "muted"),
            self.style_box,
            styled_label("Reasoning", "muted"),
            self.reasoning,
            styled_label("Cached input per 1M", "muted"),
            self.cached_price,
        )
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("AiLlmSave")
        self.save_button.setProperty("variant", "primary")
        self.save_button.clicked.connect(self.save)
        self.test_button = QPushButton("Test connection")
        self.test_button.setObjectName("AiLlmTest")
        self.test_button.setToolTip("Sends one tiny fixed question, no account data.")
        self.test_button.clicked.connect(self.check_connection)
        self.forget_button = QPushButton("Remove key")
        self.forget_button.setObjectName("AiLlmForget")
        self.forget_button.clicked.connect(self.forget_key)
        close = QPushButton("Close")
        close.setObjectName("AiLlmClose")
        close.clicked.connect(dialog.close)
        buttons = QHBoxLayout()
        for button in (self.save_button, self.test_button, self.forget_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.dialog_status = styled_label("", "muted", wrap=True)
        self.dialog_status.setObjectName("AiLlmDialogStatus")
        layout.addWidget(self.dialog_status)
        return dialog

    @staticmethod
    def _row(layout: QVBoxLayout, *widgets: QWidget) -> None:
        row = QHBoxLayout()
        for widget in widgets:
            row.addWidget(widget)
        layout.addLayout(row)

    def _say(self, text: str) -> None:
        """One status for the card and the settings window."""
        self.status.setText(text)
        self.dialog_status.setText(text)

    @property
    def asking(self) -> bool:
        return self._asking

    def open_settings(self) -> None:
        """The AI Lab settings window, with the saved values (unsaved edits are dropped)."""
        if not self.settings_dialog.isVisible():
            self.show_settings()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def attach(self, llm: LlmContext) -> None:
        self.llm = llm
        self.ai_source = AiSettingsSource(llm.source.directory)
        self.show_settings()

    def has_key(self) -> bool:
        llm = self.llm
        if llm is None:
            return False
        try:
            return bool(read_password(llm.credentials, llm.key_name))
        except CredentialError:
            return False

    def ai_settings(self) -> AiSettings:
        return self.ai_source.settings if self.ai_source is not None else AiSettings()

    def show_settings(self) -> None:
        llm = self.llm
        settings = llm.source.settings if llm is not None else LlmSettings()
        ai = self.ai_settings()
        self.enabled_box.setChecked(settings.enabled)
        self.url.setText(settings.base_url)
        self.model.setText(settings.model)
        self.input_price.setValue(settings.input_price)
        self.output_price.setValue(settings.output_price)
        self.cached_price.setValue(ai.cached_input_price)
        self.login.setChecked(settings.include_login)
        _select(self.preset, matching_preset(settings.base_url))
        _select(self.style_box, ai.api_style)
        _select(self.reasoning, ai.reasoning_effort)
        saved = self.has_key()
        self.key.clear()
        self.key.setPlaceholderText(
            "saved (type a new one to replace it)" if saved else "kept in Credential Manager",
        )
        if llm is None:
            self.summary.setText(NO_CONTEXT)
            self._say(NO_CONTEXT)
        elif not settings.enabled:
            self.summary.setText("Off. Turn it on in AI settings (the gear at the top).")
            self._say("Off: nothing is ever sent.")
        else:
            local = local_endpoint(settings.base_url)
            key = "" if saved or local else " Save your API key."
            where = host_of(settings.base_url)
            state = "key saved" if saved else "local, no key" if local else "no key yet"
            self.summary.setText(f"On: {settings.model} at {where}, {state}.")
            self._say(f"On: {settings.model}. Nothing is sent until you press Ask AI.{key}")
        self._update()

    def apply_preset(self, index: int) -> None:
        """A preset only fills the fields; every field stays editable."""
        value = self.preset.itemData(index)
        preset = find_preset(value if isinstance(value, str) else "")
        if preset is None:
            return
        self.url.setText(preset.base_url)
        self.model.setText(preset.model)
        self.input_price.setValue(preset.input_price)
        self.cached_price.setValue(preset.cached_input_price)
        self.output_price.setValue(preset.output_price)
        self._say(preset.note or f"{preset.name}: check the fields and press Save.")

    def form_settings(self) -> LlmSettings | str:
        """The settings in the form, or why they cannot be saved."""
        current = self.llm.source.settings if self.llm is not None else LlmSettings()
        values: dict[str, Any] = current.model_dump()
        values.update(
            enabled=self.enabled_box.isChecked(),
            base_url=self.url.text().strip() or DEFAULT_BASE_URL,
            model=self.model.text().strip() or DEFAULT_MODEL,
            input_price=self.input_price.value(),
            output_price=self.output_price.value(),
            include_login=self.login.isChecked(),
        )
        try:
            found = LlmSettings.model_validate(values)
        except ValueError as error:
            return f"Check the settings: {error}"
        problem = url_problem(found.base_url) if found.enabled else ""
        return problem or found

    def form_ai(self) -> AiSettings:
        values: dict[str, Any] = self.ai_settings().model_dump()
        values.update(
            preset=_data(self.preset) or CUSTOM,
            api_style=_data(self.style_box) or "auto",
            reasoning_effort=_data(self.reasoning),
            cached_input_price=self.cached_price.value(),
        )
        return AiSettings.model_validate(values)

    def save(self) -> bool:
        llm = self.llm
        if llm is None:
            self._say(NO_CONTEXT)
            return False
        found = self.form_settings()
        if isinstance(found, str):
            self._say(found)
            return False
        key = self.key.text().strip()
        before = llm.source.settings
        ai_before = self.ai_settings()
        try:
            ai = self.form_ai()
            if key:
                save_password(llm.credentials, llm.key_name, key)
            llm.source.save(found)
            if self.ai_source is not None:
                self.ai_source.save(ai)
        except (CredentialError, OSError, ValueError) as error:
            self._say(f"Not saved: {redact(str(error), (key,))}")
            return False
        llm.record(
            "llm settings changed",
            {**before.model_dump(mode="json"), **ai_before.model_dump(exclude={"learned"})},
            {**found.model_dump(mode="json"), **ai.model_dump(exclude={"learned"})},
        )
        if key:
            llm.record("llm api key saved", "", llm.key_name)
        state = "on" if found.enabled else "off"
        llm.log("INFO", f"AI connection {state}: {found.model} at {found.base_url}")
        self.show_settings()
        return True

    def forget_key(self) -> bool:
        llm = self.llm
        if llm is None:
            return False
        try:
            llm.credentials.delete(llm.key_name)
        except CredentialError as error:
            self._say(f"The key was not removed: {error}")
            return False
        llm.record("llm api key removed", llm.key_name, "")
        self.show_settings()
        self._say("The API key was removed.")
        return True

    def check_connection(self) -> bool:
        """Test connection with the fields as they are now (nothing is saved by it)."""
        llm = self.llm
        if llm is None:
            self._say(NO_CONTEXT)
            return False
        if self._asking:
            return False
        found = self.form_settings()
        if isinstance(found, str):
            self._say(found)
            return False
        problem = url_problem(found.base_url)
        if problem:
            self._say(problem)
            return False
        typed = self.key.text().strip()
        try:
            key = typed or read_password(llm.credentials, llm.key_name) or ""
        except CredentialError as error:
            self._say(f"The API key could not be read: {error}")
            return False
        if not key and not local_endpoint(found.base_url):
            self._say("Type or save your API key first.")
            return False
        ai = self.form_ai()
        client = AiClient(
            connection_of(found, ai),
            key,
            transport=llm.transport,
            learned=learned_options(ai),
        )
        self._asking = True
        host = host_of(found.base_url)
        self._say(f"Testing {found.model} at {host}: one tiny question, no account data.")
        self._update()

        def job() -> None:
            try:
                result = probe(client)
            except Exception as error:
                text = redact(f"Test failed: {type(error).__name__}: {error}", (key,))
                result = ProbeResult(False, text)
            self.bridge.tested.emit(_Tested(result, dict(client.learned)))

        llm.log("INFO", f"AI test connection to {found.model} at {host}")
        self.start_job(job)
        return True

    def show_test(self, tested: object) -> None:
        self._asking = False
        if isinstance(tested, _Tested):
            result = tested.result
            self._say(result.text)
            if self.llm is not None:
                level = "INFO" if result.ok else "WARNING"
                self.llm.log(level, f"AI test connection: {result.text}")
            if result.ok and self.ai_source is not None:
                with contextlib.suppress(OSError):
                    self.ai_source.save(with_learned(self.ai_source.settings, tested.learned))
        self._update()

    def ask_ai(self) -> bool:
        llm = self.llm
        if llm is None:
            self._say(NO_CONTEXT)
            return False
        if self._asking:
            return False
        settings = llm.source.settings
        if not settings.enabled:
            self._say("The AI connection is off: turn it on in AI settings and press Save.")
            return False
        problem = url_problem(settings.base_url)
        if problem:
            self._say(problem)
            return False
        try:
            key = read_password(llm.credentials, llm.key_name) or ""
        except CredentialError as error:
            self._say(f"The API key could not be read: {error}")
            return False
        if not key and not local_endpoint(settings.base_url):
            self._say("Save your API key first (AI settings).")
            return False
        now = time.time()
        try:
            data = self.data(now)
            if data is None:
                self._say(NO_CONTEXT)
                return False
            login = llm.login() if settings.include_login else None
            summary = compact_summary(data, now, login=login, secrets=(key,))
        except Exception as error:
            self._say(f"The summary failed: {type(error).__name__}: {error}")
            return False
        messages = build_messages(summary, self.question.text())
        transport = llm.transport
        self._asking = True
        self._say(f"Asking {settings.model}: only the summary is sent...")
        self._update()

        def job() -> None:
            try:
                answer = ask(settings, key, messages, transport=transport)
            except LlmError as error:
                self.bridge.failed.emit(str(error))
                return
            except Exception as error:
                self.bridge.failed.emit(redact(f"{type(error).__name__}: {error}", (key,)))
                return
            self.bridge.answered.emit(answer)

        llm.log("INFO", f"AI request to {settings.model}: {len(summary)} characters of summary")
        self.start_job(job)
        return True

    def show_answer(self, answer: object) -> None:
        self._asking = False
        if isinstance(answer, LlmAnswer):
            usage = usage_text(answer)
            if self.llm is not None:
                self.llm.log("INFO", f"AI answer from {answer.model}: {usage}")
            result = self.deliver(answer.text)
            self._say(
                f"Answer from {answer.model} in {answer.seconds:.0f}s ({usage}). {result} "
                "Advice only: nothing changes until you test and activate it.",
            )
        self._update()

    def show_failure(self, text: str) -> None:
        self._asking = False
        self._say(f"The AI request failed: {text}")
        if self.llm is not None:
            self.llm.log("WARNING", f"AI request failed: {text}")
        self._update()

    def _update(self) -> None:
        ready = self.llm is not None
        self.ask_button.setEnabled(ready and not self._asking)
        self.test_button.setEnabled(ready and not self._asking)
        for widget in (self.save_button, self.forget_button):
            widget.setEnabled(ready)
