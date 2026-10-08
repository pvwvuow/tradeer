"""The AI Desk's answer (AI Desk spec 3.2): a strict JSON schema for the service and the
pydantic models that check what comes back.

The JSON schema follows the strict structured-output rules (every property required, no
extra properties, null as an explicit type), so it works with OpenAI, xAI and most
compatible services. Ranges are checked by the models, not by the schema, because not every
service accepts `minimum` and `maximum`. Over-long texts are cut, not refused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

MAX_ERRORS = 8


def _cut(limit: int) -> Callable[[object], object]:
    def cut(value: object) -> object:
        return value[:limit] if isinstance(value, str) else value

    return cut


Thesis = Annotated[str, BeforeValidator(_cut(400))]
Reason = Annotated[str, BeforeValidator(_cut(400))]
Note = Annotated[str, BeforeValidator(_cut(200))]
Invalidation = Annotated[str, BeforeValidator(_cut(200))]
Price = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Percent = Annotated[int, Field(ge=0, le=100)]


class MarketView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(min_length=1, max_length=32)
    bias: Literal["bullish", "bearish", "neutral"]
    confidence: Percent
    levels: list[Price] = Field(default_factory=list, max_length=8)
    thesis: Thesis = ""


class Idea(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    order: Literal["market", "limit", "stop"]
    entry: Price
    sl: Price
    tp: Price
    expires_minutes: int = Field(ge=1, le=10_000)
    confidence: Percent
    invalidation: Invalidation = ""
    lessons: list[int] = Field(default_factory=list, max_length=12)
    reason: Reason = ""


class PositionNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket: int = Field(ge=0)
    action: Literal["hold", "tighten_sl", "partial", "close"]
    new_sl: Price | None = None
    partial_fraction: float | None = Field(default=None, allow_inf_nan=False)
    reason: Note = ""


class DeskAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market: list[MarketView] = Field(default_factory=list, max_length=20)
    ideas: list[Idea] = Field(default_factory=list, max_length=10)
    positions: list[PositionNote] = Field(default_factory=list, max_length=30)
    no_trade: Note = ""


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
STRING = {"type": "string"}
MARKET_SCHEMA = _object(
    {
        "symbol": STRING,
        "bias": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "confidence": INTEGER,
        "levels": {"type": "array", "items": NUMBER},
        "thesis": STRING,
    },
)
IDEA_SCHEMA = _object(
    {
        "symbol": STRING,
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "order": {"type": "string", "enum": ["market", "limit", "stop"]},
        "entry": NUMBER,
        "sl": NUMBER,
        "tp": NUMBER,
        "expires_minutes": INTEGER,
        "confidence": INTEGER,
        "invalidation": STRING,
        "lessons": {"type": "array", "items": INTEGER},
        "reason": STRING,
    },
)
POSITION_SCHEMA = _object(
    {
        "ticket": INTEGER,
        "action": {"type": "string", "enum": ["hold", "tighten_sl", "partial", "close"]},
        "new_sl": {"type": ["number", "null"]},
        "partial_fraction": {"type": ["number", "null"]},
        "reason": STRING,
    },
)
ANSWER_SCHEMA = _object(
    {
        "market": {"type": "array", "items": MARKET_SCHEMA},
        "ideas": {"type": "array", "items": IDEA_SCHEMA},
        "positions": {"type": "array", "items": POSITION_SCHEMA},
        "no_trade": STRING,
    },
)


def errors_text(error: ValidationError) -> str:
    """The first validation errors in one short line, for the repair message and the log."""
    parts = []
    for item in error.errors()[:MAX_ERRORS]:
        place = ".".join(str(part) for part in item["loc"]) or "answer"
        parts.append(f"{place}: {item['msg']}")
    more = len(error.errors()) - MAX_ERRORS
    if more > 0:
        parts.append(f"and {more} more")
    return "; ".join(parts)


def parse_desk_answer(json_text: str) -> DeskAnswer | str:
    """The checked answer, or the reasons it was refused as a whole."""
    try:
        return DeskAnswer.model_validate_json(json_text)
    except ValidationError as error:
        return errors_text(error)
