"""The optional AI review from inside the app (spec C13), with the user's own endpoint.

Off by default. When the user turns it on, the AI Lab sends ONE compact summary (the
statistics, breakdowns, calibration, costs, rejected reasons, recent backtests and each
strategy's current parameters with their limits) to an OpenAI-compatible
`/chat/completions` endpoint. No trade list, no password, no API key, and the account
number only when the user ticks it. The key lives in Windows Credential Manager.

The answer is advice only: it lands in the AI Lab's answer box, where the normal checks,
the backtest comparison and the Paper-only activation apply. Every request logs its
tokens and estimated cost.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.__version__ import __version__
from app.analytics import charts
from app.analytics.ai_export import ExportData, calibration
from app.analytics.breakdowns import KEYS, breakdown, label
from app.analytics.stats import compute_stats, stat_rows
from app.backtest.service import strategy_params
from app.core.credentials import CredentialStore
from app.core.paths import safe_profile_name
from app.observability.masking import MASK, MASKER
from app.strategies.registry import STRATEGIES

LLM_FILE_NAME = "llm.json"
KEY_SUFFIX = "llm-api-key"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
SUMMARY_LIMIT = 12_000
BREAKDOWN_ROWS = 8
ERROR_TEXT_LIMIT = 300
MILLION = 1_000_000.0
# Python's own "Python-urllib/3.11" is refused by many endpoints behind Cloudflare
# (error 1010, "browser signature"; PC log of 4 October 2026), so the app names itself.
USER_AGENT = f"MT5TradingWorkstation/{__version__}"
HTTP_HINTS = {
    401: "The API key was refused: check it and save it again.",
    402: "No credit left: check your plan with the provider.",
    404: "Not found: check the endpoint (it usually ends in /v1) and the model name.",
    429: "Too many requests or no credit left: wait a little or check your plan.",
}
CLOUDFLARE_HINT = (
    "The provider's firewall (Cloudflare) refused this app. Ask the provider to allow API "
    "clients, or use another endpoint."
)
DEFAULT_QUESTION = "Review this trading system and suggest careful parameter changes."
SYSTEM_PROMPT = """You review an automated MetaTrader 5 trading system from a compact
summary (there is no trade list). Be skeptical: few trades, one good month or big outliers
are not an edge. Your answer is advice only: the user decides, and the app checks,
backtests and activates any change only in Paper mode.

Answer briefly: where it loses money, whether any edge looks real, whether the predicted
win probabilities are calibrated, whether costs eat the edge. Then end with ONE JSON block
in exactly this shape, using only the strategy and parameter names and the limits listed
under "Strategy parameters":

```json
{"changes": [{"strategy": "trend_pullback", "params": {"min_adx_h1": 25},
  "reason": "why, with numbers from the summary", "expected_impact": "what should change"}]}
```

Few parameters per change. If the data is too thin, say so and return {"changes": []}.
"""

Transport = Callable[[str, Mapping[str, str], bytes, float], bytes]


class LlmError(Exception):
    """The AI request could not be made or its answer could not be read."""


class LlmSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=300)
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=120)
    input_price: float = Field(default=0.0, ge=0.0, le=1000.0)  # USD per 1M input tokens
    output_price: float = Field(default=0.0, ge=0.0, le=1000.0)  # USD per 1M output tokens
    max_tokens: int = Field(default=1500, ge=100, le=16_000)
    timeout_seconds: float = Field(default=90.0, ge=5.0, le=600.0)
    include_login: bool = False


def load_llm_settings(directory: Path) -> LlmSettings:
    try:
        raw = json.loads((directory / LLM_FILE_NAME).read_text(encoding="utf-8"))
        return LlmSettings.model_validate(raw)
    except (OSError, ValueError):
        # Missing, unreadable, corrupt or invalid: off, the safe default.
        return LlmSettings()


def save_llm_settings(directory: Path, settings: LlmSettings) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / LLM_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class LlmSettingsSource:
    """The saved settings of one profile."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.settings = load_llm_settings(directory)

    def save(self, settings: LlmSettings) -> None:
        save_llm_settings(self.directory, settings)
        self.settings = settings


def key_name(profile: str) -> str:
    """The API key's name in the credential store."""
    return f"{safe_profile_name(profile)}/{KEY_SUFFIX}"


def _quiet(level: str, message: str) -> None:
    return None


def _no_record(action: str, before: Any, after: Any) -> None:
    return None


def _no_login() -> str | None:
    return None


def http_error_text(code: int, detail: str) -> str:
    """The endpoint's HTTP error, with a plain hint for the usual causes first."""
    if code in (403, 503) and "cloudflare" in detail.lower():
        hint = CLOUDFLARE_HINT
    else:
        hint = HTTP_HINTS.get(code, "")
    return f"HTTP {code}: {hint} {detail}" if hint else f"HTTP {code}: {detail}"


def urllib_post(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
    """POST with the standard library (the URL is checked by `url_problem` first)."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as error:
        detail = error.read()[:ERROR_TEXT_LIMIT].decode("utf-8", "replace")
        raise LlmError(http_error_text(error.code, detail)) from None
    except OSError as error:  # URLError, timeouts, TLS and socket errors
        raise LlmError(f"Could not reach the endpoint: {error}") from None


@dataclass
class LlmContext:
    """What the AI Lab needs for the optional request."""

    source: LlmSettingsSource
    credentials: CredentialStore
    key_name: str
    login: Callable[[], str | None] = field(default=_no_login)
    transport: Transport = field(default=urllib_post)
    log: Callable[[str, str], None] = field(default=_quiet)
    record: Callable[[str, Any, Any], None] = field(default=_no_record)


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int
    cost: float  # USD, NaN when no price is set


@dataclass(frozen=True)
class LlmAnswer:
    text: str
    model: str
    usage: LlmUsage
    seconds: float


def local_endpoint(base_url: str) -> bool:
    return (urlsplit(base_url.strip()).hostname or "") in LOCAL_HOSTS


def url_problem(base_url: str) -> str:
    """Why this base URL is refused, or "": https only, plain http only on this PC."""
    text = base_url.strip()
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
    except ValueError:
        return "The endpoint is not a valid URL."
    if not host:
        return "Enter the endpoint, for example https://api.openai.com/v1."
    if "@" in parts.netloc:
        return "Do not put a user name or password in the endpoint URL."
    if parts.query or parts.fragment:
        return "The endpoint must not have a query (?...) or a fragment (#...)."
    if parts.scheme == "https":
        return ""
    if parts.scheme == "http" and host in LOCAL_HOSTS:
        return ""
    return "Use https (plain http only for a local server on this PC)."


def endpoint(base_url: str) -> str:
    return base_url.strip().rstrip("/") + "/chat/completions"


def cost(settings: LlmSettings, prompt_tokens: int, completion_tokens: int) -> float:
    if settings.input_price == 0.0 and settings.output_price == 0.0:
        return math.nan
    return (
        prompt_tokens / MILLION * settings.input_price
        + completion_tokens / MILLION * settings.output_price
    )


def usage_text(answer: LlmAnswer) -> str:
    usage = answer.usage
    tokens = f"{usage.prompt_tokens} in + {usage.completion_tokens} out tokens"
    if math.isfinite(usage.cost):
        return f"{tokens}, about ${usage.cost:.4f}"
    return f"{tokens}, cost unknown (set the prices)"


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    """Mask the given secrets and every known secret format."""
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, MASK)
    return MASKER.mask(masked)


# The summary ------------------------------------------------------------------------------
def _number(value: object, digits: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "n/a"
    if not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}"


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M")


def _bound(value: object) -> str:
    return "" if value is None else str(value)


def limits(schema: Mapping[str, Any]) -> str:
    """A strategy's parameter limits in one line, from its JSON schema."""
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    parts: list[str] = []
    for name, spec in properties.items():
        if not isinstance(spec, Mapping):
            continue
        kind = str(spec.get("type", ""))
        low = spec.get("minimum", spec.get("exclusiveMinimum"))
        high = spec.get("maximum", spec.get("exclusiveMaximum"))
        if isinstance(spec.get("enum"), list):
            parts.append(f"{name} one of {'/'.join(str(item) for item in spec['enum'])}")
        elif low is not None or high is not None:
            parts.append(f"{name} {kind} {_bound(low)}..{_bound(high)}")
        else:
            parts.append(f"{name} {kind}".strip())
    return "; ".join(parts)


def _overview(data: ExportData, now: float, login: str | None) -> list[str]:
    lines = [
        "# Trading summary",
        "",
        f"- Made: {_iso(now)} UTC; scope: {data.scope}",
        f"- Closed trades: {len(data.trades)}; currency: {data.currency or 'unknown'}",
        f"- Operating mode now: {data.mode.label}",
    ]
    if login:
        lines.append(f"- Account number: {login}")
    return [*lines, ""]


def _stats(data: ExportData) -> list[str]:
    stats = compute_stats(data.trades, data.start_balance)
    lines = ["## Statistics", ""]
    lines += [f"- {name}: {value}" for name, value in stat_rows(stats, data.currency)]
    lines += [f"- Warning: {text}" for text in stats.warnings]
    return [*lines, ""]


def _breakdowns(data: ExportData) -> list[str]:
    lines = [f"## Breakdowns (largest {BREAKDOWN_ROWS} groups each)", ""]
    for name in KEYS:
        groups = sorted(breakdown(data.trades, name), key=lambda item: -item.trades)
        if not groups:
            continue
        lines.append(f"By {name}:")
        lines += [
            f"- {label(item.key)}: {item.trades} trades, win {item.win_rate * 100:.0f}%, "
            f"net {item.net_profit:+,.2f}, {_number(item.expectancy_r, 3)} R, "
            f"PF {_number(item.profit_factor)}"
            for item in groups[:BREAKDOWN_ROWS]
        ]
    if len(lines) == 2:
        lines.append("No closed trades.")
    return [*lines, ""]


def _quality(data: ExportData) -> list[str]:
    lines = ["## Probability calibration", ""]
    found = calibration(data.trades)
    lines += [
        f"- {key}: {count} trades, predicted {predicted * 100:.1f}%, won {actual * 100:.1f}%"
        for key, count, predicted, actual in found
    ]
    if not found:
        lines.append("- No predicted probabilities yet.")
    lines += ["", "## MFE/MAE and costs", ""]
    lines += [f"- {text}" for text in charts.excursions(data.trades).lines]
    lines += [f"- {text}" for text in charts.cost_analysis(data.trades).lines]
    lines += ["", "## Rejected signals (top reasons)", ""]
    lines += [f"- {reason}: {count}" for reason, count in data.rejected[:10]]
    if not data.rejected:
        lines.append("- None.")
    lines += ["", "## Recent backtests", ""]
    for title, metrics in list(data.backtests)[:5]:
        lines.append(
            f"- {title}: {metrics.get('trades', 0)} trades, "
            f"{_number(metrics.get('expectancy_r'), 3)} R per trade, "
            f"PF {_number(metrics.get('profit_factor'))}",
        )
    if not data.backtests:
        lines.append("- None saved.")
    return [*lines, ""]


def _params(data: ExportData) -> list[str]:
    lines = ["## Strategy parameters", ""]
    for name, kind in STRATEGIES.items():
        state = "on" if data.settings.entry(name).enabled else "off"
        current = json.dumps(strategy_params(data.settings, name), sort_keys=True)
        schema = kind.params_model.model_json_schema()
        lines += [
            f"### {name} ({state})",
            f"Current: {current}",
            f"Limits: {limits(schema) or 'none'}",
            "",
        ]
    return lines


def compact_summary(
    data: ExportData,
    now: float,
    *,
    login: str | None = None,
    secrets: Sequence[str] = (),
) -> str:
    """The only data that is sent: aggregates and settings, masked, at most a few KB."""
    lines = [
        *_overview(data, now, login),
        *_stats(data),
        *_breakdowns(data),
        *_quality(data),
        *_params(data),
    ]
    text = "\n".join(lines)
    if len(text) > SUMMARY_LIMIT:
        text = text[:SUMMARY_LIMIT] + "\n[the summary was cut to fit]"
    return redact(text, secrets)


def build_messages(summary: str, question: str = "") -> list[dict[str, str]]:
    asked = question.strip() or DEFAULT_QUESTION
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{asked}\n\n{summary}"},
    ]


# The request ------------------------------------------------------------------------------
def _count(usage: object, name: str) -> int:
    if isinstance(usage, Mapping):
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def parse_answer(raw: bytes, settings: LlmSettings, seconds: float) -> LlmAnswer:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise LlmError("The endpoint did not answer with JSON.") from None
    if not isinstance(payload, Mapping):
        raise LlmError("The endpoint's answer has an unknown shape.")
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise LlmError("The answer is empty.")
    usage = payload.get("usage")
    prompt = _count(usage, "prompt_tokens")
    completion = _count(usage, "completion_tokens")
    model = payload.get("model")
    return LlmAnswer(
        text=content.strip(),
        model=model if isinstance(model, str) and model else settings.model,
        usage=LlmUsage(prompt, completion, cost(settings, prompt, completion)),
        seconds=seconds,
    )


def ask(
    settings: LlmSettings,
    key: str,
    messages: Sequence[Mapping[str, str]],
    *,
    transport: Transport = urllib_post,
    clock: Callable[[], float] = time.monotonic,
) -> LlmAnswer:
    """One chat completion. Raises LlmError (never with the key in its text)."""
    if not settings.enabled:
        raise LlmError("The AI connection is off.")
    problem = url_problem(settings.base_url)
    if problem:
        raise LlmError(problem)
    secret = key.strip()
    if not secret and not local_endpoint(settings.base_url):
        raise LlmError("No API key is saved.")
    body = json.dumps(
        {
            "model": settings.model,
            "messages": [dict(message) for message in messages],
            "max_tokens": settings.max_tokens,
            "temperature": 0.2,
        },
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    started = clock()
    try:
        raw = transport(endpoint(settings.base_url), headers, body, settings.timeout_seconds)
    except LlmError as error:
        raise LlmError(redact(str(error), (secret,))[:ERROR_TEXT_LIMIT]) from None
    except Exception as error:
        text = redact(f"{type(error).__name__}: {error}", (secret,))
        raise LlmError(text[:ERROR_TEXT_LIMIT]) from None
    return parse_answer(raw, settings, clock() - started)
