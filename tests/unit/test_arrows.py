"""Drop-down and spin box arrows are chevron pictures, not CSS triangles that some Windows
set-ups paint as black boxes (0.47.0)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from app.ui.arrows import HEIGHT, SCALE, WIDTH, arrow_file, chevron_alpha, chevron_png, qss_url
from app.ui.theme import DARK, LIGHT, build_qss


def pixels(png: bytes) -> tuple[int, int, bytes]:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    start = png.index(b"IDAT") + 4
    size = struct.unpack(">I", png[start - 8 : start - 4])[0]
    return width, height, zlib.decompress(png[start : start + size])


def test_a_chevron_is_a_small_transparent_png_in_the_colour() -> None:
    width, height, raw = pixels(chevron_png("#52607A"))
    assert (width, height) == (WIDTH * SCALE, HEIGHT * SCALE)
    assert len(raw) == height * (1 + width * 4)
    row = raw[1 : 1 + width * 4]
    corner = row[0:4]
    assert corner[3] < 255  # the corners stay see-through
    alphas = [raw[1 + y * (1 + width * 4) + x * 4 + 3] for y in range(height) for x in range(width)]
    assert max(alphas) == 255 and alphas.count(0) > len(alphas) // 2
    lit = [
        raw[1 + y * (1 + width * 4) + x * 4 : 1 + y * (1 + width * 4) + x * 4 + 3]
        for y in range(height)
        for x in range(width)
        if raw[1 + y * (1 + width * 4) + x * 4 + 3]
    ]
    assert all(bytes(rgb) == bytes((0x52, 0x60, 0x7A)) for rgb in lit)


def test_up_is_down_upside_down() -> None:
    down, up = chevron_alpha("down"), chevron_alpha("up")
    assert down[-1] == up[0] and down[0] == up[-1]
    middle = WIDTH * SCALE // 2
    assert down[-2][middle] > 0.5 and down[0][middle] == 0.0  # a V: the tip is low
    with pytest.raises(ValueError):
        chevron_png("#52607A", "left")
    with pytest.raises(ValueError):
        chevron_png("blue")


def test_the_file_is_written_once_and_named_by_its_colour(tmp_path: Path) -> None:
    path = arrow_file("#9AA8BB", "up", tmp_path)
    assert path is not None and path.name == "chevron-up-9aa8bb.png"
    assert path.read_bytes() == chevron_png("#9AA8BB", "up")
    stamp = path.stat().st_mtime_ns
    assert arrow_file("#9AA8BB", "up", tmp_path) == path and path.stat().st_mtime_ns == stamp
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    assert arrow_file("#9AA8BB", "up", blocked) is None
    assert qss_url(Path("C:/Users/A B/x.png")) == '"C:/Users/A B/x.png"'


def test_the_theme_uses_the_chevrons_and_square_popups() -> None:
    for tokens in (DARK, LIGHT):
        qss = build_qss(tokens)
        assert "chevron-down-" in qss and "chevron-up-" in qss
        assert "border-top: 5px solid" not in qss  # no triangles
        menu = qss[qss.index("QMenu {") :]
        assert "border-radius: 0px" in menu[: menu.index("}")]  # no black corners
        assert "QComboBox QAbstractItemView::item" in qss
