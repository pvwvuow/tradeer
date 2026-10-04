"""Persian for the Simple view (spec A, F1): the translator and its table."""

from app.core.ui_prefs import Language, UiPrefs
from app.domain.modes import OperatingMode
from app.ui.home_model import (
    KNOWLEDGE_QUESTION,
    MODE_LINES,
    PRACTICE_TEXT,
    PRACTICE_TITLE,
    SYMBOL_NAMES,
    SuggestionView,
    approve_block_text,
    approve_question,
    balance_view,
    duration_words,
    home_status,
)
from app.ui.i18n import Translator, compile_template, fields
from app.ui.i18n_fa import PAIRS, PERSIAN

NOW = 1_790_000_000.0
FA = Translator(Language.FA)


def persian(text: str) -> bool:
    return any("\u0600" <= letter <= "\u06ff" for letter in text)


def view() -> SuggestionView:
    return SuggestionView(
        signal_id="s1",
        title="Gold (XAUUSD)",
        action="Buy",
        arrow="\u25b2",
        reason="Gold is trending up and just dipped back, so it may rise again.",
        make="You could make about +$40.00 if it goes right",
        lose="You could lose about \u2212$20.00 if it goes wrong",
        confidence="Fairly confident",
        confidence_note="Based on 120 earlier suggestions of this kind.",
        ends="This suggestion ends in 1 h 05 min",
        moved="",
        queue="1 of 2 suggestions",
        details=(),
    )


def test_english_is_the_source_and_needs_no_table() -> None:
    english = Translator(Language.EN)
    assert not english.active and not english.right_to_left
    assert english.text("Approve") == "Approve"
    assert UiPrefs().language is Language.EN


def test_the_table_is_complete_and_consistent() -> None:
    assert len(PERSIAN) == len(PAIRS)  # no source text twice
    for source, target in PERSIAN.items():
        assert target.strip(), source
        assert sorted(fields(source)) == sorted(fields(target)), source
        assert compile_template(source).fullmatch(source.replace("{", "<").replace("}", ">"))


def test_fixed_texts_and_templates() -> None:
    assert FA.right_to_left
    assert FA.text("Approve") == "تأیید"
    assert FA.text("Gold (XAUUSD)") == "طلا (XAUUSD)"
    assert FA.text("Today: \u25b2 +$12.30 (+1.20%)") == "امروز: \u25b2 +$12.30 (+1.20%)"
    assert FA.text("1 h 05 min") == "1 ساعت و 05 دقیقه"
    assert FA.text("12 min") == "12 دقیقه"
    reason = (
        "The Euro vs US dollar rate is trending down and just bounced back, so it may fall again."
    )
    assert FA.text(reason).startswith("روند نرخ یورو به دلار آمریکا نزولی است")
    assert FA.text("Something new") == "Something new"  # no template: stays English
    assert FA.text("") == ""


def test_every_default_home_text_is_persian() -> None:
    texts = [PRACTICE_TITLE, PRACTICE_TEXT, KNOWLEDGE_QUESTION, *MODE_LINES.values()]
    texts += [*SYMBOL_NAMES.values(), *view().default_texts()]
    for connected, stopped, halted, suggestions, trades, market in (
        (False, "", "", 0, 0, True),
        (True, "x", "", 0, 0, True),
        (True, "", "daily_limit", 0, 0, True),
        (True, "", "", 1, 0, True),
        (True, "", "", 3, 0, True),
        (True, "", "", 0, 1, True),
        (True, "", "", 0, 2, True),
        (True, "", "", 0, 0, False),
        (True, "", "", 0, 0, True),
    ):
        texts.append(
            home_status(
                connected=connected,
                stopped=stopped,
                halted=halted,
                suggestions=suggestions,
                open_trades=trades,
                market_open=market,
            ),
        )
    balance = balance_view(None, OperatingMode.PAPER, [], NOW)
    texts += [balance.title.upper(), balance.balance, balance.today, duration_words(90)]
    texts.append(approve_block_text("the spread is too wide", OperatingMode.PAPER))
    texts.append(approve_block_text("x", OperatingMode.ANALYSIS_ONLY))
    untranslated = [text for text in texts if not persian(FA.text(text))]
    assert untranslated == []


def test_questions_are_translated_line_by_line() -> None:
    question = approve_question(view(), OperatingMode.PAPER)
    translated = FA.text(question)
    assert translated.count("\n") == question.count("\n")
    assert translated.splitlines()[0] == "خرید طلا (XAUUSD)؟"
    assert all(persian(line) for line in translated.splitlines() if line)
    real = FA.text(approve_question(view(), OperatingMode.SEMI_AUTO))
    assert "سفارش واقعی" in real
