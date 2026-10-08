"""Calls to any OpenAI-compatible service (AI Desk spec 8.1).

Both API styles: Chat Completions (`/chat/completions`) and Responses (`/responses`).
Structured output first (a JSON schema), then JSON mode, then plain text with the JSON
taken out of the answer; a parameter the service refuses (the temperature or the reasoning
effort of some models) is dropped. What worked is remembered per base URL and model, so
the next call goes straight to it. The key is only ever in the Authorization header and is
masked in every error text.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from app.ai.cost import Prices, Usage, call_cost
from app.ai.providers import ApiStyle
from app.analytics.llm_client import (
    ERROR_TEXT_LIMIT,
    USER_AGENT,
    LlmError,
    Transport,
    local_endpoint,
    redact,
    url_problem,
    urllib_post,
)

TEMPERATURE = 0.2
SCHEMA_NAME = "answer"
MAX_ATTEMPTS = 6
HTTP_CODE = re.compile(r"^HTTP (\d{3}):")
FORMAT_WORDS = ("response_format", "json_schema", "json_object", "text.format", "structured")
RESPONSES_WORDS = ("/responses", "responses api", "responses endpoint", "v1/responses")
Message = Mapping[str, str]


class OutputFormat(StrEnum):
    SCHEMA = "schema"
    JSON = "json"
    TEXT = "text"


class FailureKind(StrEnum):
    REFUSED = "refused"  # the settings were refused before anything was sent
    KEY = "key"
    CREDIT = "credit"
    MODEL = "model"
    RATE = "rate"
    SERVER = "server"
    NETWORK = "network"
    FORMAT = "format"
    OTHER = "other"


FAILURE_TEXT: dict[FailureKind, str] = {
    FailureKind.REFUSED: "The AI settings are not complete.",
    FailureKind.KEY: "The API key was refused: check it and save it again.",
    FailureKind.CREDIT: "No credit left with the provider: check your plan.",
    FailureKind.MODEL: "Unknown model or address: check the model name and the base URL.",
    FailureKind.RATE: "Too many requests: the desk waits and tries again.",
    FailureKind.SERVER: "The provider has a problem right now: the desk tries again later.",
    FailureKind.NETWORK: "No answer from {host}: check the internet or the VPN.",
    FailureKind.FORMAT: "The answer could not be read.",
    FailureKind.OTHER: "The request failed.",
}


class AiCallError(Exception):
    """An AI call failed; `text` is masked and short, `kind` says what to tell the user."""

    def __init__(self, kind: FailureKind, text: str, code: int = 0) -> None:
        super().__init__(text)
        self.kind = kind
        self.text = text
        self.code = code


def host_of(base_url: str) -> str:
    try:
        return urlsplit(base_url.strip()).hostname or base_url
    except ValueError:
        return base_url


def plain_text(error: AiCallError, base_url: str) -> str:
    """The failure in plain words for the page, with the service's own text after it."""
    words = FAILURE_TEXT[error.kind].replace("{host}", host_of(base_url))
    return f"{words} ({error.text})" if error.text else words


def classify(text: str) -> tuple[FailureKind, int]:
    """The kind and HTTP code of a transport error text ("HTTP 401: ...")."""
    low = text.lower()
    found = HTTP_CODE.match(text)
    if found is None:
        if "could not reach" in low or "timed out" in low:
            return FailureKind.NETWORK, 0
        return FailureKind.OTHER, 0
    code = int(found.group(1))
    if "cloudflare" in low:
        return FailureKind.OTHER, code
    if code in (401, 403):
        return FailureKind.KEY, code
    if code == 402:
        return FailureKind.CREDIT, code
    if code == 404:
        return FailureKind.MODEL, code
    if code == 429:
        return FailureKind.RATE, code
    if code >= 500:
        return FailureKind.SERVER, code
    return FailureKind.OTHER, code


@dataclass(frozen=True)
class Connection:
    """Everything one AI call needs, except the key."""

    base_url: str
    model: str
    timeout_seconds: float = 90.0
    input_price: float = 0.0
    cached_input_price: float = 0.0
    output_price: float = 0.0
    api_style: ApiStyle = ApiStyle.AUTO
    reasoning_effort: str = ""


@dataclass(frozen=True)
class CallOptions:
    style: ApiStyle
    output: OutputFormat
    temperature: bool = True
    reasoning: bool = True


@dataclass(frozen=True)
class Completion:
    text: str
    json_text: str | None
    model: str
    usage: Usage
    cost: float
    seconds: float
    options: CallOptions
    attempts: int


def endpoint_for(base_url: str, style: ApiStyle) -> str:
    path = "/responses" if style is ApiStyle.RESPONSES else "/chat/completions"
    return base_url.strip().rstrip("/") + path


def _strict_format(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": SCHEMA_NAME, "schema": dict(schema), "strict": True}


def request_body(
    settings: Connection,
    options: CallOptions,
    messages: Sequence[Message],
    schema: Mapping[str, Any] | None,
    max_tokens: int,
) -> dict[str, Any]:
    """The JSON body for one call in the chosen API style."""
    effort = settings.reasoning_effort if options.reasoning else ""
    turns = [{"role": item["role"], "content": item["content"]} for item in messages]
    body: dict[str, Any] = {"model": settings.model}
    if options.style is ApiStyle.RESPONSES:
        body["input"] = turns
        body["max_output_tokens"] = max_tokens
        if effort:
            body["reasoning"] = {"effort": effort}
        if schema is not None and options.output is OutputFormat.SCHEMA:
            body["text"] = {"format": {"type": "json_schema", **_strict_format(schema)}}
        elif schema is not None and options.output is OutputFormat.JSON:
            body["text"] = {"format": {"type": "json_object"}}
    else:
        body["messages"] = turns
        body["max_tokens"] = max_tokens
        if effort:
            body["reasoning_effort"] = effort
        if schema is not None and options.output is OutputFormat.SCHEMA:
            strict = {"type": "json_schema", "json_schema": _strict_format(schema)}
            body["response_format"] = strict
        elif schema is not None and options.output is OutputFormat.JSON:
            body["response_format"] = {"type": "json_object"}
    if options.temperature:
        body["temperature"] = TEMPERATURE
    return body


def _number(source: object, name: str) -> int:
    if isinstance(source, Mapping):
        value = source.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _detail(source: object, name: str) -> object:
    found: object = source.get(name) if isinstance(source, Mapping) else None
    return found


def chat_usage(usage: object) -> Usage:
    """Chat Completions usage. Some services count reasoning inside `completion_tokens`,
    others beside it; `total_tokens` tells which, so reasoning is paid exactly once."""
    prompt = _number(usage, "prompt_tokens")
    completion = _number(usage, "completion_tokens")
    total = _number(usage, "total_tokens")
    reasoning = _number(_detail(usage, "completion_tokens_details"), "reasoning_tokens")
    output = max(completion, total - prompt) if total else completion
    cached = _number(_detail(usage, "prompt_tokens_details"), "cached_tokens")
    return Usage(prompt, cached, output, reasoning)


def responses_usage(usage: object) -> Usage:
    """Responses usage: `output_tokens` already holds the reasoning tokens."""
    return Usage(
        _number(usage, "input_tokens"),
        _number(_detail(usage, "input_tokens_details"), "cached_tokens"),
        _number(usage, "output_tokens"),
        _number(_detail(usage, "output_tokens_details"), "reasoning_tokens"),
    )


def _chat_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, str):
        return content
    refusal = message.get("refusal") if isinstance(message, Mapping) else None
    if isinstance(refusal, str) and refusal:
        raise AiCallError(FailureKind.FORMAT, f"The model refused: {refusal[:200]}")
    return ""


def _responses_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    parts: list[str] = []
    output = payload.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        for piece in content if isinstance(content, list) else []:
            if isinstance(piece, Mapping) and piece.get("type") == "output_text":
                text = piece.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def parse_reply(style: ApiStyle, raw: bytes) -> tuple[str, str, Usage]:
    """(answer text, model that answered, usage) of one reply."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise AiCallError(FailureKind.FORMAT, "The service did not answer with JSON.") from None
    if not isinstance(payload, Mapping):
        raise AiCallError(FailureKind.FORMAT, "The answer has an unknown shape.")
    if style is ApiStyle.RESPONSES:
        text = _responses_text(payload)
        usage = responses_usage(payload.get("usage"))
    else:
        text = _chat_text(payload)
        usage = chat_usage(payload.get("usage"))
    if not text.strip():
        raise AiCallError(FailureKind.FORMAT, "The answer is empty.")
    model = payload.get("model")
    return text.strip(), model if isinstance(model, str) else "", usage


def extract_json(text: str) -> str | None:
    """The first complete JSON object in the text (code fences and commentary around it
    are ignored), or None. Strings and escapes are respected, so braces inside a reason
    do not end the object."""
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        json.loads(candidate)
                    except ValueError:
                        break
                    return candidate
        start = text.find("{", start + 1)
    return None


def _mentions(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)


def fallback(
    options: CallOptions,
    error: AiCallError,
    *,
    auto_style: bool,
    structured: bool,
) -> CallOptions | None:
    """The next thing to try after a refused request, or None (then the error stands)."""
    if error.kind not in (FailureKind.MODEL, FailureKind.OTHER) or error.code == 0:
        return None
    low = error.text.lower()
    if auto_style and options.style is ApiStyle.CHAT and _mentions(low, RESPONSES_WORDS):
        return replace(options, style=ApiStyle.RESPONSES)
    if error.code == 404:
        return None
    if options.temperature and "temperature" in low:
        return replace(options, temperature=False)
    if options.reasoning and "reasoning" in low:
        return replace(options, reasoning=False)
    if structured and options.output is OutputFormat.SCHEMA and _mentions(low, FORMAT_WORDS):
        return replace(options, output=OutputFormat.JSON)
    if structured and options.output is OutputFormat.JSON and _mentions(low, FORMAT_WORDS):
        return replace(options, output=OutputFormat.TEXT)
    return None


def memory_key(settings: Connection) -> str:
    return f"{settings.base_url.strip().rstrip('/')}|{settings.model.strip()}"


class AiClient:
    """One provider connection: the settings, the key and what worked for the model."""

    def __init__(
        self,
        settings: Connection,
        key: str,
        *,
        transport: Transport = urllib_post,
        clock: Callable[[], float] = time.monotonic,
        learned: dict[str, CallOptions] | None = None,
    ) -> None:
        self.settings = settings
        self.key = key.strip()
        self.transport = transport
        self.clock = clock
        self.learned: dict[str, CallOptions] = learned if learned is not None else {}

    @property
    def prices(self) -> Prices:
        settings = self.settings
        return Prices(settings.input_price, settings.cached_input_price, settings.output_price)

    def problem(self) -> str:
        """Why nothing can be sent with these settings, or ""."""
        found = url_problem(self.settings.base_url)
        if found:
            return found
        if not self.key and not local_endpoint(self.settings.base_url):
            return "No API key is saved."
        return ""

    def first_options(self, structured: bool) -> CallOptions:
        saved = self.learned.get(memory_key(self.settings))
        style = self.settings.api_style
        if saved is not None and (style is ApiStyle.AUTO or saved.style is style):
            if structured or saved.output is OutputFormat.TEXT:
                return saved
            return replace(saved, output=OutputFormat.TEXT)
        first = ApiStyle.CHAT if style is ApiStyle.AUTO else style
        output = OutputFormat.SCHEMA if structured else OutputFormat.TEXT
        return CallOptions(first, output)

    def _send(
        self,
        options: CallOptions,
        messages: Sequence[Message],
        schema: Mapping[str, Any] | None,
        max_tokens: int,
    ) -> bytes:
        body = request_body(self.settings, options, messages, schema, max_tokens)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        url = endpoint_for(self.settings.base_url, options.style)
        data = json.dumps(body).encode("utf-8")
        try:
            return self.transport(url, headers, data, self.settings.timeout_seconds)
        except LlmError as error:
            text = redact(str(error), (self.key,))[:ERROR_TEXT_LIMIT]
            kind, code = classify(text)
            raise AiCallError(kind, text, code) from None
        except Exception as error:
            text = redact(f"{type(error).__name__}: {error}", (self.key,))[:ERROR_TEXT_LIMIT]
            kind, code = classify(text)
            raise AiCallError(kind, text, code) from None

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: Mapping[str, Any] | None = None,
        max_tokens: int = 1500,
    ) -> Completion:
        """One answer. With a schema the answer must hold a JSON object (`json_text`)."""
        problem = self.problem()
        if problem:
            raise AiCallError(FailureKind.REFUSED, problem)
        structured = schema is not None
        options = self.first_options(structured)
        auto = self.settings.api_style is ApiStyle.AUTO
        started = self.clock()
        attempts = 0
        while True:
            attempts += 1
            try:
                raw = self._send(options, messages, schema, max_tokens)
                break
            except AiCallError as error:
                following = fallback(options, error, auto_style=auto, structured=structured)
                if following is None or attempts >= MAX_ATTEMPTS:
                    raise
                options = following
        text, model, usage = parse_reply(options.style, raw)
        json_text = extract_json(text) if structured else None
        if structured and json_text is None:
            raise AiCallError(FailureKind.FORMAT, "The answer holds no JSON object.")
        self.learned[memory_key(self.settings)] = options
        return Completion(
            text=text,
            json_text=json_text,
            model=model or self.settings.model,
            usage=usage,
            cost=call_cost(self.prices, usage),
            seconds=self.clock() - started,
            options=options,
            attempts=attempts,
        )
