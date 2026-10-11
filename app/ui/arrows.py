"""The arrows of drop-downs and spin boxes as small chevron pictures (0.47.0).

The style sheet drew them as CSS border triangles; on some Windows set-ups Qt paints those
as one or two black boxes. Now each arrow is a chevron drawn here, in pure Python (no image
files to ship, no Qt needed): a PNG at twice the size, smoothed edges, in the theme's colour,
written once into the temporary folder and named by its colour (`arrow_file`). When the
folder cannot be written the theme keeps the triangles.
"""

from __future__ import annotations

import math
import struct
import tempfile
import zlib
from pathlib import Path

WIDTH, HEIGHT = 10, 6  # the arrow in logical pixels
SCALE = 2  # drawn at twice the size: sharp on high-DPI screens
STROKE = 1.6
FOLDER = "mt5-workstation-ui"
DIRECTIONS = ("down", "up")

Point = tuple[float, float]


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(part * 2 for part in value)
    if len(value) != 6:
        raise ValueError(f"not a #RRGGBB colour: {color}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _distance(point: Point, start: Point, end: Point) -> float:
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def chevron_points(direction: str) -> tuple[Point, Point, Point]:
    """The chevron's three points in logical pixels (down: a V, up: a caret)."""
    left, middle, right = (1.0, 1.0), (WIDTH / 2, HEIGHT - 1.0), (WIDTH - 1.0, 1.0)
    if direction == "up":
        return (left[0], HEIGHT - left[1]), (middle[0], 1.0), (right[0], HEIGHT - right[1])
    return left, middle, right


def chevron_alpha(direction: str, scale: int = SCALE) -> list[list[float]]:
    """How much of each pixel the chevron covers (0 to 1), row by row."""
    first, middle, last = chevron_points(direction)
    half = STROKE / 2
    rows: list[list[float]] = []
    for y in range(HEIGHT * scale):
        row: list[float] = []
        for x in range(WIDTH * scale):
            centre = ((x + 0.5) / scale, (y + 0.5) / scale)
            near = min(_distance(centre, first, middle), _distance(centre, middle, last))
            row.append(max(0.0, min(1.0, (half - near) * scale + 0.5)))
        rows.append(row)
    return rows


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def chevron_png(color: str, direction: str = "down", scale: int = SCALE) -> bytes:
    """A chevron as a PNG with transparency, in `color`."""
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction: {direction}")
    red, green, blue = _rgb(color)
    raw = bytearray()
    for row in chevron_alpha(direction, scale):
        raw.append(0)  # no filter
        for alpha in row:
            raw.extend((red, green, blue, round(alpha * 255)))
    header = struct.pack(">IIBBBBB", WIDTH * scale, HEIGHT * scale, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def arrow_folder() -> Path:
    return Path(tempfile.gettempdir()) / FOLDER


def arrow_file(color: str, direction: str = "down", folder: Path | None = None) -> Path | None:
    """The chevron's file (written once), or None when it cannot be written."""
    where = folder if folder is not None else arrow_folder()
    name = f"chevron-{direction}-{color.lstrip('#').lower()}.png"
    path = where / name
    try:
        data = chevron_png(color, direction)
        if not path.is_file() or path.read_bytes() != data:
            where.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    except (OSError, ValueError):
        return None
    return path


def qss_url(path: Path) -> str:
    """A path for a style sheet's url(): forward slashes, in quotes."""
    return '"' + path.as_posix().replace('"', "") + '"'
