"""Closed trades for the analytics (spec C11): one record per trade, with its strategy,
config and predicted probability from the signal, and the filters every page shares.

Rows come from the `trades` table (bot trades of the execution engine and every trade of
the MT5 history import) joined with their signal. A bot trade without a signal row gets its
strategy from the magic number; a manual trade is "manual". The demo test's trades
(`TEST_MAGIC`) are left out: they test the order code and are no one's trading.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.domain.history import TEST_MAGIC
from app.storage.signal_store import epoch

TRADE_QUERY = (
    "SELECT t.*, s.strategy AS signal_strategy, s.config_id AS signal_config, "
    "s.strategy_version AS signal_version, s.spread AS signal_spread, "
    "s.entry AS signal_entry, s.sl AS signal_sl, s.reason AS signal_reason, "
    "s.win_probability AS signal_probability "
    "FROM trades t LEFT JOIN signals s ON s.id = t.signal_id "
    "WHERE t.close_time IS NOT NULL AND t.net_profit IS NOT NULL"
)


class Database(Protocol):
    def query(self, sql: str, parameters: Sequence[Any] = ...) -> list[Any]: ...


@dataclass(frozen=True)
class TradeRecord:
    id: str
    account: str
    mode: str  # "live" or "paper"
    source: str  # "bot", "manual" or "external"
    symbol: str
    direction: str  # "buy" or "sell"
    volume: float
    open_time: float  # UTC seconds
    close_time: float
    open_price: float
    close_price: float
    profit: float
    commission: float
    swap: float
    fee: float
    net_profit: float
    r_multiple: float | None
    risk_money: float | None
    mfe_r: float | None
    mae_r: float | None
    probability: float | None
    session: str
    strategy: str
    config: str
    signal_id: str
    exit_reason: str
    slippage: float | None = None
    spread_r: float | None = None  # the signal's spread in R (spread / SL distance)
    reason: str = ""
    sl: float | None = None
    tp: float | None = None
    magic: int = 0

    @property
    def win(self) -> bool:
        return self.net_profit > 0

    @property
    def loss(self) -> bool:
        return self.net_profit < 0

    @property
    def duration(self) -> float:
        return max(0.0, self.close_time - self.open_time)

    @property
    def gross(self) -> float:
        """The result before costs (profit without commission, swap and fees)."""
        return self.profit

    @property
    def demo_test(self) -> bool:
        """A trade of the Health > Demo test, which counts nowhere."""
        return self.magic == TEST_MAGIC


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def record_from_row(
    row: Mapping[str, Any],
    strategy_for_magic: Callable[[int], str | None] | None = None,
) -> TradeRecord:
    magic = int(row.get("magic") or 0)
    source = _text(row.get("source")) or ("bot" if row.get("signal_id") else "manual")
    strategy = _text(row.get("signal_strategy"))
    if not strategy and strategy_for_magic is not None and magic:
        strategy = strategy_for_magic(magic) or ""
    if not strategy:
        strategy = "manual" if source == "manual" else "unknown"
    config = _text(row.get("signal_config")) or _text(row.get("signal_version"))
    spread = _float(row.get("signal_spread"))
    entry = _float(row.get("signal_entry"))
    stop = _float(row.get("signal_sl"))
    spread_r = None
    if spread is not None and entry is not None and stop is not None and entry != stop:
        spread_r = spread / abs(entry - stop)
    probability = _float(row.get("predicted_probability"))
    if probability is None:
        probability = _float(row.get("signal_probability"))
    return TradeRecord(
        id=_text(row.get("id")),
        account=_text(row.get("account_id")),
        mode=_text(row.get("mode")) or "live",
        source=source,
        symbol=_text(row.get("symbol")),
        direction=_text(row.get("direction")).lower(),
        volume=_float(row.get("volume")) or 0.0,
        open_time=epoch(row.get("open_time")),
        close_time=epoch(row.get("close_time")),
        open_price=_float(row.get("open_price")) or 0.0,
        close_price=_float(row.get("close_price")) or 0.0,
        profit=_float(row.get("profit")) or 0.0,
        commission=_float(row.get("commission")) or 0.0,
        swap=_float(row.get("swap")) or 0.0,
        fee=_float(row.get("fee")) or 0.0,
        net_profit=_float(row.get("net_profit")) or 0.0,
        r_multiple=_float(row.get("r_multiple")),
        risk_money=_float(row.get("risk_money")),
        mfe_r=_float(row.get("mfe_r")),
        mae_r=_float(row.get("mae_r")),
        probability=probability,
        session=_text(row.get("session_label")),
        strategy=strategy,
        config=config,
        signal_id=_text(row.get("signal_id")),
        exit_reason=_text(row.get("exit_reason")),
        slippage=_float(row.get("slippage")),
        spread_r=spread_r,
        reason=_text(row.get("signal_reason")),
        sl=_float(row.get("sl_initial")),
        tp=_float(row.get("tp_initial")),
        magic=magic,
    )


def load_trades(
    db: Database,
    strategy_for_magic: Callable[[int], str | None] | None = None,
    *,
    tests: bool = False,
) -> list[TradeRecord]:
    """Every closed trade, oldest close first; the demo test's trades only with `tests`."""
    rows = db.query(TRADE_QUERY + " ORDER BY t.close_time, t.open_time")
    records = [record_from_row(dict(row), strategy_for_magic) for row in rows]
    return records if tests else [record for record in records if not record.demo_test]


@dataclass(frozen=True)
class TradeFilter:
    """The filters of every analytics view (spec C11): empty means "all"."""

    start: float | None = None  # UTC seconds, close time from
    end: float | None = None  # close time before
    account: str = ""
    symbol: str = ""
    strategy: str = ""
    mode: str = ""  # "live" or "paper"
    source: str = ""  # "bot" or "manual"

    def matches(self, trade: TradeRecord) -> bool:
        if self.start is not None and trade.close_time < self.start:
            return False
        if self.end is not None and trade.close_time >= self.end:
            return False
        checks = (
            (self.account, trade.account),
            (self.symbol, trade.symbol),
            (self.strategy, trade.strategy),
            (self.mode, trade.mode),
        )
        if any(wanted and wanted != value for wanted, value in checks):
            return False
        if self.source == "manual":
            return trade.source != "bot"
        return not (self.source and self.source != trade.source)

    def apply(self, trades: Iterable[TradeRecord]) -> list[TradeRecord]:
        return [trade for trade in trades if self.matches(trade)]


def choices(trades: Sequence[TradeRecord]) -> dict[str, list[str]]:
    """The values each filter can take, for the dropdowns."""
    return {
        "account": sorted({t.account for t in trades if t.account}),
        "symbol": sorted({t.symbol for t in trades if t.symbol}),
        "strategy": sorted({t.strategy for t in trades if t.strategy}),
        "mode": sorted({t.mode for t in trades if t.mode}),
    }


def utc_day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")
