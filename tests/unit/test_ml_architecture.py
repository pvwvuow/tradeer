"""Architecture rules for the model code (spec C10, D3)."""

import re

from tests.unit.test_architecture import ORDER_CALLS, sources

PICKLE = re.compile(r"\b(import pickle|pickle\.|joblib)\b")


def test_the_model_code_never_trades_imports_qt_or_unpickles() -> None:
    texts = sources()
    names = [name for name in texts if name.startswith("ml/")]
    assert "ml/model.py" in names and "ml/registry.py" in names
    for name in names:
        text = texts[name]
        assert "PySide6" not in text, name
        assert "live_broker" not in text and not ORDER_CALLS.search(text), name
        assert not re.findall(r"app\.mt5\.(gateway|api|terminal_process)\b", text), name
        assert not PICKLE.search(text), name


def test_only_the_model_wrapper_imports_lightgbm() -> None:
    texts = sources()
    users = sorted(
        name for name, text in texts.items() if re.search(r"^\s*import lightgbm", text, re.M)
    )
    assert users == ["ml/model.py"]
