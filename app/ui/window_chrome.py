"""The Windows title bar in the app's theme (7 October 2026: "the UI looks old").

Windows draws a white title bar over the dark app by default, which looked dated. On
Windows 10 (20H1 and newer) the title bar follows the theme's dark or light mode; on
Windows 11 it also takes the top bar's color, the theme's text color and a round corner, so
the window reads as one surface. Elsewhere (Linux CI, the offscreen test platform) nothing
happens. A Windows build that does not know an attribute just ignores it.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from app.ui.theme import ThemeTokens

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_ROUND = 2
ON_WINDOWS = sys.platform == "win32"


def colorref(hex_color: str) -> int:
    """A "#RRGGBB" color as the COLORREF Windows wants (0x00BBGGRR)."""
    red, green, blue = (int(hex_color[index : index + 2], 16) for index in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


def title_bar_attributes(tokens: ThemeTokens) -> list[tuple[int, int]]:
    """(DWM attribute, value) pairs that give the title bar the theme's look."""
    return [
        (DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if tokens.dark else 0),
        (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
        (DWMWA_CAPTION_COLOR, colorref(tokens.surface)),
        (DWMWA_TEXT_COLOR, colorref(tokens.text)),
        (DWMWA_BORDER_COLOR, colorref(tokens.border)),
    ]


def _dwm() -> Any:
    if not ON_WINDOWS or QGuiApplication.platformName() != "windows":
        return None
    loader = getattr(ctypes, "windll", None)
    if loader is None:
        return None
    try:
        return loader.dwmapi
    except OSError:
        return None


def style_title_bar(window: QWidget, tokens: ThemeTokens) -> int:
    """Give `window`'s title bar the theme's colors; returns how many attributes Windows took."""
    dwm = _dwm()
    if dwm is None:
        return 0
    handle = ctypes.c_void_p(int(window.winId()))
    applied = 0
    for attribute, value in title_bar_attributes(tokens):
        data = ctypes.c_int(value)
        try:
            result = dwm.DwmSetWindowAttribute(
                handle,
                ctypes.c_uint(attribute),
                ctypes.byref(data),
                ctypes.sizeof(data),
            )
        except OSError:
            continue
        if result == 0:
            applied += 1
    return applied
