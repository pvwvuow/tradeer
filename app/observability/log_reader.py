"""Saved log files read back (spec E3, F3 page 12): the Logs page's time ranges, the full
trace view and the export.

The writer keeps `logs/<category>/<date>.jsonl` and zips closed files next to them
(`<date>.<stamp>.jsonl.zip`). Reading is plain file work with no Qt, so the page runs it in
a background thread and the tests call it directly. Every line was masked when it was
written; an export writes the lines as they are.
"""

from __future__ import annotations

import csv
import json
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.files import JSONL_SUFFIX, ZIP_SUFFIX

Entry = dict[str, Any]
Keep = Callable[[Mapping[str, Any]], bool]

MAX_ENTRIES = 2000
EXPORT_FOLDER = "exports"
CSV_FIELDS = (
    "time",
    "level",
    "category",
    "symbol",
    "strategy",
    "trace_id",
    "signal_id",
    "trade_id",
    "module",
    "function",
    "message",
)


def entry_time(entry: Mapping[str, Any]) -> float | None:
    """The UTC seconds of a log entry's ISO `time`, None when it has none."""
    text = str(entry.get("time") or "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def log_files(root: Path, since: float | None = None) -> list[Path]:
    """The JSON line files (plain and zipped) below `root`, skipping ones older than `since`."""
    found: list[Path] = []
    try:
        candidates = sorted(path for path in root.rglob("*") if path.is_file())
    except OSError:
        return []
    for path in candidates:
        name = path.name
        if not (name.endswith(JSONL_SUFFIX) or name.endswith(JSONL_SUFFIX + ZIP_SUFFIX)):
            continue
        try:
            if since is not None and path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        found.append(path)
    return found


def file_lines(path: Path) -> Iterator[str]:
    """The text lines of a log file, also inside a rotated zip."""
    try:
        if path.name.endswith(ZIP_SUFFIX):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if member.endswith(JSONL_SUFFIX):
                        data = archive.read(member).decode("utf-8", errors="replace")
                        yield from data.splitlines()
            return
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                yield line.rstrip("\n")
    except (OSError, zipfile.BadZipFile):
        return


def _newest(entries: list[Entry], limit: int) -> list[Entry]:
    entries.sort(key=lambda entry: str(entry.get("time") or ""))
    return entries[-limit:]


def read_entries(
    root: Path,
    *,
    since: float | None = None,
    keep: Keep | None = None,
    contains: str = "",
    limit: int = MAX_ENTRIES,
) -> list[Entry]:
    """The newest `limit` saved entries that pass `keep`, oldest first.

    `contains` is a quick text test on the raw line before it is parsed (a trace id).
    """
    found: list[Entry] = []
    for path in log_files(root, since):
        for line in file_lines(path):
            if not line or (contains and contains not in line):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if keep is not None and not keep(entry):
                continue
            found.append(entry)
            if len(found) > 4 * limit:
                found = _newest(found, limit)
    return _newest(found, limit)


def _key(entry: Mapping[str, Any]) -> tuple[str, ...]:
    fields = ("time", "category", "level", "message")
    return tuple(str(entry.get(name) or "") for name in fields)


def merge_entries(*groups: Sequence[Mapping[str, Any]], limit: int = MAX_ENTRIES) -> list[Entry]:
    """Saved and live entries together: each line once, in time order, the newest `limit`."""
    seen: set[tuple[str, ...]] = set()
    merged: list[Entry] = []
    for group in groups:
        for entry in group:
            key = _key(entry)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(entry))
    merged.sort(key=lambda entry: str(entry.get("time") or ""))
    return merged[-limit:]


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    return datetime.fromtimestamp(seconds, UTC).strftime("%H:%M:%S.%f")[:-3]


def timeline_text(trace_id: str, entries: Sequence[Mapping[str, Any]]) -> str:
    """One trace from the first line to the last, with the time since the start."""
    if not entries:
        return f"Trace {trace_id}: no lines found."
    times = [entry_time(entry) for entry in entries]
    known = [moment for moment in times if moment is not None]
    first = min(known) if known else None
    span = (max(known) - first) if known and first is not None else 0.0
    lines = [f"Trace {trace_id}: {len(entries)} lines over {span:,.3f} s (times in UTC)", ""]
    for entry, moment in zip(entries, times, strict=True):
        offset = f"+{moment - first:,.3f} s" if moment is not None and first is not None else ""
        message = str(entry.get("message", "")).splitlines()[0] if entry.get("message") else ""
        lines.append(
            f"{_clock(moment)}  {offset:>12}  {str(entry.get('level', '')):<8} "
            f"{str(entry.get('category', '')):<10} {message}",
        )
    return "\n".join(lines)


def export_name(now: float) -> str:
    return "logs-" + datetime.fromtimestamp(now, UTC).strftime("%Y%m%d-%H%M%S")


def export_entries(
    entries: Sequence[Mapping[str, Any]],
    folder: Path,
    now: float,
) -> tuple[Path, Path]:
    """Write the entries as JSON lines and as CSV; returns both paths."""
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / export_name(now)
    json_path = base.with_suffix(".jsonl")
    csv_path = base.with_suffix(".csv")
    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_FIELDS)
        for entry in entries:
            values = [entry.get(name) for name in CSV_FIELDS]
            writer.writerow(["" if value is None else value for value in values])
    return json_path, csv_path
