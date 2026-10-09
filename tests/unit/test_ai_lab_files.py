"""The Files panel of the AI Lab (docs/NOCURVE_V2.md 20e3): only the Lab's exports, newest
first, real sizes; nothing outside that folder opens."""

from __future__ import annotations

import os
from pathlib import Path

from app.ai.lab_files import inside_exports, lab_files, size_text


def test_only_the_lab_exports_are_listed_newest_first(tmp_path: Path) -> None:
    assert lab_files(tmp_path) == []
    old = tmp_path / "exports" / "ai_20261001_100000"
    new = tmp_path / "exports" / "ai_20261009_140200"
    other = tmp_path / "exports" / "trades_20261009"
    for folder in (old, new, other):
        folder.mkdir(parents=True)
    (old / "report.md").write_bytes(b"x" * 2048)
    (new / "report.md").write_bytes(b"y" * 10)
    (other / "trades.csv").write_bytes(b"z")
    os.utime(old / "report.md", (1_000_000, 1_000_000))
    os.utime(new / "report.md", (2_000_000, 2_000_000))
    found = lab_files(tmp_path)
    assert [(item.folder, item.size) for item in found] == [
        ("ai_20261009_140200", 10),
        ("ai_20261001_100000", 2048),
    ]
    assert found[0].name == "report.md"
    assert inside_exports(tmp_path, found[0].path)
    assert not inside_exports(tmp_path, tmp_path / "secret.txt")
    assert size_text(10) == "10 B" and size_text(2048) == "2 KB"
    assert size_text(3 * 1024 * 1024) == "3.0 MB"
