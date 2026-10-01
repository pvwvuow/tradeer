import json
import tempfile
from pathlib import Path

from app.core.paths import app_data_dir, safe_profile_name
from app.core.ui_prefs import (
    PREFS_FILE_NAME,
    ThemeName,
    UiPrefs,
    ViewMode,
    load_prefs,
    save_prefs,
)


def test_missing_file_gives_safe_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        prefs = load_prefs(Path(tmp))
    assert prefs.theme is ThemeName.DARK
    assert prefs.view_mode is ViewMode.SIMPLE


def test_preferences_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "profile"
        save_prefs(directory, UiPrefs(theme=ThemeName.LIGHT, view_mode=ViewMode.ADVANCED))
        loaded = load_prefs(directory)
    assert loaded.theme is ThemeName.LIGHT
    assert loaded.view_mode is ViewMode.ADVANCED


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
