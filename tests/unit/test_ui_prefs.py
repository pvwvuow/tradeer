import json
import tempfile
from pathlib import Path

from app.core.paths import app_data_dir, safe_profile_name
from app.core.ui_prefs import (
    LOOK,
    PREFS_FILE_NAME,
    ThemeName,
    UiPrefs,
    ViewMode,
    load_prefs,
    migrate,
    save_prefs,
)


def test_missing_file_gives_safe_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        prefs = load_prefs(Path(tmp))
    assert prefs.theme is ThemeName.LIGHT  # No Curve v2 opens in the light theme
    assert prefs.view_mode is ViewMode.SIMPLE
    assert prefs.look == LOOK


def test_preferences_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "profile"
        save_prefs(directory, UiPrefs(theme=ThemeName.DARK, view_mode=ViewMode.ADVANCED))
        loaded = load_prefs(directory)
    assert loaded.theme is ThemeName.DARK  # a dark choice made in the new look is kept
    assert loaded.view_mode is ViewMode.ADVANCED


def test_a_file_from_before_0_33_moves_to_the_light_theme_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        old = {"theme": "dark", "view_mode": "advanced", "onboarded": True, "language": "fa"}
        (directory / PREFS_FILE_NAME).write_text(json.dumps(old), encoding="utf-8")
        moved = load_prefs(directory)
        assert moved.theme is ThemeName.LIGHT and moved.look == LOOK
        assert moved.view_mode is ViewMode.ADVANCED and moved.onboarded
        assert moved.language.value == "fa"
        save_prefs(directory, moved.model_copy(update={"theme": ThemeName.DARK}))
        assert load_prefs(directory).theme is ThemeName.DARK
    assert migrate(UiPrefs(theme=ThemeName.DARK, look=1)).theme is ThemeName.LIGHT
    assert migrate(UiPrefs(theme=ThemeName.DARK)).theme is ThemeName.DARK


def test_corrupt_or_invalid_file_falls_back_to_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        target = directory / PREFS_FILE_NAME
        target.write_text("{not json", encoding="utf-8")
        assert load_prefs(directory) == UiPrefs()
        target.write_text(json.dumps({"theme": "neon"}), encoding="utf-8")
        assert load_prefs(directory) == UiPrefs()
        target.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        assert load_prefs(directory) == UiPrefs()


def test_profile_names_are_sanitised() -> None:
    assert safe_profile_name("demo-1") == "demo-1"
    assert safe_profile_name("../../evil") == "evil"
    assert safe_profile_name("") == "default"
    assert app_data_dir("demo").parts[-2:] == ("profiles", "demo")
