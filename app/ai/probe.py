"""Test connection (AI Desk spec 8): one tiny request that shows whether the key, the
model and JSON output work, how long it takes and what one desk cycle would cost.

The request carries no account data at all: a fixed health-check question only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.ai.cost import Usage, call_cost
from app.ai.providers import ApiStyle
from app.ai.transport import AiCallError, AiClient, CallOptions, OutputFormat, plain_text

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "model": {"type": "string"}},
    "required": ["ok", "model"],
    "additionalProperties": False,
}
PROBE_MESSAGES = (
    {"role": "system", "content": "You answer health checks with one JSON object."},
    {
        "role": "user",
        "content": 'Reply with the JSON object {"ok": true, "model": "<your model name>"}.',
    },
)
PROBE_MAX_TOKENS = 400
# One desk cycle (AI Desk spec 8.2): about 5000 input, 800 output and up to 1500 reasoning.
CYCLE_USAGE = Usage(input_tokens=5000, output_tokens=2300)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    text: str  # one line for the page
    model: str = ""
    seconds: float = 0.0
    json_works: bool = False
    options: CallOptions | None = None
    cycle_cost: float = 0.0


def _answer_ok(json_text: str | None) -> bool:
    if json_text is None:
        return False
    try:
        data = json.loads(json_text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("ok") is True


def cost_text(client: AiClient, cycle: float) -> str:
    if not client.prices.known:
        return "cost unknown (set the prices)"
    return f"one desk cycle costs about ${cycle:.3f}"


def probe(client: AiClient) -> ProbeResult:
    try:
        answer = client.complete(
            PROBE_MESSAGES,
            schema=PROBE_SCHEMA,
            max_tokens=PROBE_MAX_TOKENS,
        )
    except AiCallError as error:
        return ProbeResult(False, plain_text(error, client.settings.base_url))
    works = answer.options.output is not OutputFormat.TEXT and _answer_ok(answer.json_text)
    cycle = call_cost(client.prices, CYCLE_USAGE)
    style = "Responses" if answer.options.style is ApiStyle.RESPONSES else "Chat Completions"
    json_state = "JSON works" if works else "JSON only from plain text"
    text = (
        f"Connected: {answer.model} answered in {answer.seconds:.1f}s on {style}; "
        f"{json_state}; {cost_text(client, cycle)}."
    )
    return ProbeResult(
        ok=True,
        text=text,
        model=answer.model,
        seconds=answer.seconds,
        json_works=works,
        options=answer.options,
        cycle_cost=cycle,
    )
