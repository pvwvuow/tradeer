"""Operating modes (spec B3). Paper is always the first-launch default."""

from __future__ import annotations

from enum import StrEnum


class OperatingMode(StrEnum):
    ANALYSIS_ONLY = "analysis_only"
    PAPER = "paper"
    SEMI_AUTO = "semi_auto"
    AUTO = "auto"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def places_real_orders(self) -> bool:
        return self in (OperatingMode.SEMI_AUTO, OperatingMode.AUTO)


_LABELS: dict[OperatingMode, str] = {
    OperatingMode.ANALYSIS_ONLY: "Analysis-only",
    OperatingMode.PAPER: "Paper",
    OperatingMode.SEMI_AUTO: "Semi-auto",
    OperatingMode.AUTO: "Auto",
}

DEFAULT_MODE = OperatingMode.PAPER
