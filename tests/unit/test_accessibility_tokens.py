"""WCAG AA for every color pair the app draws, and visible keyboard focus (spec F1).

0.33 (No Curve v2): words meet 4.5:1; profit, loss and warning are the design's exact colors
and meet 4.2:1 (`TONE_TEXT`); the ink accent (the focus ring) meets 3:1 on every surface.
"""

from dataclasses import replace

from app.ui.theme import (
    AA_NON_TEXT,
    AA_TEXT,
    DARK,
    LIGHT,
    TONE_TEXT,
    build_qss,
    contrast_failures,
    contrast_pairs,
    contrast_ratio,
)


def test_every_pair_meets_its_minimum_in_both_themes() -> None:
    for tokens in (DARK, LIGHT):
        assert contrast_failures(tokens) == [], tokens.name


def test_the_audit_covers_words_results_fills_and_focus() -> None:
    pairs = contrast_pairs(DARK)
    assert ("text_secondary", "bg", AA_TEXT) in pairs
    assert ("loss", "card", TONE_TEXT) in pairs
    assert ("profit", "profit_soft", TONE_TEXT) in pairs
    assert ("text_secondary", "hover", AA_TEXT) in pairs
    assert ("accent_text", "accent", AA_TEXT) in pairs
    assert ("accent", "surface", AA_NON_TEXT) in pairs
    # The design's hairline control border is not the only cue of a control.
    assert not [pair for pair in pairs if pair[0] == "control_border"]


def test_a_pale_color_is_reported() -> None:
    pale = replace(LIGHT, text_secondary="#C0C4CC")
    assert any(text.startswith("text_secondary on") for text in contrast_failures(pale))
    faint = replace(LIGHT, profit="#7FBFA0")
    assert any(text.startswith("profit on") for text in contrast_failures(faint))


def test_every_kind_of_button_shows_keyboard_focus() -> None:
    qss = build_qss(DARK)
    for selector in (
        "QPushButton:focus",
        'QPushButton[nav="true"]:focus',
        'QPushButton[variant="ghost"]:focus',
        'QPushButton[variant="primary"]:focus',
        'QPushButton[variant="danger"]:focus',
        "QCheckBox:focus",
        "QTableView:focus",
        "QLineEdit:focus",
    ):
        assert selector in qss, selector


def test_controls_have_a_border_and_an_ink_focus_ring() -> None:
    for tokens in (DARK, LIGHT):
        qss = build_qss(tokens)
        assert f"border: 1px solid {tokens.control_border}" in qss
        assert f"border: 1px solid {tokens.accent};" in qss
        assert contrast_ratio(tokens.accent, tokens.card) >= AA_NON_TEXT
        assert contrast_ratio(tokens.accent, tokens.bg) >= AA_NON_TEXT
