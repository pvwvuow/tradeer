"""The debug bundle (spec E3, I6): one zip to attach to a bug report or to give to an AI.

It holds the newest log files and crash reports, the profile's settings files, the latest
health checks and performance metrics, the versions and the last decision traces, plus
`README_DEBUG.md`, which says what is inside and gives a prompt for an AI. All text goes
through the secret masker once more (logs are already masked when written; this is the
second net) and account logins are hidden as well. Big log files are cut to their newest part
so the zip stays small enough to attach.
"""

from __future__ import annotations

import dataclasses
import json
import platform
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from app.__version__ import __version__
from app.observability.crash_handler import package_versions
from app.observability.masking import MASK, MASKER, SecretMasker

BUNDLE_FOLDER = "debug"
README_NAME = "README_DEBUG.md"
DAY = 86_400.0
LOG_DAYS = 2
MAX_LOG_BYTES = 24 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_CRASH_REPORTS = 20
MAX_SETTINGS_BYTES = 1024 * 1024
TRACE_LIMIT = 50
TEXT_SUFFIXES = frozenset({".jsonl", ".log", ".txt", ".json", ".md"})
CUT_NOTE = "[older lines cut: the file is bigger than a debug bundle allows]\n"
AI_PROMPT = (
    "Find the root cause of this problem. The attached zip is a debug bundle of MT5 Trading "
    "Workstation, a Windows desktop app (Python, PySide6) that analyses and trades through "
    "MetaTrader 5. Read README_DEBUG.md first, then health.json and performance.json, then "
    "the ERROR and CRITICAL lines in logs/ (JSON lines per category; all.log is the readable "
    "version) and any crash_reports/. Tell me: 1) what went wrong and when (UTC), 2) the "
    "evidence (quote the lines), 3) the most likely cause, 4) how to fix or avoid it, 5) what "
    "to check next if the evidence is not enough. Do not guess beyond the evidence."
)

CONTENTS = (
    ("versions.json", "App, Python, Windows and package versions."),
    ("health.json", "The latest health checks, the background workers and recent issues."),
    ("performance.json", "CPU, memory, latencies and queues with their budgets."),
    ("decision_traces.json", "The last decision traces: why each signal was taken or not."),
    ("settings/", "The profile's settings files (secrets and logins masked)."),
    ("logs/", f"The log files of the last {LOG_DAYS} days (the newest part of big files)."),
    ("crash_reports/", f"The newest {MAX_CRASH_REPORTS} crash reports."),
)


@dataclass(frozen=True)
class BundleInputs:
    log_dir: Path
    crash_dir: Path
    settings_dir: Path
    versions: Mapping[str, Any] = field(default_factory=dict)
    health: Mapping[str, Any] = field(default_factory=dict)
    performance: Mapping[str, Any] = field(default_factory=dict)
    traces: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class BundleResult:
    path: Path
    files: tuple[str, ...]
    cut: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def text(self) -> str:
        megabytes = self.size / (1024 * 1024)
        return f"Debug bundle saved: {self.path} ({len(self.files)} files, {megabytes:.1f} MB)"


def bundle_name(now: float) -> str:
    return "debug-" + datetime.fromtimestamp(now, UTC).strftime("%Y%m%d-%H%M%S") + ".zip"


def _utc(seconds: float) -> str:
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def plain(value: Any) -> Any:
    """Dataclasses, enums, paths and tuples as JSON-ready values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {item.name: plain(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(item) for item in value]
    return value


def _is_login(key: str) -> bool:
    lowered = key.lower()
    return lowered == "login" or lowered.endswith("_login")


def hide_logins(value: Any) -> Any:
    """Replace account login numbers (keys `login`, `*_login`) with the mask."""
    if isinstance(value, Mapping):
        return {
            key: MASK if _is_login(str(key)) and item not in (None, "") else hide_logins(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [hide_logins(item) for item in value]
    return value


def masked_json(value: Any, masker: SecretMasker = MASKER) -> str:
    cleaned = hide_logins(masker.mask_value(plain(value)))
    return json.dumps(cleaned, indent=2, ensure_ascii=False, default=str) + "\n"


def versions(profile: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "app_version": __version__,
        "python": platform.python_version(),
        "os": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "profile": profile,
        "packages": package_versions(),
        **dict(extra or {}),
    }


def read_tail(path: Path, limit: int) -> tuple[str, bool]:
    """The file as text, or its newest `limit` bytes from a line start (True when cut)."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= limit:
            return handle.read().decode("utf-8", errors="replace"), False
        handle.seek(size - limit)
        data = handle.read()
    newline = data.find(b"\n")
    if newline >= 0:
        data = data[newline + 1 :]
    return CUT_NOTE + data.decode("utf-8", errors="replace"), True


def newest_files(folder: Path, since: float | None = None) -> list[Path]:
    """Text files below `folder`, newest first (modified at or after `since` if given)."""
    stamped: list[tuple[float, Path]] = []
    try:
        candidates = [path for path in folder.rglob("*") if path.is_file()]
    except OSError:
        return []
    for path in candidates:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if since is None or modified >= since:
            stamped.append((modified, path))
    stamped.sort(key=lambda item: item[0], reverse=True)
    return [path for _modified, path in stamped]


def readme(
    now: float,
    files: Sequence[str],
    cut: Sequence[str],
    skipped: Sequence[str],
    version: str = "",
) -> str:
    lines = [
        "# Debug bundle",
        "",
        f"Created {_utc(now)} by MT5 Trading Workstation {version}".rstrip() + ".",
        "",
        "Secrets are masked as `***` (passwords, tokens, API keys, account logins). Look "
        "through it before you share it anyway.",
        "",
        "## What is inside",
        "",
        *[f"- `{name}`: {text}" for name, text in CONTENTS],
        "",
        "## Files",
        "",
        *[f"- `{name}`" for name in files],
    ]
    if cut:
        lines += ["", "Cut to their newest part: " + ", ".join(f"`{name}`" for name in cut)]
    if skipped:
        lines += ["", "Left out (bundle size limit): " + ", ".join(f"`{n}`" for n in skipped)]
    lines += [
        "",
        "## Ask an AI",
        "",
        "Give an AI assistant this zip (or its files) together with this prompt:",
        "",
        "```text",
        AI_PROMPT,
        "```",
        "",
    ]
    return "\n".join(lines)


class _Writer:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.files: list[str] = []
        self.cut: list[str] = []
        self.skipped: list[str] = []

    def put(self, name: str, text: str) -> None:
        self.archive.writestr(name, text)
        self.files.append(name)


def _settings(writer: _Writer, folder: Path, masker: SecretMasker) -> None:
    try:
        candidates = sorted(path for path in folder.glob("*.json") if path.is_file())
    except OSError:
        return
    for path in candidates:
        name = f"settings/{path.name}"
        try:
            if path.stat().st_size > MAX_SETTINGS_BYTES:
                writer.skipped.append(name)
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            writer.put(name, masked_json(json.loads(text), masker))
        except ValueError:
            writer.put(name, masker.mask(text))


def _copy_text(
    writer: _Writer,
    prefix: str,
    root: Path,
    paths: Sequence[Path],
    budget: int,
    masker: SecretMasker,
) -> None:
    for path in paths:
        name = f"{prefix}/{path.relative_to(root).as_posix()}"
        if budget <= 0:
            writer.skipped.append(name)
            continue
        try:
            text, was_cut = read_tail(path, min(MAX_FILE_BYTES, budget))
        except OSError:
            continue
        budget -= len(text.encode("utf-8"))
        writer.put(name, masker.mask(text))
        if was_cut:
            writer.cut.append(name)


def build_bundle(
    inputs: BundleInputs,
    target_dir: Path,
    *,
    now: float,
    masker: SecretMasker = MASKER,
) -> BundleResult:
    """Write `debug-<UTC time>.zip` into `target_dir` and say what went in."""
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / bundle_name(now)
    partial = path.with_name(path.name + ".part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        writer = _Writer(archive)
        writer.put("versions.json", masked_json(inputs.versions, masker))
        writer.put("health.json", masked_json(inputs.health, masker))
        writer.put("performance.json", masked_json(inputs.performance, masker))
        writer.put("decision_traces.json", masked_json(list(inputs.traces), masker))
        _settings(writer, inputs.settings_dir, masker)
        logs = newest_files(inputs.log_dir, since=now - LOG_DAYS * DAY)
        _copy_text(writer, "logs", inputs.log_dir, logs, MAX_LOG_BYTES, masker)
        crashes = newest_files(inputs.crash_dir)[:MAX_CRASH_REPORTS]
        _copy_text(writer, "crash_reports", inputs.crash_dir, crashes, MAX_LOG_BYTES, masker)
        version = str(inputs.versions.get("app_version", ""))
        files = [*writer.files, README_NAME]
        archive.writestr(README_NAME, readme(now, files, writer.cut, writer.skipped, version))
    partial.replace(path)
    return BundleResult(path, tuple(files), tuple(writer.cut), tuple(writer.skipped))
