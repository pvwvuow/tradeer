"""Form fields from a settings model (spec F3: "auto-generated params form").

Pure: reads the pydantic JSON schema, so the Strategies page builds its inputs (number,
whole number, on/off, choice, text) with the model's own limits and labels, and the model
still validates what the user typed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

KINDS = ("int", "float", "bool", "choice", "text")


@dataclass(frozen=True)
class ParamField:
    name: str
    kind: str  # one of KINDS
    label: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    pattern: str = ""

    def step(self) -> float:
        """A sensible increment for number inputs."""
        if self.kind == "int":
            return 1.0
        span = (self.maximum or 0.0) - (self.minimum or 0.0)
        return 0.05 if not math.isfinite(span) or span <= 10 else 0.5


def _limit(raw: Mapping[str, Any], inclusive: str, exclusive: str) -> float | None:
    value = raw.get(inclusive, raw.get(exclusive))
    return float(value) if isinstance(value, int | float) else None


def param_fields(model: type[BaseModel]) -> list[ParamField]:
    schema = model.model_json_schema()
    fields: list[ParamField] = []
    for name, raw in dict(schema.get("properties", {})).items():
        kind_name = raw.get("type")
        choices = tuple(str(value) for value in raw.get("enum", ()))
        if choices:
            kind = "choice"
        elif kind_name == "integer":
            kind = "int"
        elif kind_name == "number":
            kind = "float"
        elif kind_name == "boolean":
            kind = "bool"
        else:
            kind = "text"
        fields.append(
            ParamField(
                name=name,
                kind=kind,
                label=str(raw.get("description") or raw.get("title") or name),
                default=raw.get("default"),
                minimum=_limit(raw, "minimum", "exclusiveMinimum"),
                maximum=_limit(raw, "maximum", "exclusiveMaximum"),
                choices=choices,
                pattern=str(raw.get("pattern", "")),
            ),
        )
    return fields


def validate_params(
    model: type[BaseModel],
    values: Mapping[str, Any],
) -> tuple[BaseModel | None, list[str]]:
    """The validated settings, or None and one plain message per problem."""
    try:
        return model.model_validate(dict(values)), []
    except ValidationError as error:
        problems: list[str] = []
        for item in error.errors():
            where = ".".join(str(part) for part in item.get("loc", ())) or "settings"
            problems.append(f"{where}: {item.get('msg', 'invalid value')}")
        return None, problems
