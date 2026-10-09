"""Saved prompts of the AI Lab (docs/NOCURVE_V2.md 20e3): the starters until one is saved,
save, newest first, no duplicates, delete, limits, a broken file."""

from __future__ import annotations

from pathlib import Path

from app.ai.prompt_store import PROMPT_FILE, STARTERS, TEXT_CHARS, PromptStore, starters


def test_the_starters_come_until_a_prompt_is_saved(tmp_path: Path) -> None:
    store = PromptStore(tmp_path)
    found = store.prompts(fa=True)
    assert [prompt.title for prompt in found] == [persian for _, persian, _ in STARTERS]
    assert all(prompt.builtin and prompt.text == prompt.title for prompt in found)
    assert starters(False)[0].title == "Build the report for the AI"
    assert not (tmp_path / PROMPT_FILE).exists()


def test_saving_keeps_the_newest_first_without_duplicates(tmp_path: Path) -> None:
    store = PromptStore(tmp_path)
    assert store.save("   ", 1.0) is None
    first = store.save("Why do I lose on Mondays?", 10.0)
    second = store.save("Which session is best?\nShow a table.", 20.0, note="sessions")
    assert first is not None and second is not None
    again = store.save("Why do I lose on Mondays?", 30.0)
    assert again is not None
    saved = store.saved()
    assert [prompt.text for prompt in saved] == [
        "Why do I lose on Mondays?",
        "Which session is best?\nShow a table.",
    ]
    assert saved[1].title == "Which session is best? Show a table." and saved[1].note == "sessions"
    listed = store.prompts()
    assert listed[0].prompt_id == again.prompt_id and not listed[0].builtin
    assert len(listed) == 2 + len(STARTERS)


def test_delete_limits_and_a_broken_file(tmp_path: Path) -> None:
    store = PromptStore(tmp_path)
    found = store.save("x" * (TEXT_CHARS + 50), 5.0)
    assert found is not None and len(found.text) == TEXT_CHARS
    assert store.delete("nope") is False
    assert store.delete(found.prompt_id) is True and store.saved() == []
    (tmp_path / PROMPT_FILE).write_text("{broken", encoding="utf-8")
    assert store.saved() == [] and len(store.prompts()) == len(STARTERS)
