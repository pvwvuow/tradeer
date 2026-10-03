"""Version numbers of releases (SemVer `MAJOR.MINOR.PATCH`, an optional leading `v`)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version | None:
    """`v0.12.3`, `0.12.3` or `0.12.3-beta.1` (the suffix is ignored); None if unreadable."""
    core = text.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return Version(int(parts[0]), int(parts[1]), int(parts[2]))


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def is_major_update(candidate: str, current: str) -> bool:
    """A breaking release has a higher major number; it is never downloaded on its own."""
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return True
    return new.major > old.major


def tag_for(version: str) -> str:
    return "v" + version.strip().lstrip("vV")
