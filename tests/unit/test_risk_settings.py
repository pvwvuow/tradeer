"""Risk settings and profiles (spec B4, C6): hard caps and safe fallbacks."""

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.param_fields import param_fields
from app.risk.settings import (
    CUSTOM,
    PROFILE_TITLES,
    PROFILES,
    RISK_FILE_NAME,
    RiskConfig,
    RiskSettings,
    RiskSettingsSource,
    load_risk_config,
    matching_profile,
    profile_config,
    save_risk_config,
)


def test_the_defaults_are_the_spec_defaults() -> None:
    settings = RiskSettings()
    assert settings.risk_per_trade_percent == 0.5
    assert settings.max_total_open_risk_percent == 1.5
    assert settings.max_daily_loss_percent == 2.0
    assert settings.max_total_drawdown_percent == 8.0
    assert settings.max_open_trades == 3 and settings.max_trades_per_day == 6
    assert settings.margin_level_floor_percent == 300.0
    assert matching_profile(settings) == "normal"


def test_the_built_in_profiles() -> None:
    assert PROFILES["conservative"].risk_per_trade_percent == 0.25
    assert PROFILES["prop_firm"].drawdown_mode == "static"
    assert set(PROFILES) | {CUSTOM} == set(PROFILE_TITLES)
    for name in PROFILES:
        assert matching_profile(profile_config(name).settings) == name
    edited = PROFILES["normal"].model_copy(update={"max_open_trades": 2})
    assert matching_profile(edited) == CUSTOM


def test_no_setting_can_raise_the_risk_above_the_hard_caps() -> None:
    with pytest.raises(ValidationError):
        RiskSettings(risk_per_trade_percent=2.0)
    with pytest.raises(ValidationError):
        RiskSettings(max_total_drawdown_percent=80.0)
    with pytest.raises(ValidationError):
        RiskSettings(risk_per_trade_percent=1.0, max_total_open_risk_percent=0.5)


def test_the_form_gets_every_setting_with_its_limits() -> None:
    fields = {field.name: field for field in param_fields(RiskSettings)}
    assert fields["risk_per_trade_percent"].maximum == 1.0
    assert fields["capital_basis"].choices == ("equity", "balance")
    assert fields["count_manual_trades"].kind == "bool"


def test_saved_settings_round_trip_and_bad_files_give_normal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        assert load_risk_config(folder) == (RiskConfig(), "")
        save_risk_config(folder, profile_config("conservative"))
        config, note = load_risk_config(folder)
        assert config.profile == "conservative" and note == ""
        assert config.settings.risk_per_trade_percent == 0.25
        (folder / RISK_FILE_NAME).write_text('{"settings": {"risk_per_trade_percent": 5}}')
        config, note = load_risk_config(folder)
        assert config == RiskConfig() and "invalid" in note
        (folder / RISK_FILE_NAME).write_text("{broken")
        config, note = load_risk_config(folder)
        assert config.settings == RiskSettings() and "could not be read" in note


def test_the_source_reads_the_file_again_after_a_save() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        notes: list[str] = []
        source = RiskSettingsSource(Path(tmp), note=notes.append)
        assert source.config.profile == "normal"
        source.save(profile_config("prop_firm"))
        assert source.settings().max_daily_loss_percent == 4.0
        assert source.config.profile_title() == "Prop-firm"
        assert notes == []
