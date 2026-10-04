"""The debug bundle (spec E3, I6): what goes in, what stays out, and that secrets are masked."""

import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.observability import debug_bundle
from app.observability.debug_bundle import (
    AI_PROMPT,
    CUT_NOTE,
    README_NAME,
    BundleInputs,
    build_bundle,
    bundle_name,
    hide_logins,
    plain,
    read_tail,
    versions,
)
from app.observability.health import HealthStatus
from app.observability.masking import MASK, SecretMasker

NOW = 1_790_000_000.0
TOKEN = "sk-" + "a1b2c3d4e5f6g7h8i9j0"


def folders(tmp_path: Path) -> BundleInputs:
    logs, crashes, profile = tmp_path / "logs", tmp_path / "crash_reports", tmp_path / "profile"
    (logs / "app").mkdir(parents=True)
    crashes.mkdir()
    profile.mkdir()
    (logs / "app" / "2026-09-22.jsonl").write_text('{"message": "today"}\n', encoding="utf-8")
    (logs / "all.log").write_text(
        f"INFO started\nWARNING password=hunter2222 key {TOKEN}\n",
        encoding="utf-8",
    )
    old = logs / "app" / "2026-09-01.jsonl"
    old.write_text('{"message": "old"}\n', encoding="utf-8")
    os.utime(old, (NOW - 10 * 86_400, NOW - 10 * 86_400))
    (logs / "app" / "2026-09-20.jsonl.zip").write_bytes(b"PK")
    for path in (logs / "app" / "2026-09-22.jsonl", logs / "all.log"):
        os.utime(path, (NOW - 60, NOW - 60))
    (crashes / "crash_20260922.json").write_text('{"summary": "boom"}', encoding="utf-8")
    (profile / "account.json").write_text(
        json.dumps({"login": 51234567, "server": "Broker-Demo", "auto_connect": True}),
        encoding="utf-8",
    )
    (profile / "llm.json").write_text(
        json.dumps({"enabled": False, "api_key": "abcdef123456", "model": "gpt-4o-mini"}),
        encoding="utf-8",
    )
    (profile / "broken.json").write_text("{not json password=letmein99", encoding="utf-8")
    return BundleInputs(
        log_dir=logs,
        crash_dir=crashes,
        settings_dir=profile,
        versions={"app_version": "0.18.0", "python": "3.11"},
        health={"summary": "Health: all checks OK", "checks": [{"name": "disk_space"}]},
        performance={"summary": "Performance: within the budgets"},
        traces=[{"trace_id": "t1", "final_decision": "rejected", "steps": []}],
    )


def read(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def test_the_bundle_holds_everything_masked(tmp_path: Path) -> None:
    result = build_bundle(folders(tmp_path), tmp_path / "debug", now=NOW, masker=SecretMasker())
    assert result.path.name == bundle_name(NOW) == "debug-20260921-141320.zip"
    assert result.path.exists() and result.size > 0 and "files" in result.text
    files = read(result.path)
    assert set(files) == {
        "versions.json",
        "health.json",
        "performance.json",
        "decision_traces.json",
        "settings/account.json",
        "settings/broken.json",
        "settings/llm.json",
        "logs/app/2026-09-22.jsonl",
        "logs/all.log",
        "crash_reports/crash_20260922.json",
        README_NAME,
    }
    assert set(result.files) == set(files)
    everything = "\n".join(files.values())
    for secret in ("hunter2222", TOKEN, "abcdef123456", "letmein99", "51234567"):
        assert secret not in everything, secret
    account = json.loads(files["settings/account.json"])
    assert account == {"login": MASK, "server": "Broker-Demo", "auto_connect": True}
    assert json.loads(files["decision_traces.json"])[0]["trace_id"] == "t1"
    assert json.loads(files["versions.json"])["app_version"] == "0.18.0"
    readme = files[README_NAME]
    assert AI_PROMPT in readme and "Find the root cause of this problem." in readme
    assert "0.18.0" in readme and "- `logs/all.log`" in readme
    assert not list((tmp_path / "debug").glob("*.part"))


def test_big_logs_are_cut_to_their_newest_part(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = folders(tmp_path)
    lines = "".join(f"line {number:05d}\n" for number in range(2_000))
    (inputs.log_dir / "all.log").write_bytes(lines.encode("utf-8"))  # \n also on Windows
    os.utime(inputs.log_dir / "all.log", (NOW - 5, NOW - 5))
    monkeypatch.setattr(debug_bundle, "MAX_FILE_BYTES", 120)
    result = build_bundle(inputs, tmp_path / "debug", now=NOW, masker=SecretMasker())
    text = read(result.path)["logs/all.log"]
    assert text.startswith(CUT_NOTE) and text.endswith("line 01999\n")
    assert "line 00000" not in text
    assert "logs/all.log" in result.cut
    assert "Cut to their newest part" in read(result.path)[README_NAME]


def test_the_log_budget_leaves_out_the_rest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = folders(tmp_path)
    monkeypatch.setattr(debug_bundle, "MAX_LOG_BYTES", 10)
    result = build_bundle(inputs, tmp_path / "debug", now=NOW, masker=SecretMasker())
    assert result.skipped  # the second log file did not fit any more
    assert "Left out" in read(result.path)[README_NAME]


def test_read_tail(tmp_path: Path) -> None:
    path = tmp_path / "x.log"
    path.write_bytes(b"first\nsecond\nthird\n")
    assert read_tail(path, 1000) == ("first\nsecond\nthird\n", False)
    text, cut = read_tail(path, 10)
    assert cut and text == CUT_NOTE + "third\n"


@dataclass(frozen=True)
class Row:
    name: str
    status: HealthStatus
    where: Path


def test_plain_values_logins_and_versions() -> None:
    row = Row("disk", HealthStatus.WARNING, Path("a"))
    assert plain([row, (1, 2)]) == [{"name": "disk", "status": "warning", "where": "a"}, [1, 2]]
    nested = {"accounts": [{"login": 123, "trade_login": "77", "name": "x"}], "login": None}
    assert hide_logins(nested) == {
        "accounts": [{"login": MASK, "trade_login": MASK, "name": "x"}],
        "login": None,
    }
    found = versions("default", {"mt5_connection": "connected"})
    assert found["profile"] == "default" and found["mt5_connection"] == "connected"
    assert "app_version" in found and "PySide6" in found["packages"]
