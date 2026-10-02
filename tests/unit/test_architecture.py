"""Architecture rules from spec D3 that a test can enforce."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
IMPORTS_MT5 = re.compile(r"^\s*(import MetaTrader5|from MetaTrader5 )", re.MULTILINE)
ORDER_CALLS = re.compile(r"\border_(send|check)\b")
READ_ONLY_MODULES = (
    "checklist.py",
    "diagnostics.py",
    "smoke_test.py",
    "connection.py",
    "history_sync.py",
)


def sources() -> dict[str, str]:
    return {
        path.relative_to(APP).as_posix(): path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
    }


def test_only_the_helper_process_imports_metatrader5() -> None:
    importers = sorted(name for name, text in sources().items() if IMPORTS_MT5.search(text))
    assert importers == ["mt5/terminal_process.py"]


def test_the_app_never_imports_test_fakes() -> None:
    offenders = [name for name, text in sources().items() if "tests.fakes" in text]
    assert offenders == []


def test_read_only_tools_never_mention_order_functions() -> None:
    texts = sources()
    offenders = [name for name in READ_ONLY_MODULES if ORDER_CALLS.search(texts[f"mt5/{name}"])]
    assert offenders == []


def test_storage_and_the_domain_never_import_qt_or_metatrader5() -> None:
    layers = ("storage/", "domain/")
    offenders = [
        name
        for name, text in sources().items()
        if name.startswith(layers) and ("PySide6" in text or IMPORTS_MT5.search(text))
    ]
    assert offenders == []


def test_analysis_calendar_engine_and_core_never_import_qt_or_metatrader5() -> None:
    layers = ("analysis/", "calendar/", "engine/", "core/")
    offenders = [
        name
        for name, text in sources().items()
        if name.startswith(layers) and ("PySide6" in text or IMPORTS_MT5.search(text))
    ]
    assert offenders == []


def test_the_analysis_is_pure() -> None:
    """No MT5 layer, no storage, no files, no threads: the same bars give the same result."""
    forbidden = re.compile(r"app\.(mt5|storage|engine|ui)\b|(?<![\w.])open\(|threading|sqlite3")
    offenders = [
        f"{name}: {found.group(0)}"
        for name, text in sources().items()
        if name.startswith("analysis/")
        for found in forbidden.finditer(text)
    ]
    assert offenders == []


def test_market_data_tools_never_trade() -> None:
    texts = sources()
    for name in ("mt5/market_data.py", "engine/market_watch.py"):
        assert not ORDER_CALLS.search(texts[name]), name


def test_strategies_are_pure() -> None:
    """Strategies see only the MarketContext: no MT5, storage, engine, UI, files or threads."""
    forbidden = re.compile(r"app\.(mt5|storage|engine|ui)\b|(?<![\w.])open\(|threading|sqlite3")
    offenders = [
        f"{name}: {found.group(0)}"
        for name, text in sources().items()
        if name.startswith("strategies/")
        for found in forbidden.finditer(text)
    ]
    assert offenders == []


def test_signal_code_never_trades() -> None:
    texts = sources()
    names = ["engine/signal_pipeline.py", "engine/filters.py", "ui/signals_page.py"]
    assert [name for name in names if ORDER_CALLS.search(texts[name])] == []
