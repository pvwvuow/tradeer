"""The files of the AI Lab (docs/NOCURVE_V2.md 20e3, the inspector's Files panel): every
export the Lab wrote (`exports/ai_<stamp>/`), newest first, with the real size and time.
Only that folder is listed and opened; nothing else on the disk is touched."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXPORT_FOLDER = "exports"
PREFIX = "ai_"
MAX_FILES = 30


@dataclass(frozen=True)
class LabFile:
    path: Path
    folder: str  # ai_20261009_142233
    size: int  # bytes
    modified: float  # UTC seconds

    @property
    def name(self) -> str:
        return self.path.name


def size_text(size: int) -> str:
    """The design's sizes: 38 KB, 1.2 MB; under a kilobyte in bytes."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:,.0f} KB"
    return f"{size / (1024 * 1024):,.1f} MB"


def lab_files(export_dir: Path, limit: int = MAX_FILES) -> list[LabFile]:
    """The files of the Lab's exports, newest first (a missing folder: none)."""
    root = export_dir / EXPORT_FOLDER
    if not root.is_dir():
        return []
    found: list[LabFile] = []
    for folder in root.iterdir():
        if not folder.is_dir() or not folder.name.startswith(PREFIX):
            continue
        for path in folder.iterdir():
            if not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            found.append(LabFile(path, folder.name, info.st_size, info.st_mtime))
    found.sort(key=lambda item: (-item.modified, item.name))
    return found[:limit]


def inside_exports(export_dir: Path, path: Path) -> bool:
    """`path` is one of the Lab's export files (the only ones the panel opens)."""
    try:
        path.resolve().relative_to((export_dir / EXPORT_FOLDER).resolve())
    except (OSError, ValueError):
        return False
    return path.is_file()
