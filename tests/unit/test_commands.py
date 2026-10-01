from app.ui.commands import Command, filter_commands, match_score

TITLES = ["Go to Dashboard", "Go to Market", "Go to Risk", "Toggle theme"]


def _noop() -> None:
    """Do nothing."""


def _commands() -> list[Command]:
    return [Command(title.lower(), title, "", _noop) for title in TITLES]


def test_empty_query_keeps_the_original_order() -> None:
    assert [command.title for command in filter_commands(_commands(), "")] == TITLES


def test_prefix_beats_substring_beats_fuzzy() -> None:
    prefix = match_score("go", "Go to Market")
    substring = match_score("market", "Go to Market")
    fuzzy = match_score("gtm", "Go to Market")
    assert prefix == 0
    assert substring == 7
    assert fuzzy is not None
    assert fuzzy >= 100


def test_characters_out_of_order_do_not_match() -> None:
    assert match_score("xyz", "Go to Market") is None
    assert match_score("tekram", "Go to Market") is None


def test_filter_ranks_the_best_match_first() -> None:
    result = filter_commands(_commands(), "mar")
    assert [command.title for command in result] == ["Go to Market"]
