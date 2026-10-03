"""The short "what changed" text of an audit line (spec E3).

The audit entry keeps the full before and after values; the line in `all.log` and
`logs/audit/` says in a few words what changed, so a log read by a person shows it directly:
`Audit: trading mode changed (paper \u2192 semi_auto)`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.observability.masking import MASK, is_sensitive_key

AUDIT_SUMMARY_CHARS = 300
AUDIT_MAX_CHANGES = 8
_ABSENT = object()


def change_summary(before: object, after: object) -> str:
    """What changed, in a few words, for the audit line (the entry keeps the full values).

    Two mappings give only the changed keys (`profile normal \u2192 custom`); other values give
    `before \u2192 after`, or the one that is set. Values of secret-looking keys are masked.
    """
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        old, new = _flat(before), _flat(after)
        keys = [
            key
            for key in dict.fromkeys([*old, *new])
            if old.get(key, _ABSENT) != new.get(key, _ABSENT)
        ]
        parts = [
            f"{key} {_value_text(key, old.get(key))} \u2192 {_value_text(key, new.get(key))}"
            for key in keys[:AUDIT_MAX_CHANGES]
        ]
        if len(keys) > AUDIT_MAX_CHANGES:
            parts.append(f"{len(keys) - AUDIT_MAX_CHANGES} more")
        text = ", ".join(parts) or "no change"
    else:
        old_text, new_text = _side_text(before), _side_text(after)
        text = f"{old_text} \u2192 {new_text}" if old_text and new_text else old_text or new_text
    if len(text) > AUDIT_SUMMARY_CHARS:
        text = text[: AUDIT_SUMMARY_CHARS - 3] + "..."
    return text


def _flat(values: Mapping[Any, Any], prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    for key, value in values.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flat(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _items_text(value: list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any]) -> str:
    texts = [str(item) for item in value]
    if isinstance(value, set | frozenset):
        texts.sort()
    return ", ".join(texts)


def _value_text(key: str, value: object) -> str:
    if is_sensitive_key(key.rsplit(".", 1)[-1]):
        return MASK
    if value is None:
        return "none"
    if isinstance(value, list | tuple | set | frozenset):
        return f"[{_items_text(value)}]"
    return str(value)


def _side_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={_value_text(key, item)}" for key, item in _flat(value).items())
    if isinstance(value, list | tuple | set | frozenset):
        return _items_text(value) or "none"
    return str(value)
