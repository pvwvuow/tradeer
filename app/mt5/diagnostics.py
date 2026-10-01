"""Connection Diagnostics (spec I4): every checklist step plus ping, history depth per
symbol and timeframe, symbol specifications, the broker time offset and permission flags.

`DiagnosticsReport.text()` is the "Copy report" text. It never contains a password and is
masked once more before it is returned.
"""

from __future__ import annotations

import os
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.__version__ import __version__
from app.mt5.api import TIMEFRAMES, MT5Api
from app.mt5.checklist import ChecklistReport, ConnectRequest, run_checklist
from app.mt5.models import SymbolSpec
from app.observability.masking import MASKER

DIAGNOSTIC_TIMEFRAMES: tuple[str, ...] = ("M15", "H1", "H4", "D1")
HISTORY_PROBE_LIMIT = 100_000
OFFSET_TOLERANCE_SECONDS = 180.0


@dataclass(frozen=True)
class HistoryDepth:
    symbol: str
    timeframe: str
    bars: int
    limit: int

    def text(self) -> str:
        shown = f"{self.bars:,}+" if self.bars >= self.limit else f"{self.bars:,}"
        return f"{self.symbol} {self.timeframe}: {shown} bars"


@dataclass(frozen=True)
class DiagnosticsReport:
    checklist: ChecklistReport
    history: tuple[HistoryDepth, ...]
    specs: tuple[SymbolSpec, ...]
    broker_offset_hours: float | None
    generated_at: datetime
    elevated: bool | None

    def text(self) -> str:
        lines = [
            f"MT5 Trading Workstation {__version__}: connection diagnostics",
            f"Created {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')} UTC on "
            f"{platform.platform()}",
            f"App running as administrator: {_yes_no(self.elevated)}",
            "",
            "Checklist",
            *self.checklist.lines(),
        ]
        terminal = self.checklist.terminal
        if terminal is not None:
            lines += [
                "",
                "Terminal",
                f"  {terminal.name} ({terminal.company}), build {terminal.build}",
                f"  Program folder: {terminal.path}",
                f"  Data folder: {terminal.data_path}",
                f"  Ping: {terminal.ping_ms:.0f} ms, Max bars in chart: {terminal.maxbars:,}",
                f"  Algo Trading: {_on_off(terminal.trade_allowed)}, Python API trading "
                f"disabled: {_yes_no(terminal.tradeapi_disabled)}",
            ]
        account = self.checklist.account
        if account is not None:
            lines += [
                "",
                "Account",
                f"  {account.summary()}",
                f"  {account.stop_out_text()}",
                f"  Trading allowed: {_yes_no(account.trade_allowed)}, Expert Advisors "
                f"allowed: {_yes_no(account.trade_expert)}",
            ]
        lines += ["", f"Broker time: {offset_text(self.broker_offset_hours)}"]
        if self.history:
            lines += ["", "History", *(f"  {depth.text()}" for depth in self.history)]
        if self.specs:
            lines += ["", "Symbols"]
            lines += [f"  {spec_line(spec)}" for spec in self.specs]
        return MASKER.mask("\n".join(lines))


def _yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def offset_text(hours: float | None) -> str:
    if hours is None:
        return "offset unknown (no fresh price; the market may be closed)"
    sign = "+" if hours >= 0 else "-"
    whole = int(abs(hours))
    minutes = round((abs(hours) - whole) * 60)
    suffix = f":{minutes:02d}" if minutes else ""
    return f"server time is UTC{sign}{whole}{suffix}"


def spec_line(spec: SymbolSpec) -> str:
    return (
        f"{spec.name}: digits {spec.digits}, point {spec.point:g}, tick {spec.tick_size:g} = "
        f"{spec.tick_value:g} {spec.currency_profit}, contract {spec.contract_size:g}, volume "
        f"{spec.volume_min:g}-{spec.volume_max:g} step {spec.volume_step:g}, stops level "
        f"{spec.stops_level}, freeze level {spec.freeze_level}, filling {spec.filling_mode}, "
        f"trading {spec.trade_mode.value}"
    )


def estimate_broker_offset(
    server_time: int,
    utc_now: float,
    tolerance_seconds: float = OFFSET_TOLERANCE_SECONDS,
) -> float | None:
    """The broker's UTC offset in hours (half-hour steps), from a fresh tick's server time.

    MT5 stamps ticks with the broker's clock written as if it were UTC, so the difference to
    real UTC is the offset. A stale tick (market closed) gives no reliable answer.
    """
    difference = server_time - utc_now
    offset = round(difference / 1800.0) / 2.0
    if abs(offset) > 14 or abs(difference - offset * 3600.0) > tolerance_seconds:
        return None
    return offset


def run_diagnostics(
    mt5: MT5Api,
    request: ConnectRequest,
    *,
    utc_now: Callable[[], float] = time.time,
    path_exists: Callable[[str], bool] = os.path.exists,
    elevated: bool | None = None,
    history_limit: int = HISTORY_PROBE_LIMIT,
) -> DiagnosticsReport:
    checklist = run_checklist(mt5, request, path_exists=path_exists, elevated=elevated)
    history: list[HistoryDepth] = []
    specs: list[SymbolSpec] = []
    offset: float | None = None
    if checklist.connected:
        for name in checklist.symbols:
            info = mt5.symbol_info(name)
            if info is not None:
                specs.append(SymbolSpec.from_mt5(info))
            for timeframe in DIAGNOSTIC_TIMEFRAMES:
                rates = mt5.copy_rates_from_pos(name, TIMEFRAMES[timeframe], 0, history_limit)
                bars = len(rates) if rates is not None else 0
                history.append(HistoryDepth(name, timeframe, bars, history_limit))
        fresh = [quote for quote in checklist.quotes if quote.valid]
        if fresh:
            newest = max(quote.server_time for quote in fresh)
            offset = estimate_broker_offset(newest, utc_now())
    return DiagnosticsReport(
        checklist,
        tuple(history),
        tuple(specs),
        offset,
        datetime.fromtimestamp(utc_now(), UTC),
        elevated,
    )
