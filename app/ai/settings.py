"""The AI Desk settings of one profile (AI Desk spec 8), next to the AI connection.

The connection itself (base URL, model, prices, the key in the credential store) stays in
`app.analytics.llm_client`, shared with the AI Lab. This file adds what the AI Desk needs:
the API style, the reasoning effort, the cached-input price, the desk mode with its cadence
and caps, and what worked per model (so the next call goes straight to it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.providers import CUSTOM, ApiStyle
from app.ai.transport import CallOptions, Connection, OutputFormat
from app.analytics.llm_client import LlmSettings

AI_FILE_NAME = "ai_desk.json"
MAX_LEARNED = 20


class AiSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    preset: str = Field(default=CUSTOM, max_length=40)
    api_style: Literal["auto", "chat", "responses"] = "auto"
    reasoning_effort: Literal["", "low", "medium", "high"] = ""
    cached_input_price: float = Field(default=0.0, ge=0.0, le=1000.0)
    mode: Literal["off", "advisor", "copilot"] = "off"
    cadence_minutes: int = Field(default=30, ge=15, le=240)
    daily_cost_cap: float = Field(default=1.0, ge=0.0, le=100.0)
    daily_call_cap: int = Field(default=60, ge=1, le=1000)
    answer_language: Literal["en", "fa"] = "en"
    keep_history_days: int = Field(default=30, ge=1, le=365)
    learned: dict[str, dict[str, str]] = Field(default_factory=dict)


def load_ai_settings(directory: Path) -> AiSettings:
    try:
        raw = json.loads((directory / AI_FILE_NAME).read_text(encoding="utf-8"))
        return AiSettings.model_validate(raw)
    except (OSError, ValueError):
        return AiSettings()


def save_ai_settings(directory: Path, settings: AiSettings) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / AI_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class AiSettingsSource:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.settings = load_ai_settings(directory)

    def save(self, settings: AiSettings) -> None:
        save_ai_settings(self.directory, settings)
        self.settings = settings


def connection_of(llm: LlmSettings, ai: AiSettings) -> Connection:
    return Connection(
        base_url=llm.base_url,
        model=llm.model,
        timeout_seconds=llm.timeout_seconds,
        input_price=llm.input_price,
        cached_input_price=ai.cached_input_price,
        output_price=llm.output_price,
        api_style=ApiStyle(ai.api_style),
        reasoning_effort=ai.reasoning_effort,
    )


def options_to_dict(options: CallOptions) -> dict[str, str]:
    return {
        "style": options.style.value,
        "output": options.output.value,
        "temperature": "yes" if options.temperature else "no",
        "reasoning": "yes" if options.reasoning else "no",
    }


def options_from_dict(raw: dict[str, str]) -> CallOptions | None:
    try:
        return CallOptions(
            style=ApiStyle(raw.get("style", "")),
            output=OutputFormat(raw.get("output", "")),
            temperature=raw.get("temperature") != "no",
            reasoning=raw.get("reasoning") != "no",
        )
    except ValueError:
        return None


def learned_options(settings: AiSettings) -> dict[str, CallOptions]:
    found: dict[str, CallOptions] = {}
    for key, raw in settings.learned.items():
        options = options_from_dict(raw)
        if options is not None:
            found[key] = options
    return found


def with_learned(settings: AiSettings, learned: dict[str, CallOptions]) -> AiSettings:
    """The settings with what worked, newest entries kept when there are too many."""
    items = list(learned.items())[-MAX_LEARNED:]
    stored = {key: options_to_dict(options) for key, options in items}
    return settings.model_copy(update={"learned": stored})
