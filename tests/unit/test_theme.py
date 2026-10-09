import re

from app.core.ui_prefs import ThemeName
from app.ui.theme import (
    CHIP_TONES,
    DARK,
    LIGHT,
    NUMBER_FONT,
    build_qss,
    chip_colors,
    contrast_failures,
    contrast_ratio,
    shades,
    tokens_for,
)

HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$")
WCAG_AA = 4.5


def test_all_color_tokens_are_uppercase_hex() -> None:
    for tokens in (DARK, LIGHT):
        for name, value in tokens.colors().items():
            assert HEX_COLOR.match(value), (tokens.name, name, value)


def test_dark_theme_uses_the_0_31_ink_and_cream_palette() -> None:
    assert DARK.bg == "#0E100F"
    assert DARK.text == "#ECE8DC"
    assert DARK.text_secondary == "#A2ACA4"
    assert DARK.border_strong == "#364039"
    # The ink is the accent: selected and primary controls are drawn inverted.
    assert DARK.accent == DARK.text
    assert DARK.accent_text == DARK.bg
    assert DARK.profit == "#5AD19A"
    assert DARK.loss == "#F2795F"
    assert DARK.warning == "#D9B45A"
    assert LIGHT.bg == "#F1EEE6"
    assert LIGHT.text == "#1B1C19"
    assert LIGHT.accent == LIGHT.text


def test_the_layers_go_from_page_to_chrome_to_content() -> None:
    for tokens in (DARK, LIGHT):
        page, chrome, content = (tokens.bg, tokens.surface, tokens.card)
        assert len({page, chrome, content}) == 3, tokens.name
    # Dark: content is lighter than the page; light: content is brighter paper on cream.
    assert contrast_ratio(DARK.card, "#000000") > contrast_ratio(DARK.bg, "#000000")
    assert contrast_ratio(LIGHT.card, "#000000") > contrast_ratio(LIGHT.bg, "#000000")


def test_text_colors_meet_wcag_aa_on_every_surface() -> None:
    for tokens in (DARK, LIGHT):
        foregrounds = (tokens.text, tokens.text_secondary, tokens.profit, tokens.loss)
        for background in (tokens.bg, tokens.surface, tokens.card, shades(tokens).field):
            for foreground in (*foregrounds, tokens.warning):
                ratio = contrast_ratio(foreground, background)
                assert ratio >= WCAG_AA, (tokens.name, foreground, background, ratio)
        assert contrast_ratio(tokens.accent_text, tokens.accent) >= WCAG_AA
        assert contrast_failures(tokens) == []


def test_contrast_of_black_on_white_is_21() -> None:
    assert round(contrast_ratio("#000000", "#FFFFFF"), 1) == 21.0


def test_stylesheet_is_generated_from_tokens() -> None:
    dark_qss = build_qss(DARK)
    assert DARK.bg in dark_qss
    assert DARK.accent in dark_qss
    assert LIGHT.bg in build_qss(LIGHT)
    assert build_qss(LIGHT) != dark_qss


def test_figures_use_the_number_face_and_tables_have_no_zebra() -> None:
    qss = build_qss(DARK)
    assert f'font-family: "{NUMBER_FONT}"' in qss
    assert f"alternate-background-color: {DARK.card};" in qss


def test_tokens_for_returns_the_requested_theme() -> None:
    assert tokens_for(ThemeName.DARK) is DARK
    assert tokens_for(ThemeName.LIGHT) is LIGHT


def test_status_chips_meet_wcag_aa_in_both_themes() -> None:
    for tokens in (DARK, LIGHT):
        for tone in CHIP_TONES:
            text, fill = chip_colors(tokens, tone)
            assert contrast_ratio(text, fill) >= WCAG_AA, (tokens.name, tone)
        assert contrast_ratio(tokens.text, tokens.hover) >= WCAG_AA
        assert contrast_ratio(tokens.accent, tokens.accent_soft) >= WCAG_AA


def test_the_stylesheet_covers_every_common_control() -> None:
    qss = build_qss(DARK)
    for selector in (
        "QTableView",
        "QHeaderView::section",
        "QTabBar::tab:selected",
        "QComboBox",
        "QAbstractSpinBox",
        "QScrollBar::handle:vertical",
        "QProgressBar::chunk",
        "QGroupBox",
        "QMenu::item:selected",
        'QLabel[chip="loss"]',
        'QLabel[role="kpi"]',
        'QPushButton[variant="ghost"]',
        'QPushButton[variant="primary"]',
    ):
        assert selector in qss, selector


def test_the_stylesheet_braces_are_balanced() -> None:
    for tokens in (DARK, LIGHT):
        depth = 0
        for char in build_qss(tokens):
            if char == "{":
                depth += 1
                assert depth == 1
            elif char == "}":
                depth -= 1
                assert depth == 0
        assert depth == 0
