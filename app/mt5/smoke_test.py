"""`--mt5-smoke-test` (spec I4): connect, then print account, prices, bars and deals.

Read-only by construction: it calls only information functions, never an order function.
Used to verify every new build on the user's PC in about 30 seconds. Exit code 0 or 1.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.mt5.api import TIMEFRAMES, MT5Api
from app.mt5.checklist import CheckStatus, ConnectRequest, run_checklist
from app.mt5.models import server_time_text
from app.mt5.symbols import resolve_symbol

BARS_SYMBOL = "EURUSD"
BARS_TIMEFRAME = "M15"
BAR_COUNT = 10
DEAL_COUNT = 10
HISTORY_START = datetime(2000, 1, 1, tzinfo=UTC)


def _bar_line(bar: Any, digits: int) -> str:
    prices = " ".join(f"{float(bar[key]):.{digits}f}" for key in ("open", "high", "low", "close"))
    ticks = int(bar["tick_volume"])
    return f"  {server_time_text(int(bar['time']))}  O H L C {prices}  ticks {ticks}"


def _deal_line(deal: Any) -> str:
    return (
        f"  #{getattr(deal, 'ticket', '?')} {server_time_text(int(getattr(deal, 'time', 0)))} "
        f"{getattr(deal, 'symbol', '') or '(balance)'} volume {getattr(deal, 'volume', 0):g} "
        f"profit {getattr(deal, 'profit', 0.0):.2f} commission "
        f"{getattr(deal, 'commission', 0.0):.2f} swap {getattr(deal, 'swap', 0.0):.2f}"
    )


def run_smoke_test(
    mt5: MT5Api,
    request: ConnectRequest,
    emit: Callable[[str], None],
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
    elevated: bool | None = None,
) -> bool:
    emit("MT5 smoke test (read-only: no orders are sent)")
    report = run_checklist(mt5, request, path_exists=path_exists, elevated=elevated)
    for line in report.lines():
        emit(line)
    if not report.connected or report.account is None:
        problem = report.first_problem()
        emit(f"Not connected: {problem.value if problem else 'see the checklist above'}")
        emit("Result: FAIL")
        return False

    emit("")
    emit(f"Account: {report.account.summary()}")
    emit("Prices:")
    for quote in report.quotes:
        emit(f"  {quote.text()} at {server_time_text(quote.server_time)}")
    if not report.quotes:
        emit("  none (the market may be closed)")

    names = [str(getattr(item, "name", "")) for item in (mt5.symbols_get() or ())]
    bars_symbol = resolve_symbol(BARS_SYMBOL, names)
    bars_ok = False
    if bars_symbol is None:
        emit(f"{BARS_SYMBOL} is not available at this broker")
    else:
        mt5.symbol_select(bars_symbol, True)
        info = mt5.symbol_info(bars_symbol)
        digits = int(getattr(info, "digits", 5)) if info is not None else 5
        timeframe = TIMEFRAMES[BARS_TIMEFRAME]
        rates = mt5.copy_rates_from_pos(bars_symbol, timeframe, 1, BAR_COUNT)
        count = len(rates) if rates is not None else 0
        emit(f"{bars_symbol} {BARS_TIMEFRAME}, last {count} closed bars:")
        for index in range(count):
            emit(_bar_line(rates[index], digits))
        bars_ok = count > 0

    end = datetime.now(UTC) + timedelta(days=1)
    deals = list(mt5.history_deals_get(HISTORY_START, end) or ())
    deals.sort(key=lambda deal: int(getattr(deal, "time", 0)))
    recent = deals[-DEAL_COUNT:]
    emit(f"Last {len(recent)} deals (of {len(deals)} in the history):")
    for deal in recent:
        emit(_deal_line(deal))

    prices_ok = any(quote.valid for quote in report.quotes)
    ok = bars_ok and prices_ok and report.status("broker_connection") is CheckStatus.OK
    emit("Result: PASS" if ok else "Result: FAIL")
    return ok
