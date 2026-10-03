from app.mt5.symbols import resolve_symbol

BROKER = ["EURUSD.m", "EURUSD.pro", "GBPUSDm", "XAUUSD", "eurgbp"]


def test_exact_names_win_in_any_case() -> None:
    assert resolve_symbol("XAUUSD", BROKER) == "XAUUSD"
    assert resolve_symbol("EURGBP", BROKER) == "eurgbp"


def test_suffixes_resolve_to_the_shortest_match() -> None:
    assert resolve_symbol("EURUSD", BROKER) == "EURUSD.m"
    assert resolve_symbol("gbpusd", BROKER) == "GBPUSDm"


def test_missing_or_empty_symbols() -> None:
    assert resolve_symbol("USDJPY", BROKER) is None
    assert resolve_symbol("  ", BROKER) is None
