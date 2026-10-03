"""MT5 errors and plain-language explanations with a fix for each (spec C1, I3)."""

from __future__ import annotations

from dataclasses import dataclass

from app.mt5 import api


class MT5Error(Exception):
    """A failed MT5 call. `code` and `detail` come from `last_error()` when available."""

    def __init__(self, title: str, fix: str, code: int | None = None, detail: str = "") -> None:
        super().__init__(f"{title} ({code}: {detail})" if code is not None else title)
        self.title = title
        self.fix = fix
        self.code = code
        self.detail = detail


class MT5Timeout(MT5Error):
    """The gateway did not answer in time. The MT5 call may still be running."""


class MT5Unavailable(MT5Error):
    """The `MetaTrader5` package could not be loaded."""


@dataclass(frozen=True)
class Explanation:
    title: str
    fix: str


SAME_USER_HINT = (
    "Make sure MT5 and this app run as the same Windows user and at the same level: both "
    'normally, or both with "Run as administrator".'
)

_EXPLANATIONS: dict[int, Explanation] = {
    api.RES_E_AUTH_FAILED: Explanation(
        "Login failed: wrong login number, password or server",
        "Type the login, password and server exactly as in MT5 (File > Login to Trade "
        'Account). The server name is case-sensitive, for example "ICMarketsSC-Demo".',
    ),
    api.RES_E_INTERNAL_FAIL_TIMEOUT: Explanation(
        "The app could not talk to the MT5 terminal in time (IPC timeout)",
        "Wait until MT5 has fully started and shows your account, then press Re-check. "
        + SAME_USER_HINT,
    ),
    api.RES_E_INTERNAL_FAIL_INIT: Explanation(
        "The MT5 terminal did not accept the connection (IPC initialize failed)",
        "Start MT5 once by hand, log in, and close any update or login dialog. Use the 64-bit "
        "MetaTrader 5 (terminal64.exe), not MetaTrader 4. " + SAME_USER_HINT,
    ),
    api.RES_E_INTERNAL_FAIL_CONNECT: Explanation(
        "The connection to the MT5 terminal broke",
        "Check that MT5 is still open, then press Re-check. " + SAME_USER_HINT,
    ),
    api.RES_E_INTERNAL_FAIL_SEND: Explanation(
        "A request could not be sent to the MT5 terminal",
        "MT5 may be busy or closing. Wait a moment and press Re-check.",
    ),
    api.RES_E_INTERNAL_FAIL_RECEIVE: Explanation(
        "No answer arrived from the MT5 terminal",
        "MT5 may be busy or closing. Wait a moment and press Re-check.",
    ),
    api.RES_E_INTERNAL_FAIL: Explanation(
        "MT5 reported an internal error",
        "Restart MT5, then press Re-check. If it repeats, update MT5 (Help > Check for Updates).",
    ),
    api.RES_E_INVALID_PARAMS: Explanation(
        "MT5 rejected the connection settings",
        "Check the terminal path (it must end with terminal64.exe) and the login number.",
    ),
    api.RES_E_NOT_FOUND: Explanation(
        "MT5 could not find what was asked for",
        "Check the terminal path, and that the symbol is shown in MT5's Market Watch.",
    ),
    api.RES_E_INVALID_VERSION: Explanation(
        "This MT5 terminal is too old for the app",
        "Update MT5 (Help > Check for Updates), restart it, then press Re-check.",
    ),
    api.RES_E_UNSUPPORTED: Explanation(
        "This MT5 terminal does not support the request",
        "Update MT5 to the latest build.",
    ),
    api.RES_E_AUTO_TRADING_DISABLED: Explanation(
        "Algo Trading is switched off",
        'Press the "Algo Trading" button in the MT5 toolbar so it turns green.',
    ),
    api.RES_E_NO_MEMORY: Explanation(
        "MT5 ran out of memory",
        "Close other programs, lower Tools > Options > Charts > Max bars in chart, restart MT5.",
    ),
}

_GENERIC = Explanation(
    "MT5 reported an error",
    "Make sure MT5 is open and logged in, then press Re-check. " + SAME_USER_HINT,
)


def explain(code: int | None, detail: str = "", elevated: bool | None = None) -> Explanation:
    """A plain-language title and fix for a `last_error()` code."""
    text = detail.casefold()
    if code is None and "authoriz" in text:
        code = api.RES_E_AUTH_FAILED
    found = _EXPLANATIONS.get(code, _GENERIC) if code is not None else _GENERIC
    if elevated is not None and code in (
        api.RES_E_INTERNAL_FAIL_TIMEOUT,
        api.RES_E_INTERNAL_FAIL_INIT,
        api.RES_E_INTERNAL_FAIL_CONNECT,
    ):
        level = "as administrator" if elevated else "normally (not as administrator)"
        return Explanation(found.title, f"{found.fix} This app is running {level} right now.")
    return found


def error_from_last(
    last_error: tuple[int, str] | None,
    elevated: bool | None = None,
) -> MT5Error:
    code, detail = last_error if last_error else (None, "")
    found = explain(code, detail, elevated)
    return MT5Error(found.title, found.fix, code, detail)
