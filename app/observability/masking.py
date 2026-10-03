"""Secret masking for logs, crash reports, exports and debug bundles (spec D5, G4).

Three layers, applied to every log message, every structured field and every crash report:
1. Values registered at runtime (for example a password read from the keyring) are replaced
   wherever they appear.
2. Well-known token formats are replaced: GitHub, OpenAI-style `sk-` keys, JWTs (Supabase
   keys), Telegram bot tokens, AWS access key ids, Slack tokens, Bearer tokens.
3. `key=value` and `"key": "value"` pairs with a sensitive key, and passwords inside URLs.
Masking more than necessary is the accepted failure mode; leaking is not.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from typing import Any

MASK = "***"
MIN_SECRET_LENGTH = 6
MAX_DEPTH = 20

_KEY_WORDS = (
    r"passwords?|passwd|passphrase|pwd|secrets?|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|anon[_-]?key|service[_-]?role(?:[_-]?key)?|authorization|auth"
    r"|credentials?"
)

_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
)

_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")

_PAIR = re.compile(
    rf"(?i)(?P<key>[\"']?[\w-]*?(?:{_KEY_WORDS}|pin)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s\"',;&}\]]+)"
)

_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+):([^\s/@]+)@")

_SENSITIVE_KEY = re.compile(rf"(?i)(?:{_KEY_WORDS})(?:$|[^a-z])|(?:^|[^a-z])pin$")


def is_sensitive_key(key: str) -> bool:
    """True for field names such as `password`, `api_key`, `telegramToken` or `pin`."""
    return bool(_SENSITIVE_KEY.search(key))


def _mask_pair(match: re.Match[str]) -> str:
    value = match.group("value")
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return f"{match.group('key')}{value[0]}{MASK}{value[0]}"
    return f"{match.group('key')}{MASK}"


def _mask_bearer(match: re.Match[str]) -> str:
    return f"{match.group(1)} {MASK}"


class SecretMasker:
    """Thread-safe masker. Use the shared `MASKER` in the app; tests create their own."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._literals: tuple[str, ...] = ()

    def register(self, secret: str | None) -> None:
        """Mask this exact value everywhere from now on. Short values are ignored."""
        if not secret or len(secret) < MIN_SECRET_LENGTH:
            return
        with self._lock:
            if secret not in self._literals:
                values = {*self._literals, secret}
                self._literals = tuple(sorted(values, key=len, reverse=True))

    def forget(self, secret: str) -> None:
        with self._lock:
            self._literals = tuple(value for value in self._literals if value != secret)

    def mask(self, text: str) -> str:
        if not text:
            return text
        masked = text
        for literal in self._literals:
            if literal in masked:
                masked = masked.replace(literal, MASK)
        for pattern in _TOKEN_PATTERNS:
            masked = pattern.sub(MASK, masked)
        masked = _BEARER.sub(_mask_bearer, masked)
        masked = _PAIR.sub(_mask_pair, masked)
        return _URL_CREDENTIALS.sub(rf"\1:{MASK}@", masked)

    def mask_value(self, value: object, key: str | None = None, _depth: int = 0) -> Any:
        """A JSON-safe copy of `value` with every secret masked, recursively."""
        if key is not None and is_sensitive_key(key) and value not in (None, ""):
            return MASK
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return self.mask(value)
        if _depth >= MAX_DEPTH:
            return "<nested too deeply>"
        if isinstance(value, Mapping):
            return {
                str(name): self.mask_value(item, str(name), _depth + 1)
                for name, item in value.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [self.mask_value(item, None, _depth + 1) for item in value]
        if isinstance(value, bytes | bytearray):
            return f"<{len(value)} bytes>"
        return self.mask(str(value))


MASKER = SecretMasker()
