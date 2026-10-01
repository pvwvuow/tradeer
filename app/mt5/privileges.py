"""Is this process running as administrator? (spec I2: MT5 and the app must match.)"""

from __future__ import annotations

import sys


def is_elevated() -> bool | None:
    """True or False on Windows; None where it cannot be determined."""
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None
