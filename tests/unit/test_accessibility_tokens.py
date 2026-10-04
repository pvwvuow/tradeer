"""WCAG AA for every color pair the app draws, and visible keyboard focus (spec F1)."""

from dataclasses import replace

from app.ui.theme import (
    AA_NON_TEXT,
    AA_TEXT,
    DARK,
    LIGHT,
    build_qss,
    contrast_failures,
    contrast_pairs,
    contrast_ratio,
)


def test_every_pair_meets_wcag_aa_in_both_themes() -> None:
    for tokens in (DARK, LIGHT):
        assert contrast_failures(tokens) == [], tokens.name


def test_the_audit_covers_text_controls_and_focus() -> None:
    pairs = contrast_pairs(DARK)
    assert ("loss", "card", AA_TEXT) in pairs
    assert ("text_secondary", "hover", AA_TEXT) in pairs
    assert ("control_border", "bg", AA_NON_TEXT) in pairs
    assert ("accent", "surface", AA_NON_TEXT) in pairs


def test_a_pale_color_is_reported() -> None:
    pale = replace(LIGHT, text_secondary="#C0C4CC")
    assert any(text.startswith("text_secondary on") for text in contrast_failures(pale))


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


def test_controls_have_a_visible_border() -> None:
    for tokens in (DARK, LIGHT):
        assert f"border: 1px solid {tokens.control_border}" in build_qss(tokens)
        assert contrast_ratio(tokens.control_border, tokens.card) >= AA_NON_TEXT
