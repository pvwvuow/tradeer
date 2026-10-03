from app.observability.categories import DEFAULT_CATEGORY, LogCategory, parse_category

SPEC_E3_CATEGORIES = (
    "app",
    "mt5",
    "market_data",
    "analysis",
    "strategy",
    "ml",
    "risk",
    "execution",
    "position",
    "sync",
    "backtest",
    "ui",
    "notify",
    "llm",
    "audit",
    "perf",
)


def test_every_spec_category_exists_plus_update() -> None:
    values = [category.value for category in LogCategory]
    assert tuple(values[: len(SPEC_E3_CATEGORIES)]) == SPEC_E3_CATEGORIES
    assert "update" in values  # spec J4 logs the updater to an `update` category
    assert len(values) == len(set(values)) == 17


def test_unknown_categories_fall_back_to_app() -> None:
    assert parse_category("mt5") is LogCategory.MT5
    assert parse_category(LogCategory.RISK) is LogCategory.RISK
    assert parse_category("nonsense") is DEFAULT_CATEGORY is LogCategory.APP
    assert parse_category(None) is LogCategory.APP
