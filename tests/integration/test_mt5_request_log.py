"""MT5 requests reach the `mt5` log files through the real loguru pipeline, masked."""

import json
import tempfile
from pathlib import Path

from app.mt5.gateway import MT5Gateway
from app.mt5.request_log import log_event, log_request
from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.files import LogFilePolicy, LogFileWriter
from app.observability.levels import LevelRegistry, LogLevel
from app.observability.logger import LogPipeline
from app.observability.masking import SecretMasker
from tests.fakes.fake_mt5 import FakeAccount, FakeMT5


def test_requests_and_events_are_logged_to_the_mt5_category_without_the_password() -> None:
    account = FakeAccount()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        registry = LevelRegistry(overrides={LogCategory.MT5: LogLevel.DEBUG})
        writer = LogFileWriter(root / "logs", LogFilePolicy(retention_days=100_000))
        pipeline = LogPipeline(writer, RecentLogBuffer(), registry, SecretMasker())
        pipeline.start()
        gateway = MT5Gateway(FakeMT5, on_request=log_request, idle_seconds=0.05)
        gateway.start()
        try:
            gateway.call("initialize")
            gateway.call("login", account.login, password=account.password, server=account.server)
            log_event("WARNING", "MT5 connection lost (test)")
        finally:
            gateway.stop()
            pipeline.flush()
            pipeline.stop()
        files = sorted((root / "logs" / "mt5").glob("*.jsonl"))
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    entries = [json.loads(line) for line in text.splitlines() if line]
    contexts = [entry.get("context", {}) for entry in entries]
    assert [context["request"] for context in contexts if "request" in context] == [
        "initialize",
        "login",
    ]
    login = next(context for context in contexts if context.get("request") == "login")
    assert login["arguments"]["password"] == "***"
    assert account.password not in text
    assert any(entry["message"] == "MT5 connection lost (test)" for entry in entries)
