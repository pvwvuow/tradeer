"""Strategy settings per profile (`strategies.json`) and the auto-generated form fields."""

import json
import tempfile
from pathlib import Path

from app.core.param_fields import param_fields, validate_params
from app.core.strategy_settings import (
    STRATEGIES_FILE_NAME,
    StrategyEntry,
    StrategySettingsSource,
    load_strategy_settings,
    save_strategy_settings,
)
from app.engine.filters import FilterSettings
from app.strategies.london_breakout import LondonBreakoutParams

EXAMPLES = ["trend_pullback", "london_breakout"]
LAB = ["range_reversion", "channel_breakout", "ema_momentum"]


def test_defaults_turn_both_example_strategies_on() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = load_strategy_settings(Path(tmp))
        assert settings.enabled() == EXAMPLES
        assert settings.filters == FilterSettings()


def test_the_lab_strategies_start_off_and_can_be_turned_on() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = StrategySettingsSource(folder)
        settings = source.settings
        for name in LAB:
            assert not settings.entry(name).enabled
            settings = settings.with_entry(name, StrategyEntry(enabled=True))
        source.save(settings)
        assert load_strategy_settings(folder).enabled() == [*EXAMPLES, *LAB]
        old = {"strategies": {"trend_pullback": {"enabled": True}}}
        (folder / STRATEGIES_FILE_NAME).write_text(json.dumps(old), encoding="utf-8")
        assert load_strategy_settings(folder).enabled() == EXAMPLES


def test_settings_round_trip_and_reload_on_change() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = StrategySettingsSource(folder)
        assert [s.name for s in source.strategies()] == EXAMPLES
        changed = source.settings.with_entry(
            "trend_pullback",
            StrategyEntry(enabled=False, params={"reward_r": 3.0}),
        )
        source.save(changed.with_filters(FilterSettings(min_ev_r=0.2)))
        again = load_strategy_settings(folder)
        assert again.enabled() == ["london_breakout"]
        assert again.entry("trend_pullback").params == {"reward_r": 3.0}
        assert source.filters().min_ev_r == 0.2
        assert [s.name for s in source.strategies()] == ["london_breakout"]


def test_bad_files_and_bad_params_fall_back_to_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / STRATEGIES_FILE_NAME).write_text("{broken", encoding="utf-8")
        assert load_strategy_settings(folder).enabled() == EXAMPLES
        raw = {
            "strategies": {"trend_pullback": {"enabled": True, "params": {"reward_r": -1}}},
            "filters": {"min_probability_percent": 500},
        }
        (folder / STRATEGIES_FILE_NAME).write_text(json.dumps(raw), encoding="utf-8")
        notes: list[str] = []
        source = StrategySettingsSource(folder, note=notes.append)
        built = {s.name: s for s in source.strategies()}
        assert built["trend_pullback"].params.model_dump()["reward_r"] == 2.0
        assert notes and "invalid" in notes[0]
        assert source.filters() == FilterSettings()
        save_strategy_settings(folder, load_strategy_settings(folder))


def test_form_fields_follow_the_model() -> None:
    fields = {field.name: field for field in param_fields(LondonBreakoutParams)}
    assert fields["clock"].kind == "choice" and fields["clock"].choices == (
        "london",
        "broker",
        "utc",
    )
    assert fields["reward_r"].kind == "float" and fields["reward_r"].maximum == 10
    assert fields["range_start"].kind == "text" and fields["range_start"].pattern
    filters = {field.name: field for field in param_fields(FilterSettings)}
    assert filters["require_probability"].kind == "bool"
    assert filters["news_minutes"].kind == "int" and filters["news_minutes"].step() == 1.0
    ok, problems = validate_params(LondonBreakoutParams, {"reward_r": 2.0})
    assert ok is not None and problems == []
    bad, problems = validate_params(LondonBreakoutParams, {"range_start": "09:00"})
    assert bad is None and "Times must be in order" in problems[0]
