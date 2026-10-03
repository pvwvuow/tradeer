"""`--backtest` (spec C8): connect with the saved profile, read the history, replay it with the
profile's strategy, risk and execution settings, and print the report. Read-only: it never
sends an order. Runs entirely in the MT5 thread (a command has no window to keep alive)."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from app.backtest import metrics as metric_text
from app.backtest import monte_carlo as carlo_text
from app.backtest.history import CACHE_FOLDER, DirectGateway, load_history, resolve_broker_symbol
from app.backtest.service import BacktestRequest, default_request, run_report
from app.core.clock import BrokerClock, guess_scheme, measure_offset
from app.core.execution_settings import ExecutionSettingsSource
from app.core.strategy_settings import StrategySettingsSource
from app.mt5.api import MT5Api
from app.mt5.checklist import ConnectRequest, run_checklist
from app.risk.settings import RiskSettingsSource

Emit = Callable[[str], None]


def measured_clock(mt5: MT5Api, symbol: str, now: float) -> tuple[BrokerClock, str]:
    """The broker clock from a fresh tick, or the usual UTC+2/+3 when the price is old."""
    tick = mt5.symbol_info_tick(symbol)
    server = float(getattr(tick, "time", 0) or 0) if tick is not None else 0.0
    offset = measure_offset(server, now) if server > 0 else None
    if offset is None:
        return BrokerClock.assumed(), "no fresh price: broker time assumed UTC+2/+3"
    scheme, winter = guess_scheme(offset, now)
    clock = BrokerClock(winter, scheme, measured=True)
    return clock, f"broker time {clock.text()}"


def parse_day(text: str, fallback: date) -> date:
    return date.fromisoformat(text) if text.strip() else fallback


def run_backtest_command(
    mt5: MT5Api,
    request: ConnectRequest,
    *,
    symbol: str,
    start: str,
    end: str,
    strategies: Sequence[str],
    directory: Path,
    emit: Emit,
    elevated: bool | None = None,
    now: Callable[[], float] = time.time,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> bool:
    emit("Backtest (read-only: no orders are sent)")
    report = run_checklist(mt5, request, path_exists=path_exists, elevated=elevated)
    if not report.connected or report.account is None:
        for line in report.lines():
            emit(line)
        emit("Not connected: see the checklist above")
        emit("Result: FAIL")
        return False
    defaults = default_request()
    try:
        backtest = BacktestRequest(
            symbol=symbol,
            start=parse_day(start, defaults.start),
            end=parse_day(end, defaults.end),
            strategies=list(strategies) or defaults.strategies,
            costs=defaults.costs.model_copy(update={"account_currency": report.account.currency}),
        )
    except ValueError as error:
        emit(f"Invalid backtest request: {error}")
        emit("Result: FAIL")
        return False
    gateway = DirectGateway(mt5)
    broker = resolve_broker_symbol(gateway, backtest.symbol)
    clock, clock_note = measured_clock(mt5, broker, now())
    emit(f"{backtest.symbol} ({broker}) {backtest.period}, {', '.join(backtest.strategies)}")
    emit(clock_note)
    history, loaded = load_history(
        gateway,
        backtest.symbol,
        broker,
        backtest.utc_start,
        backtest.utc_end,
        clock,
        cache=directory / CACHE_FOLDER,
        note=emit,
    )
    counts = ", ".join(f"{tf} {count}" for tf, count in loaded.counts.items())
    emit(f"History: {counts} bars")
    shown = [-1]

    def progress(stage: str, done: int, total: int) -> None:
        tenth = int(done * 10 / total) if total else 10
        if stage == "Backtest" and tenth != shown[0]:
            shown[0] = tenth
            emit(f"{stage}: {tenth * 10}%")

    result = run_report(
        history,
        backtest,
        settings=StrategySettingsSource(directory).settings,
        risk=RiskSettingsSource(directory).config,
        execution=ExecutionSettingsSource(directory).config,
        progress=progress,
        notes=loaded.notes,
    )
    emit("")
    for line in metric_text.summary_lines(result.metrics, backtest.costs.account_currency):
        emit(line)
    if result.monte_carlo is not None:
        for line in carlo_text.summary_lines(result.monte_carlo):
            emit(line)
    for trade in result.result.trades:
        known = trade.r_multiple is not None and math.isfinite(trade.r_multiple)
        r = f"{trade.r_multiple:+.2f} R" if known else "n/a"
        emit(
            f"  {trade.strategy} {trade.direction} {trade.volume:g} lots: net "
            f"{trade.net_profit:+.2f} ({r}, {trade.exit_reason})",
        )
    emit("No calendar events are used by the command (the Backtest page uses the saved ones).")
    emit(f"Replayed {result.result.bars} bars in {result.result.seconds:.0f} s")
    emit("Result: DONE")
    return True
