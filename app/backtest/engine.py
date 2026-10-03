"""The backtest engine (spec C8, D3.4): a bar-by-bar replay of the live code path.

The MT5 Strategy Tester runs MQL5 experts only and cannot be driven from Python, so this
engine replaces it. It runs the real `SignalPipeline`, `RiskManager` and `ExecutionEngine`
(Paper mode, every signal that waits for approval is approved) on a temporary database, with
the `BacktestBroker` filling orders from history. Per M5 bar of the test period:

1. at the bar's open the price is the open (plus the bar's spread): the engine sends the
   signals approved at the previous close (entries at the next bar open) and manages;
2. the broker plays the bar: pending fills, SL and TP inside its range (same candle: SL);
3. at the bar's close the price is the close: one more engine cycle, then, when a strategy's
   entry bar has just closed, the symbol is analysed exactly as live (the newest closed bars
   per timeframe, as many as the live cache keeps) and the strategies run.

Open positions are closed at the last close. The result holds the trades (the `trades` rows
the engine wrote, as live), every signal with its decision trace, and the equity per bar.
"""

from __future__ import annotations

import bisect
import math
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from app.analysis.bars import ANALYSIS_TIMEFRAMES, TF_SECONDS, Bars
from app.analysis.symbol import SymbolAnalysis, Tick, analyze_symbol
from app.backtest.costs import BacktestCosts
from app.backtest.market import ReplayMarket
from app.brokers.backtest_broker import BacktestBroker, ReplayBar
from app.calendar.models import CalendarEvent
from app.core.clock import BrokerClock
from app.core.execution_settings import ExecutionConfig
from app.domain.modes import OperatingMode
from app.domain.signals import SignalRecord, SignalState
from app.engine.execution import ExecutionEngine
from app.engine.filters import FilterSettings
from app.engine.signal_pipeline import SignalPipeline
from app.mt5.market_data import BAR_COUNTS
from app.mt5.models import SymbolSpec
from app.risk.risk_manager import RiskManager
from app.risk.settings import RiskConfig
from app.storage.migrate import migrate
from app.storage.repositories import Store
from app.storage.risk_store import RiskRepository
from app.storage.signal_store import SignalRepository, epoch
from app.storage.sqlite_db import Database, database_path
from app.storage.trade_store import TradeRepository
from app.strategies.base import Strategy
from app.strategies.registry import MAGIC_NUMBERS, strategy_for_magic

ACCOUNT = "backtest"
STEP = "M5"
STEP_SECONDS = TF_SECONDS[STEP]
EVENTS_BEFORE = 2 * 86_400  # the live calendar window: 2 days back, 7 days ahead
EVENTS_AFTER = 7 * 86_400
PROGRESS_EVERY = 288  # bars (one day of M5)
END_REASON = "end of test"

Log = Callable[[str, str], None]
Progress = Callable[[int, int], None]
FloatArray = npt.NDArray[np.float64]


def _quiet(level: str, message: str) -> None:
    return None


@dataclass(frozen=True)
class History:
    """Every closed bar of one symbol per timeframe (UTC `time`, broker `server_time`)."""

    symbol: str  # the watchlist name, e.g. "EURUSD"
    broker_symbol: str  # the broker's name, e.g. "EURUSD.m"
    spec: SymbolSpec
    clock: BrokerClock
    bars: Mapping[str, Bars]

    def first_time(self) -> int:
        step = self.bars.get(STEP)
        return int(step.time[0]) if step is not None and len(step) else 0

    def last_time(self) -> int:
        step = self.bars.get(STEP)
        return int(step.time[-1]) + STEP_SECONDS if step is not None and len(step) else 0


@dataclass(frozen=True)
class BacktestSetup:
    strategies: tuple[Strategy, ...]
    start: float  # UTC seconds, inclusive (bar open times)
    end: float  # UTC seconds, exclusive
    filters: FilterSettings = field(default_factory=FilterSettings)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: BacktestCosts = field(default_factory=BacktestCosts)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    events: tuple[CalendarEvent, ...] = ()


@dataclass(frozen=True)
class BacktestTrade:
    trade_id: str
    signal_id: str
    strategy: str
    symbol: str
    direction: str  # "buy" or "sell"
    volume: float
    open_time: float  # UTC seconds
    close_time: float
    open_price: float
    close_price: float
    sl: float
    tp: float
    risk_money: float | None
    profit: float
    commission: float
    swap: float
    net_profit: float
    r_multiple: float | None
    exit_reason: str
    session: str
    mfe_r: float | None
    mae_r: float | None

    @property
    def win(self) -> bool:
        return self.net_profit > 0


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    start: float
    end: float
    start_balance: float
    trades: tuple[BacktestTrade, ...]
    signals: tuple[SignalRecord, ...]
    times: FloatArray  # UTC close time of every replayed bar
    balance: FloatArray
    equity: FloatArray
    bars: int
    analyses: int
    seconds: float
    cancelled: bool = False
    notes: tuple[str, ...] = ()

    @property
    def end_balance(self) -> float:
        return float(self.balance[-1]) if len(self.balance) else self.start_balance

    def counts(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for record in self.signals:
            name = record.signal.state.value
            found[name] = found.get(name, 0) + 1
        return found


def _float(value: Any, default: float = math.nan) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional(value: Any) -> float | None:
    number = _float(value)
    return number if math.isfinite(number) else None


def trade_from_row(row: Mapping[str, Any]) -> BacktestTrade:
    magic = int(_float(row.get("magic"), 0.0))
    return BacktestTrade(
        trade_id=str(row.get("id") or ""),
        signal_id=str(row.get("signal_id") or ""),
        strategy=strategy_for_magic(magic),
        symbol=str(row.get("symbol") or ""),
        direction=str(row.get("direction") or ""),
        volume=_float(row.get("volume"), 0.0),
        open_time=epoch(row.get("open_time")),
        close_time=epoch(row.get("close_time")),
        open_price=_float(row.get("open_price")),
        close_price=_float(row.get("close_price")),
        sl=_float(row.get("sl_initial")),
        tp=_float(row.get("tp_initial")),
        risk_money=_optional(row.get("risk_money")),
        profit=_float(row.get("profit"), 0.0),
        commission=_float(row.get("commission"), 0.0),
        swap=_float(row.get("swap"), 0.0),
        net_profit=_float(row.get("net_profit"), 0.0),
        r_multiple=_optional(row.get("r_multiple")),
        exit_reason=str(row.get("exit_reason") or ""),
        session=str(row.get("session_label") or ""),
        mfe_r=_optional(row.get("mfe_r")),
        mae_r=_optional(row.get("mae_r")),
    )


class _Windows:
    """The newest closed bars per timeframe at a moment, as the live cache holds them."""

    def __init__(self, bars: Mapping[str, Bars]) -> None:
        self._bars = {tf: bars[tf] for tf in ANALYSIS_TIMEFRAMES if tf in bars}
        self._closes = {tf: b.time + b.seconds for tf, b in self._bars.items()}

    def closed(self, timeframe: str, now: float) -> int:
        closes = self._closes.get(timeframe)
        return int(np.searchsorted(closes, now, side="right")) if closes is not None else 0

    def newest(self, timeframe: str, now: float) -> int | None:
        count = self.closed(timeframe, now)
        return int(self._bars[timeframe].time[count - 1]) if count else None

    def at(self, now: float) -> dict[str, Bars]:
        found: dict[str, Bars] = {}
        for timeframe, series in self._bars.items():
            stop = self.closed(timeframe, now)
            found[timeframe] = series.slice(max(stop - BAR_COUNTS[timeframe], 0), stop)
        return found


class Replay:
    """One backtest run on one symbol. Owns its temporary database while it runs."""

    def __init__(self, history: History, setup: BacktestSetup, store: Store, log: Log) -> None:
        self.history = history
        self.setup = setup
        self.store = store
        self._log = log
        self.now = float(setup.start)
        costs = setup.costs
        spec = history.spec
        self.point = spec.point if spec.point > 0 else 10.0**-spec.digits
        self.market = ReplayMarket({history.broker_symbol: spec}, costs, strategy_for_magic)
        settings = setup.execution.settings.model_copy(
            update={
                "paper_slippage_points": costs.slippage_points,
                "paper_commission_per_lot": costs.commission_per_lot,
                "paper_start_balance": costs.start_balance,
            },
        )
        config = setup.execution.model_copy(
            update={"mode": OperatingMode.PAPER, "settings": settings},
        )
        self.broker = BacktestBroker(
            self.market,
            lambda: config.settings,
            swap_per_lot=costs.swap_per_lot,
            triple_weekday=costs.triple_swap_weekday,
        )
        self.market.broker = self.broker
        risk_settings = setup.risk.settings.model_copy(
            update={"commission_per_lot": costs.commission_per_lot},
        )
        risk_config = setup.risk.model_copy(update={"settings": risk_settings})
        clock = history.clock
        self.risk = RiskManager(
            lambda: risk_config,
            self.market,
            store=RiskRepository(store),
            account=lambda: ACCOUNT,
            log=log,
            utc_now=lambda: self.now,
        )
        self.risk.clock_source = lambda: clock
        self.engine = ExecutionEngine(
            lambda: config,
            market=self.market,
            paper=self.broker,
            live=None,
            magics=MAGIC_NUMBERS,
            risk=self.risk,
            store=TradeRepository(store),
            account=lambda: ACCOUNT,
            clock=lambda: clock,
            connected=lambda: True,
            netting=lambda: False,
            broker_symbol=lambda name: history.broker_symbol,
            stop_trading=self.risk.request_stop,
            log=log,
            utc_now=lambda: self.now,
        )
        strategies = list(setup.strategies)
        filters = setup.filters
        self.pipeline = SignalPipeline(
            lambda: strategies,
            lambda: filters,
            store=SignalRepository(store),
            account=lambda: ACCOUNT,
            risk=self.risk,
            executor=self.engine,
            log=log,
            utc_now=lambda: self.now,
        )
        self.windows = _Windows(history.bars)
        self.entry_timeframes = sorted({s.entry_timeframe for s in strategies})
        self._done: dict[str, int | None] = dict.fromkeys(self.entry_timeframes)
        self._events = sorted(setup.events, key=lambda event: event.time)
        self._event_times = [event.time for event in self._events]
        self.analyses = 0
        self.closed_at_end: set[str] = set()

    # One bar ------------------------------------------------------------------------------
    def bar(self, step: Bars, index: int) -> None:
        name = self.history.broker_symbol
        spread = self.setup.costs.spread_points(float(step.spread[index])) * self.point
        server = int(step.server_time[index])
        opened = float(step.time[index])
        self.now = opened
        self.market.set_quote(name, float(step.open[index]), spread, server)
        self.pipeline.on_cycle(opened)
        self.broker.step_bar(
            ReplayBar(
                symbol=name,
                server_open=server,
                server_close=server + STEP_SECONDS,
                open=float(step.open[index]),
                high=float(step.high[index]),
                low=float(step.low[index]),
                close=float(step.close[index]),
                spread=spread,
            ),
        )
        closed = opened + STEP_SECONDS
        self.now = closed
        price = float(step.close[index])
        self.market.set_quote(name, price, spread, server + STEP_SECONDS)
        self.pipeline.on_cycle(closed)
        if self._due(closed):
            self._evaluate(closed, price, spread)

    def _due(self, now: float) -> bool:
        return any(
            self.windows.newest(tf, now) not in (None, self._done[tf])
            for tf in self.entry_timeframes
        )

    def _evaluate(self, now: float, price: float, spread: float) -> None:
        analysis = self.analyse(now, price, spread)
        self.analyses += 1
        if not analysis.quality.ok:
            return  # as live: the strategies wait for good data (the next M5 close)
        for timeframe in self.entry_timeframes:
            self._done[timeframe] = self.windows.newest(timeframe, now)
        created = self.pipeline.on_analysis(
            analysis,
            clock=self.history.clock,
            spec=self.history.spec,
            spread=spread,
            now=now,
        )
        for record in created:
            if record.signal.state is SignalState.PENDING_APPROVAL:
                self.pipeline.approve(record.id)

    def analyse(self, now: float, price: float, spread: float) -> SymbolAnalysis:
        """`MarketWatch._analyze` on the bars the live cache would hold at `now`."""
        spec = self.history.spec
        tick = Tick(price, price + spread, now, self.point, spec.digits)
        return analyze_symbol(
            self.history.symbol,
            self.windows.at(now),
            now=now,
            clock=self.history.clock,
            tick=tick,
            digits=spec.digits,
            broker_symbol=self.history.broker_symbol,
            events=self._events_near(now),
        )

    def _events_near(self, now: float) -> list[CalendarEvent]:
        low = bisect.bisect_left(self._event_times, int(now) - EVENTS_BEFORE)
        high = bisect.bisect_right(self._event_times, int(now + EVENTS_AFTER))
        return self._events[low:high]

    def finish(self) -> None:
        """Close what is still open at the last price, cancel pending orders, sync."""
        magics = MAGIC_NUMBERS.values()
        for order in self.broker.orders(magics) or []:
            self.broker.cancel(order)
        for position in self.broker.positions(magics) or []:
            result = self.broker.close(position)
            if result.ok:
                self.closed_at_end.add(str(position.ticket))
        self.pipeline.on_cycle(self.now)

    # Result -------------------------------------------------------------------------------
    def trades(self) -> tuple[BacktestTrade, ...]:
        rows = self.store.db.query(
            "SELECT * FROM trades WHERE mode = 'paper' AND close_time IS NOT NULL "
            "ORDER BY open_time, ticket",
        )
        found: list[BacktestTrade] = []
        for row in rows:
            trade = trade_from_row(row)
            if str(row.get("ticket")) in self.closed_at_end:
                trade = _with_reason(trade, END_REASON)
            found.append(trade)
        return tuple(found)

    def signals(self) -> tuple[SignalRecord, ...]:
        rows = SignalRepository(self.store).recent(1_000_000, ACCOUNT)
        return tuple(sorted(rows, key=lambda record: (record.signal.created_at, record.id)))


def _with_reason(trade: BacktestTrade, reason: str) -> BacktestTrade:
    return replace(trade, exit_reason=reason)


def run_backtest(
    history: History,
    setup: BacktestSetup,
    *,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] | None = None,
    log: Log = _quiet,
) -> BacktestResult:
    """Replay `setup` on `history`. Never touches MT5 or the app's own database."""
    started = time.perf_counter()
    step = history.bars.get(STEP)
    if step is None or not len(step):
        raise ValueError(f"{history.symbol}: no {STEP} bars to replay")
    if not setup.strategies:
        raise ValueError("choose at least one strategy")
    first = int(np.searchsorted(step.time, setup.start, side="left"))
    stop = int(np.searchsorted(step.time, setup.end, side="left"))
    if stop <= first:
        raise ValueError("the test period has no bars")
    total = stop - first
    times = np.zeros(total, dtype=np.float64)
    balance = np.zeros(total, dtype=np.float64)
    equity = np.zeros(total, dtype=np.float64)
    stopped = False
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="tradeer-backtest-") as folder:
        db = Database(database_path(Path(folder)))
        try:
            migrate(db)
            db.connection().execute("PRAGMA synchronous=OFF")  # a throw-away database
            replay = Replay(history, setup, Store(db), log)
            done = 0
            for done, index in enumerate(range(first, stop), start=1):
                replay.bar(step, index)
                times[done - 1] = replay.now
                balance[done - 1] = replay.broker.balance
                equity[done - 1] = replay.broker.equity
                if done % PROGRESS_EVERY == 0:
                    if progress is not None:
                        progress(done, total)
                    if cancelled is not None and cancelled():
                        stopped = True
                        notes.append(f"cancelled after {done} of {total} bars")
                        break
            replay.finish()
            if done:
                balance[done - 1] = replay.broker.balance
                equity[done - 1] = replay.broker.balance
            if progress is not None:
                progress(total, total)
            trades = replay.trades()
            signals = replay.signals()
            analyses = replay.analyses
        finally:
            db.close()
    count = done
    if replay.closed_at_end:
        notes.append(f"{len(replay.closed_at_end)} position(s) closed at the end of the test")
    return BacktestResult(
        symbol=history.symbol,
        start=float(setup.start),
        end=float(times[count - 1]) if count else float(setup.start),
        start_balance=setup.costs.start_balance,
        trades=trades,
        signals=signals,
        times=times[:count],
        balance=balance[:count],
        equity=equity[:count],
        bars=count,
        analyses=analyses,
        seconds=time.perf_counter() - started,
        cancelled=stopped,
        notes=tuple(notes),
    )


def full_history(
    symbol: str,
    bars: Mapping[str, Bars],
    spec: SymbolSpec,
    *,
    broker_symbol: str = "",
    clock: BrokerClock | None = None,
) -> History:
    return History(
        symbol=symbol,
        broker_symbol=broker_symbol or spec.name or symbol,
        spec=spec,
        clock=clock or BrokerClock.assumed(),
        bars=dict(bars),
    )
