"""Risk settings per profile (spec B4, C6): `risk.json`.

Three built-in profiles: Conservative (0.25% per trade), Normal (0.5%, the default) and
Prop-firm (the usual prop daily-loss and drawdown rules with a safety buffer). Editing a value
makes the profile "custom". The model's limits are hard caps: no setting can raise the risk
per trade above 1%, and a file that cannot be read gives the Normal profile, never a riskier
one.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RISK_FILE_NAME = "risk.json"
CUSTOM = "custom"


class RiskSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capital_basis: Literal["equity", "balance"] = Field(
        default="equity",
        description="Size from equity or balance",
    )
    risk_per_trade_percent: float = Field(
        default=0.5,
        gt=0,
        le=1,
        description="Risk per trade (% of capital)",
    )
    max_total_open_risk_percent: float = Field(
        default=1.5,
        gt=0,
        le=5,
        description="Max total open risk (%)",
    )
    max_daily_loss_percent: float = Field(
        default=2.0,
        gt=0,
        le=10,
        description="Max daily loss (% of start-of-day equity)",
    )
    max_total_drawdown_percent: float = Field(
        default=8.0,
        gt=0,
        le=50,
        description="Max drawdown (%)",
    )
    drawdown_mode: Literal["trailing", "static"] = Field(
        default="trailing",
        description="Drawdown from the equity high (trailing) or the first equity (static)",
    )
    max_open_trades: int = Field(default=3, ge=1, le=20, description="Max open trades")
    max_open_per_symbol: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Max open trades per symbol",
    )
    max_open_per_strategy: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Max open trades per strategy",
    )
    max_trades_per_day: int = Field(default=6, ge=1, le=50, description="Max trades per day")
    max_currency_exposure_percent: float = Field(
        default=1.0,
        gt=0,
        le=5,
        description="Max risk on one currency (%)",
    )
    margin_level_floor_percent: float = Field(
        default=300.0,
        ge=100,
        le=10_000,
        description="Margin level floor after the trade (%)",
    )
    count_manual_trades: bool = Field(
        default=True,
        description="Manual trades count toward exposure and daily trades",
    )
    commission_per_lot: float = Field(
        default=0.0,
        ge=0,
        le=1000,
        description="Commission per lot when the history has none (account currency)",
    )

    @model_validator(mode="after")
    def _consistent(self) -> RiskSettings:
        if self.risk_per_trade_percent > self.max_total_open_risk_percent:
            raise ValueError("the risk per trade cannot be above the max total open risk")
        return self


PROFILES: dict[str, RiskSettings] = {
    "conservative": RiskSettings(
        risk_per_trade_percent=0.25,
        max_total_open_risk_percent=0.75,
        max_daily_loss_percent=1.0,
        max_total_drawdown_percent=5.0,
        max_open_trades=2,
        max_trades_per_day=4,
        max_currency_exposure_percent=0.5,
    ),
    "normal": RiskSettings(),
    "prop_firm": RiskSettings(
        risk_per_trade_percent=0.5,
        max_total_open_risk_percent=1.5,
        max_daily_loss_percent=4.0,
        max_total_drawdown_percent=8.0,
        drawdown_mode="static",
        max_trades_per_day=6,
    ),
}
PROFILE_TITLES: dict[str, str] = {
    "conservative": "Conservative",
    "normal": "Normal",
    "prop_firm": "Prop-firm",
    CUSTOM: "Custom",
}
PROFILE_NOTES: dict[str, str] = {
    "conservative": "0.25% per trade, 1% daily loss, 5% drawdown.",
    "normal": "0.5% per trade, 2% daily loss, 8% trailing drawdown (spec defaults).",
    "prop_firm": (
        "0.5% per trade, 4% daily loss, 8% static drawdown: a buffer below the common 5% / "
        "10% prop rules. Set your firm's exact numbers, then it becomes Custom."
    ),
    CUSTOM: "Your own values.",
}


def _default_settings() -> RiskSettings:
    return RiskSettings()


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    profile: str = "normal"
    settings: RiskSettings = Field(default_factory=_default_settings)

    def profile_title(self) -> str:
        return PROFILE_TITLES.get(self.profile, self.profile)


def profile_config(name: str) -> RiskConfig:
    return RiskConfig(profile=name, settings=PROFILES[name].model_copy())


def matching_profile(settings: RiskSettings) -> str:
    """The built-in profile with exactly these values, or "custom"."""
    for name, preset in PROFILES.items():
        if preset == settings:
            return name
    return CUSTOM


def load_risk_config(directory: Path) -> tuple[RiskConfig, str]:
    """The saved config and a note when the file was invalid (then the Normal profile)."""
    path = directory / RISK_FILE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RiskConfig(), ""
    except (OSError, ValueError):
        return RiskConfig(), f"{RISK_FILE_NAME} could not be read: using the Normal profile"
    try:
        return RiskConfig.model_validate(raw), ""
    except ValidationError as error:
        count = error.error_count()
        return RiskConfig(), f"{RISK_FILE_NAME}: {count} invalid value(s), using the Normal profile"


def save_risk_config(directory: Path, config: RiskConfig) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / RISK_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class RiskSettingsSource:
    """The saved risk config, shared by the UI and the analysis thread; read again only when
    the file changed."""

    def __init__(self, directory: Path, note: Callable[[str], None] | None = None) -> None:
        self._directory = directory
        self._path = directory / RISK_FILE_NAME
        self._note = note
        self._lock = threading.Lock()
        self._stamp: float | None = None
        self._loaded = False
        self._config = RiskConfig()

    @property
    def config(self) -> RiskConfig:
        self._reload()
        with self._lock:
            return self._config

    def settings(self) -> RiskSettings:
        return self.config.settings

    def save(self, config: RiskConfig) -> None:
        save_risk_config(self._directory, config)
        with self._lock:
            self._loaded = False
        self._reload()

    def _reload(self) -> None:
        try:
            stamp: float | None = self._path.stat().st_mtime_ns / 1e9
        except OSError:
            stamp = None
        with self._lock:
            if self._loaded and stamp == self._stamp:
                return
            self._loaded = True
            self._stamp = stamp
            config, note = load_risk_config(self._directory)
            self._config = config
        if note and self._note is not None:
            self._note(note)
