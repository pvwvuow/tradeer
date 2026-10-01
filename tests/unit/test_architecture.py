"""Architecture rules from spec D3 that a test can enforce."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
IMPORTS_MT5 = re.compile(r"^\s*(import MetaTrader5|from MetaTrader5 )", re.MULTILINE)
ORDER_CALLS = re.compile(r"\border_(send|check)\b")
READ_ONLY_MODULES = ("checklist.py", "diagnostics.py", "smoke_test.py", "connection.py")


def sources() -> dict[str, str]:
    return {
        path.relative_to(APP).as_posix(): path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
    }


def test_only_the_gateway_imports_metatrader5() -> None:
    importers = sorted(name for name, text in sources().items() if IMPORTS_MT5.search(text))
    assert importers == ["mt5/gateway.py"]


def test_the_app_never_imports_test_fakes() -> None:
    offenders = [name for name, text in sources().items() if "tests.fakes" in text]
    assert offenders == []


def test_read_only_tools_never_mention_order_functions() -> None:
    texts = sources()
    offenders = [name for name in READ_ONLY_MODULES if ORDER_CALLS.search(texts[f"mt5/{name}"])]
    assert offenders == []
