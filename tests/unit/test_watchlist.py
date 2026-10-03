import tempfile
from pathlib import Path

from app.core.watchlist import (
    DEFAULT_SYMBOLS,
    MAX_SYMBOLS,
    WATCHLIST_FILE_NAME,
    Watchlist,
    clean_symbols,
    load_watchlist,
    save_watchlist,
)


def test_symbols_are_cleaned() -> None:
    assert clean_symbols([" eurusd", "EURUSD", "", "xau usd"]) == ["EURUSD", "XAUUSD"]
    assert len(clean_symbols(f"S{index}" for index in range(20))) == MAX_SYMBOLS


def test_the_watchlist_is_saved_per_profile() -> None:
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        assert load_watchlist(directory).symbols == list(DEFAULT_SYMBOLS)
        save_watchlist(directory, Watchlist().with_symbol("usdjpy").without("gbpusd"))
        assert load_watchlist(directory).symbols == ["EURUSD", "XAUUSD", "USDJPY"]
        (directory / WATCHLIST_FILE_NAME).write_text("{broken", encoding="utf-8")
        assert load_watchlist(directory).symbols == list(DEFAULT_SYMBOLS)
