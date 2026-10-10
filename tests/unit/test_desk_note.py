"""The AI's note on a pasted signal (docs/SIGNAL_DESK.md 2.5, phase 21b3)."""

from app.ai.desk_note import NOTE_CHARACTERS, SYSTEM, clean_note, note_messages


def test_the_note_request_holds_the_signal_and_the_facts_only() -> None:
    messages = note_messages("EURUSD buy sl 1.08", ["Now: EURUSD: H4 uptrend", " ", "x"])
    system, user = messages
    assert system["role"] == "system" and system["content"] == f"{SYSTEM} Answer in English."
    assert user["role"] == "user"
    assert user["content"] == (
        "The pasted signal:\nEURUSD buy sl 1.08\n\nThe facts:\nNow: EURUSD: H4 uptrend\nx"
    )
    persian = note_messages("EURUSD buy", [], persian=True)
    assert persian[0]["content"].endswith("Answer in Persian.")


def test_the_note_is_three_plain_sentences() -> None:
    text = "**One** thing.\n\nTwo things!  # Three?\nFour."
    assert clean_note(text) == "One thing. Two things! Three?"
    persian = "\u0627\u0648\u0644\u061f \u062f\u0648\u0645."  # two sentences, a Persian ?
    assert clean_note(persian) == persian
    long = clean_note("word " * 400)
    assert len(long) == NOTE_CHARACTERS and long.endswith("\u2026")
    assert clean_note("   ") == ""
