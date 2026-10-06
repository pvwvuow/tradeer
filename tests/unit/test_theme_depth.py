"""The 0.24 look (6 October 2026): the depth shades and the drawn controls stay readable."""

from app.ui.theme import AA_TEXT, DARK, LIGHT, build_qss, contrast_ratio, mix, shades


def test_colors_mix() -> None:
    assert mix("#000000", "#FFFFFF", 0.5) == "#808080"
    assert mix("#123456", "#FFFFFF", 0.0) == "#123456"
    assert mix("#123456", "#FFFFFF", 2.0) == "#FFFFFF"


def test_text_stays_readable_on_every_shade() -> None:
    for tokens in (DARK, LIGHT):
        found = shades(tokens)
        colors = (tokens.text, tokens.text_secondary, tokens.accent)
        for color in (*colors, tokens.profit, tokens.loss, tokens.warning):
            assert contrast_ratio(color, found.card_low) >= AA_TEXT, (tokens.name, color)
        assert contrast_ratio(tokens.text_secondary, found.row_hover) >= AA_TEXT
        for top in (found.accent_top, found.accent_hover):
            assert contrast_ratio(tokens.accent_text, top) >= AA_TEXT, (tokens.name, top)


def test_inputs_have_drawn_arrows_and_check_marks() -> None:
    qss = build_qss(DARK)
    for selector in (
        "QComboBox::down-arrow",
        "QAbstractSpinBox::up-arrow",
        "QCheckBox::indicator:checked",
        "QRadioButton::indicator:checked",
        "QTableView::item:hover",
        "qlineargradient",
    ):
        assert selector in qss, selector


def test_only_properties_qt_knows() -> None:
    for tokens in (DARK, LIGHT):
        qss = build_qss(tokens)
        for word in ("letter-spacing", "box-shadow", "transition", "text-transform"):
            assert word not in qss, word
