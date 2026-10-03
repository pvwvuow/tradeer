"""The Test-connection checklist (spec C1, I3): one plain-language line per step, with a fix.

`run_checklist` runs inside the gateway thread with the real package or FakeMT5. It only
reads: it never sends, checks or modifies an order.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PureWindowsPath

from app.mt5.api import TIMEFRAMES, MT5Api
from app.mt5.errors import explain
from app.mt5.models import AccountSnapshot, Quote, TerminalSnapshot
from app.mt5.symbols import resolve_symbol

DEFAULT_SYMBOLS: tuple[str, ...] = ("EURUSD", "GBPUSD", "XAUUSD")
LOGIN_TIMEOUT_MS = 60_000
HISTORY_TIMEFRAME = "M15"
HISTORY_PROBE_BARS = 1000
MIN_MAX_BARS = 100_000
TERMINAL_EXE = "terminal64.exe"


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"

    @property
    def mark(self) -> str:
        return {"ok": "\u2713", "warn": "!", "fail": "\u2717", "skipped": "-"}[self.value]


@dataclass(frozen=True)
class CheckItem:
    key: str
    title: str
    status: CheckStatus
    value: str = ""
    fix: str = ""

    def line(self) -> str:
        text = f"[{self.status.mark}] {self.title}"
        if self.value:
            text = f"{text}: {self.value}"
        if self.fix and self.status in (CheckStatus.WARN, CheckStatus.FAIL):
            text = f"{text}\n      Fix: {self.fix}"
        return text


@dataclass(frozen=True)
class ConnectRequest:
    terminal_path: str = ""
    login: int | None = None
    password: str = field(default="", repr=False)
    server: str = ""
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    timeout_ms: int = LOGIN_TIMEOUT_MS


@dataclass(frozen=True)
class ChecklistReport:
    items: tuple[CheckItem, ...]
    terminal: TerminalSnapshot | None = None
    account: AccountSnapshot | None = None
    symbols: tuple[str, ...] = ()
    quotes: tuple[Quote, ...] = ()

    @property
    def connected(self) -> bool:
        """Logged in and connected to the broker: enough for analysis."""
        return (
            self.account is not None
            and self.terminal is not None
            and self.terminal.connected
            and self.status("login") is CheckStatus.OK
        )

    @property
    def analysis_only(self) -> bool:
        return self.account is not None and self.account.read_only

    @property
    def ready_to_trade(self) -> bool:
        failed = any(item.status is CheckStatus.FAIL for item in self.items)
        return self.connected and not failed and not self.analysis_only

    def status(self, key: str) -> CheckStatus | None:
        return next((item.status for item in self.items if item.key == key), None)

    def first_problem(self) -> CheckItem | None:
        return next((item for item in self.items if item.status is CheckStatus.FAIL), None)

    def lines(self) -> list[str]:
        return [item.line() for item in self.items]


STEPS: tuple[tuple[str, str], ...] = (
    ("terminal_found", "MetaTrader 5 terminal found"),
    ("terminal_running", "Terminal running"),
    ("login", "Logged in"),
    ("account", "Account information loaded"),
    ("broker_connection", "Connected to the broker server"),
    ("algo_trading", "Algo Trading enabled in MT5"),
    ("account_trading", "Trading allowed on this account"),
    ("symbols", "Symbols available"),
    ("quotes", "Live prices"),
    ("history", "Price history available"),
)


class _Checks:
    def __init__(self) -> None:
        self.items: list[CheckItem] = []

    def add(self, key: str, status: CheckStatus, value: str = "", fix: str = "") -> None:
        title = dict(STEPS)[key]
        self.items.append(CheckItem(key, title, status, value, fix))

    def skip_rest(self) -> None:
        done = {item.key for item in self.items}
        for key, title in STEPS:
            if key not in done:
                self.items.append(CheckItem(key, title, CheckStatus.SKIPPED, "not checked"))


def check_terminal_path(path: str, path_exists: Callable[[str], bool]) -> CheckItem:
    title = dict(STEPS)["terminal_found"]
    if not path:
        return CheckItem(
            "terminal_found",
            title,
            CheckStatus.WARN,
            "no terminal selected: MT5 uses the terminal that was started last",
            "Pick your broker's terminal in the list so the app always opens the right one.",
        )
    name = PureWindowsPath(path).name.casefold()
    if name == "terminal.exe":
        return CheckItem(
            "terminal_found",
            title,
            CheckStatus.FAIL,
            f"{path} is MetaTrader 4 or the 32-bit MetaTrader 5",
            "Install and select the 64-bit MetaTrader 5 (terminal64.exe).",
        )
    if name != TERMINAL_EXE:
        return CheckItem(
            "terminal_found",
            title,
            CheckStatus.FAIL,
            f"{path} is not terminal64.exe",
            "Use Browse to select terminal64.exe in your MetaTrader 5 folder.",
        )
    if not path_exists(path):
        return CheckItem(
            "terminal_found",
            title,
            CheckStatus.FAIL,
            f"nothing at {path}",
            "MT5 may have moved or been uninstalled. Press Refresh or use Browse.",
        )
    return CheckItem("terminal_found", title, CheckStatus.OK, path)


def read_quotes(mt5: MT5Api, symbols: Sequence[str]) -> tuple[Quote, ...]:
    quotes: list[Quote] = []
    for name in symbols:
        tick = mt5.symbol_info_tick(name)
        if tick is None:
            continue
        info = mt5.symbol_info(name)
        digits = int(getattr(info, "digits", 5)) if info is not None else 5
        quotes.append(Quote.from_mt5(name, tick, digits))
    return tuple(quotes)


def run_checklist(
    mt5: MT5Api,
    request: ConnectRequest,
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
    elevated: bool | None = None,
) -> ChecklistReport:
    checks = _Checks()
    found = check_terminal_path(request.terminal_path, path_exists)
    checks.items.append(found)
    if found.status is CheckStatus.FAIL:
        checks.skip_rest()
        return ChecklistReport(tuple(checks.items))

    # Terminal running: initialize() attaches to MT5, or starts it and waits.
    options: dict[str, object] = {"timeout": request.timeout_ms}
    started = (
        mt5.initialize(request.terminal_path, **options)
        if request.terminal_path
        else mt5.initialize(**options)
    )
    if not started:
        problem = explain(*mt5.last_error(), elevated=elevated)
        checks.add("terminal_running", CheckStatus.FAIL, problem.title, problem.fix)
        checks.skip_rest()
        return ChecklistReport(tuple(checks.items))
    terminal = TerminalSnapshot.from_mt5(mt5.terminal_info(), mt5.version())
    checks.add("terminal_running", CheckStatus.OK, f"{terminal.name}, build {terminal.build}")

    # Login: the saved account, or the one already logged in to MT5.
    if request.login is not None:
        credentials: dict[str, object] = {"timeout": request.timeout_ms}
        if request.password:
            credentials["password"] = request.password
        if request.server:
            credentials["server"] = request.server
        if not mt5.login(request.login, **credentials):
            problem = explain(*mt5.last_error(), elevated=elevated)
            checks.add("login", CheckStatus.FAIL, problem.title, problem.fix)
            checks.skip_rest()
            return ChecklistReport(tuple(checks.items), terminal)
        server = request.server or "the saved server"
        checks.add("login", CheckStatus.OK, f"{request.login} on {server}")
    else:
        checks.add("login", CheckStatus.OK, "using the account that is logged in to MT5")

    raw_account = mt5.account_info()
    if raw_account is None:
        checks.add(
            "account",
            CheckStatus.FAIL,
            "MT5 is not logged in to a trading account",
            "In MT5 use File > Login to Trade Account, or enter login, password and server here.",
        )
        checks.skip_rest()
        return ChecklistReport(tuple(checks.items), terminal)
    account = AccountSnapshot.from_mt5(raw_account)
    terminal = TerminalSnapshot.from_mt5(mt5.terminal_info(), mt5.version())
    if request.login is not None and account.login != request.login:
        checks.add(
            "account",
            CheckStatus.WARN,
            f"MT5 shows account {account.login}, not {request.login}",
            "Log in to the right account in MT5, or correct the login number here.",
        )
    else:
        checks.add("account", CheckStatus.OK, f"{account.summary()} · {account.stop_out_text()}")

    if terminal.connected:
        checks.add("broker_connection", CheckStatus.OK, f"ping {terminal.ping_ms:.0f} ms")
    else:
        checks.add(
            "broker_connection",
            CheckStatus.FAIL,
            'MT5 shows "No connection" to the broker',
            "Check the internet connection and the server name. The bottom-right corner of MT5 "
            'must show a ping, not "No connection" or "Invalid account".',
        )

    if terminal.tradeapi_disabled:
        checks.add(
            "algo_trading",
            CheckStatus.FAIL,
            "trading through the Python API is disabled",
            'In MT5 open Tools > Options > Expert Advisors and untick "Disable algorithmic '
            'trading via external Python API".',
        )
    elif terminal.trade_allowed:
        checks.add("algo_trading", CheckStatus.OK, "on")
    else:
        checks.add(
            "algo_trading",
            CheckStatus.FAIL,
            "off",
            'Press the "Algo Trading" button in the MT5 toolbar so it turns green, then press '
            "Re-check. Analysis works without it; trading does not.",
        )

    if account.read_only:
        checks.add(
            "account_trading",
            CheckStatus.WARN,
            "read-only login (investor password): the app switches to Analysis-only",
            "To trade later, log in with the master password instead of the investor password.",
        )
    elif not account.trade_expert:
        checks.add(
            "account_trading",
            CheckStatus.FAIL,
            "the broker does not allow automated trading on this account",
            "Ask the broker to enable Expert Advisors (algorithmic trading) for this account.",
        )
    else:
        checks.add("account_trading", CheckStatus.OK, "allowed")

    available = [str(getattr(item, "name", "")) for item in (mt5.symbols_get() or ())]
    resolved: list[str] = []
    missing: list[str] = []
    for wanted in request.symbols:
        name = resolve_symbol(wanted, available)
        if name is None or not mt5.symbol_select(name, True):
            missing.append(wanted)
        else:
            resolved.append(name)
    mapping = ", ".join(
        f"{wanted} = {name}" if wanted != name else name
        for wanted, name in zip(
            [wanted for wanted in request.symbols if wanted not in missing],
            resolved,
            strict=True,
        )
    )
    value = f"{mapping or 'none'} ({len(available)} symbols at this broker)"
    if not resolved:
        checks.add(
            "symbols",
            CheckStatus.FAIL,
            value,
            "Open Market Watch in MT5 (Ctrl+M), right-click, choose Show All, then Re-check.",
        )
    elif missing:
        checks.add(
            "symbols",
            CheckStatus.WARN,
            f"{value}; not found: {', '.join(missing)}",
            "This broker may use other names. Add the symbols to Market Watch in MT5.",
        )
    else:
        checks.add("symbols", CheckStatus.OK, value)

    quotes = read_quotes(mt5, resolved)
    valid = [quote for quote in quotes if quote.valid]
    if valid and len(valid) == len(resolved):
        checks.add("quotes", CheckStatus.OK, "; ".join(quote.text() for quote in valid))
    elif valid:
        checks.add(
            "quotes",
            CheckStatus.WARN,
            "; ".join(quote.text() for quote in valid),
            "Some symbols have no price yet. The market may be closed for them.",
        )
    elif resolved:
        checks.add(
            "quotes",
            CheckStatus.WARN,
            "no prices yet",
            "The market may be closed (weekend) or MT5 is still loading. Press Re-check later.",
        )
    else:
        checks.add("quotes", CheckStatus.SKIPPED, "no symbols")

    if resolved:
        timeframe = TIMEFRAMES[HISTORY_TIMEFRAME]
        rates = mt5.copy_rates_from_pos(resolved[0], timeframe, 0, HISTORY_PROBE_BARS)
        bars = len(rates) if rates is not None else 0
        value = f"{resolved[0]} {HISTORY_TIMEFRAME}: {bars} bars; Max bars in chart: "
        value += "unlimited" if terminal.maxbars >= 2**31 - 1 else f"{terminal.maxbars:,}"
        if bars == 0:
            checks.add(
                "history",
                CheckStatus.FAIL,
                value,
                f"Open a {resolved[0]} chart in MT5 once so it downloads history, then Re-check.",
            )
        elif terminal.maxbars < MIN_MAX_BARS:
            checks.add(
                "history",
                CheckStatus.WARN,
                value,
                "Backtests and model training need long history. In MT5 set Tools > Options > "
                "Charts > Max bars in chart to 100000 or Unlimited and restart MT5.",
            )
        else:
            checks.add("history", CheckStatus.OK, value)
    else:
        checks.add("history", CheckStatus.SKIPPED, "no symbols")

    return ChecklistReport(tuple(checks.items), terminal, account, tuple(resolved), quotes)
