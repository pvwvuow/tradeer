"""Provider presets for the AI connection (AI Desk spec section 8).

A preset only fills the fields; every field stays editable, so any OpenAI-compatible
service works with its own base URL, API key and model name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class ApiStyle(StrEnum):
    AUTO = "auto"
    CHAT = "chat"
    RESPONSES = "responses"


STYLE_NAMES: dict[ApiStyle, str] = {
    ApiStyle.AUTO: "Auto",
    ApiStyle.CHAT: "Chat Completions",
    ApiStyle.RESPONSES: "Responses",
}
REASONING_EFFORTS: tuple[str, ...] = ("", "low", "medium", "high")
CUSTOM = "custom"


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    base_url: str
    model: str
    input_price: float  # USD per 1M input tokens
    cached_input_price: float  # USD per 1M cached input tokens, 0 = the input price
    output_price: float  # USD per 1M output tokens
    note: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="xai",
        name="xAI Grok",
        base_url="https://api.x.ai/v1",
        model="grok-4.6",
        input_price=2.0,
        cached_input_price=0.0,
        output_price=6.0,
        note="grok-4.7 may answer only on the Responses API: keep the API style on Auto.",
    ),
    Preset(
        key="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        input_price=0.15,
        cached_input_price=0.075,
        output_price=0.6,
    ),
    Preset(
        key="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        model="x-ai/grok-4.6",
        input_price=0.0,
        cached_input_price=0.0,
        output_price=0.0,
        note="One key for many companies: type the model as listed on openrouter.ai.",
    ),
    Preset(
        key="local",
        name="Local (Ollama, LM Studio)",
        base_url="http://localhost:11434/v1",
        model="llama3.1",
        input_price=0.0,
        cached_input_price=0.0,
        output_price=0.0,
        note="No key, no cost, no internet: the model runs on this PC.",
    ),
)


def find_preset(key: str) -> Preset | None:
    return next((preset for preset in PRESETS if preset.key == key), None)


def _host(url: str) -> str:
    try:
        return (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def matching_preset(base_url: str) -> str:
    """The preset whose service this base URL belongs to, or `CUSTOM`."""
    host = _host(base_url)
    if not host:
        return CUSTOM
    for preset in PRESETS:
        if _host(preset.base_url) == host:
            return preset.key
    if host in ("127.0.0.1", "::1"):
        return "local"
    return CUSTOM
