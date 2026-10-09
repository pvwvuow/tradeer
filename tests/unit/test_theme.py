import re

from app.core.ui_prefs import ThemeName, UiPrefs
from app.ui.theme import (
    CHIP_TONES,
    DARK,
    DEFAULT,
    FONT_BODY,
    FONT_KPI,
    FONT_SMALL,
    FONT_TITLE,
    LIGHT,
    NUMBER_FONT,
    RADIUS_TAG,
    TONE_TEXT,
    build_qss,
    chip_colors,
    chip_edge,
    contrast_failures,
    contrast_ratio,
    px,
    shades,
    tokens_for,
)

HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$")
WCAG_AA = 4.5


def test_all_color_tokens_are_uppercase_hex() -> None:
    for tokens in (DARK, LIGHT):
        for name, value in tokens.colors().items():
            assert HEX_COLOR.match(value), (tokens.name, name, value)


def test_the_light_theme_is_the_no_curve_default() -> None:
    assert DEFAULT is LIGHT and UiPrefs().theme is ThemeName.LIGHT
    assert LIGHT.bg == "#EDF0F4"
    assert LIGHT.surface == LIGHT.card == "#FFFFFF"
    assert LIGHT.border == "#D5DBE5"
    assert LIGHT.border_strong == LIGHT.control_border == "#AEB9CA"
    assert LIGHT.text == "#0F1B2D"
    assert LIGHT.text_secondary == "#52607A"
    assert LIGHT.hover == LIGHT.accent_soft == "#E3E8EF"
    assert (LIGHT.profit, LIGHT.loss, LIGHT.warning) == ("#1B7F53", "#B93636", "#9A6310")
    # The design's rgba(..., .12) soft fills, laid over white.
    assert (LIGHT.profit_soft, LIGHT.loss_soft, LIGHT.warning_soft) == (
        "#E4F0EA",
        "#F7E7E7",
        "#F3ECE2",
    )


def test_the_dark_theme_is_the_designs_lt_palette() -> None:
    assert DARK.bg == "#0A0E13"
    assert DARK.surface == DARK.card == "#121921"
    assert DARK.border == "#243040"
    assert DARK.border_strong == DARK.control_border == "#37485F"
    assert DARK.text == "#E8EDF4"
    assert DARK.text_secondary == "#9AA8BB"
    assert DARK.hover == DARK.accent_soft == "#1A2430"
    assert (DARK.profit, DARK.loss, DARK.warning) == ("#4CC38A", "#FF7D7D", "#E3B04B")
    assert (DARK.profit_soft, DARK.loss_soft, DARK.warning_soft) == (
        "#1A3130",
        "#36282F",
        "#2F2E27",
    )


def test_the_ink_is_the_accent_and_inverted_controls_use_the_page_color() -> None:
    for tokens in (DARK, LIGHT):
        assert tokens.accent == tokens.text, tokens.name
        assert tokens.accent_text == tokens.bg, tokens.name
        # The design's gold accent is never drawn.
        assert "#C8A15A" not in build_qss(tokens) and "#D9B26A" not in build_qss(tokens)


def test_cards_are_the_surface_on_a_different_page() -> None:
    for tokens in (DARK, LIGHT):
        assert tokens.card == tokens.surface != tokens.bg, tokens.name
    # Light: white cards on a grey page; dark: the cards are lighter than the page.
    assert contrast_ratio(LIGHT.card, "#000000") > contrast_ratio(LIGHT.bg, "#000000")
    assert contrast_ratio(DARK.card, "#000000") > contrast_ratio(DARK.bg, "#000000")


def test_words_meet_wcag_aa_and_results_the_design_minimum() -> None:
    for tokens in (DARK, LIGHT):
        words = (tokens.text, tokens.text_secondary)
        tones = (tokens.profit, tokens.loss, tokens.warning)
        for background in (tokens.bg, tokens.surface, tokens.card, shades(tokens).field):
            for foreground in words:
                assert contrast_ratio(foreground, background) >= WCAG_AA, tokens.name
            for foreground in tones:
                ratio = contrast_ratio(foreground, background)
                assert ratio >= TONE_TEXT, (tokens.name, foreground, background, ratio)
        assert contrast_ratio(tokens.accent_text, tokens.accent) >= WCAG_AA
        assert contrast_failures(tokens) == []
    # On cards (where most figures are) every result color meets full AA.
    for tone in (LIGHT.profit, LIGHT.loss, LIGHT.warning):
        assert contrast_ratio(tone, LIGHT.card) >= WCAG_AA


def test_font_sizes_are_the_designs_pixels_in_points() -> None:
    assert px(16) == 12
    assert FONT_SMALL == px(11) == 8.25
    assert FONT_BODY == px(14) == 10.5
    assert FONT_TITLE == px(26)
    assert FONT_KPI == px(27)


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


def test_tags_are_outlined_and_only_the_accent_tag_is_filled() -> None:
    for tokens in (DARK, LIGHT):
        qss = build_qss(tokens)
        for tone in CHIP_TONES:
            text, background = chip_colors(tokens, tone)
            minimum = WCAG_AA if tone in ("neutral", "accent") else TONE_TEXT
            assert contrast_ratio(text, background) >= minimum, (tokens.name, tone)
            if tone != "accent":  # outlined tags also sit on the page itself
                assert contrast_ratio(text, tokens.bg) >= minimum, (tokens.name, tone)
            rule = qss.split(f'QLabel[chip="{tone}"] {{', 1)[1].split("}", 1)[0]
            assert f"border: 1px solid {chip_edge(tokens, tone)};" in rule
            assert f"border-radius: {RADIUS_TAG}px;" in rule
            filled = f"background-color: {tokens.accent};" in rule
            assert filled is (tone == "accent"), (tokens.name, tone)
        assert chip_edge(tokens, "neutral") == tokens.border_strong
        assert chip_edge(tokens, "loss") == tokens.loss
        assert chip_colors(tokens, "accent") == (tokens.bg, tokens.text)


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
