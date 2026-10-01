import pytest

from app.ui.navigation import (
    ADVANCED_GROUPS,
    ADVANCED_PAGES,
    SIMPLE_HOME,
    page_by_id,
    pages_in_group,
)


def test_sidebar_groups_and_order_match_the_spec() -> None:
    assert ADVANCED_GROUPS == ("Trade", "Analyze", "System")
    titles = {group: [page.title for page in pages_in_group(group)] for group in ADVANCED_GROUPS}
    assert titles["Trade"] == ["Dashboard", "Market", "Signals", "Positions & Trades"]
    assert titles["Analyze"] == ["Analytics", "Journal", "Backtest", "Model", "AI Lab"]
    assert titles["System"] == ["Strategies", "Risk", "Logs", "Health", "Settings"]


def test_page_ids_are_unique() -> None:
    ids = [page.page_id for page in (SIMPLE_HOME, *ADVANCED_PAGES)]
    assert len(ids) == len(set(ids))


def test_every_page_names_its_delivery_phase() -> None:
    for page in (SIMPLE_HOME, *ADVANCED_PAGES):
        assert 1 <= page.phase <= 16
        assert page.summary


def test_page_lookup() -> None:
    assert page_by_id("risk").title == "Risk"
    assert page_by_id("home") is SIMPLE_HOME
    with pytest.raises(KeyError):
        page_by_id("does-not-exist")
