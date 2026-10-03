"""Architecture rules for analytics, journal and notifications (spec C11, C12, C14, D3)."""

import re

from tests.unit.test_architecture import ORDER_CALLS, sources

PACKAGES = ("analytics/", "journal/", "notify/")


def test_analytics_journal_and_notify_never_trade_or_import_qt() -> None:
    texts = sources()
    names = [name for name in texts if name.startswith(PACKAGES)]
    assert "analytics/stats.py" in names and "notify/telegram.py" in names
    for name in names:
        text = texts[name]
        assert "PySide6" not in text, name
        assert "live_broker" not in text and not ORDER_CALLS.search(text), name
        assert not re.findall(r"app\.mt5\.(gateway|api|terminal_process)\b", text), name


def test_only_the_telegram_module_talks_to_the_network() -> None:
    texts = sources()
    host = re.compile(r"api\.telegram\.org")
    users = sorted(name for name, text in texts.items() if host.search(text))
    assert users == ["notify/telegram.py"]
    network = [n for n in texts if n.startswith(PACKAGES) and "urllib.request" in texts[n]]
    assert network == ["notify/telegram.py"]
