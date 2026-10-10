"""The signal pipeline (spec C5): on each closed bar, context -> strategy -> features ->
probability + EV -> filters -> risk -> decision, every step in the decision trace.

Runs in the `market-analysis` thread, called by `MarketWatch` after a symbol was analysed.
A signal that passes every filter is sized and checked by the risk manager (Phase 7); one
that breaks a limit is `RISK_REJECTED`. A signal within every limit waits for approval
(`PENDING_APPROVAL`) and expires. Approving hands it to the execution engine (Phase 8), which
re-checks, sends and manages it; the engine's state changes (`SENT`, `FILLED`, `MANAGED`,
`CLOSED`, `FAILED`) come back as `SignalUpdate`s. The UI asks to approve or dismiss a signal
through a queue, so every database write happens in this one thread.

The Signal desk (docs/SIGNAL_DESK.md) hands in pasted signals the same way (`submit`): each
is planned against the newest analysis of its symbol, split into one leg per target and
every leg runs the same features, probability, filters and risk as a strategy's signal.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from app.analysis.bars import TF_SECONDS
from app.analysis.indicators import atr, last
from app.analysis.scanner import ScanEntry, rank
from app.analysis.sessions import active_sessions, market_open
from app.analysis.symbol import SymbolAnalysis
from app.core.clock import BrokerClock
from app.domain.probability import ProbabilityEstimate, baseline, cost_in_r, expected_value_r
from app.domain.signals import TRANSITIONS, OrderType, Signal, SignalRecord, SignalState
from app.engine.currency_guard import shared_bet
from app.engine.execution import SignalUpdate
from app.engine.filters import FilterInput, FilterSettings, run_filters
from app.engine.signal_desk import (
    KEEP_RESULTS,
    DeskRequest,
    DeskResult,
    desk_reason,
    leg_shares,
    summary,
)
from app.ml import features as model_features
from app.ml.predictor import Prediction
from app.mt5.models import SymbolSpec, SymbolTradeMode
from app.observability.decision_trace import DecisionTrace
from app.risk.risk_manager import RiskDecision
from app.signals.plan import Market, OrderPlan, plan
from app.signals.words import resolve_symbol
from app.storage.ids import stable_id
from app.storage.signal_store import TradeResult, config_id
from app.strategies.base import EXAMPLE_NOTE, Evaluation, Strategy
from app.strategies.context import MarketContext, build_context
from app.strategies.manual_signal import GROUP_FEATURE, ManualSignal

KEEP_SIGNALS = 200
FRESH_GRACE_SECONDS = 120
RISK_NOTE = "No risk manager is running, so nothing was sized"
FILTERED_RISK_NOTE = "not sized: the signal was filtered out"
EXECUTION_NOTE = "approve to send it (Paper or Semi-auto mode); unapproved signals expire"
NO_EXECUTION_NOTE = "No execution engine is running: signal only"
OCO_REASON = "the other side of the breakout filled (OCO)"
# A trade on its way or open: one per symbol and side across strategies (8 October 2026)
LIVE_STATES = frozenset(
    {
        SignalState.PENDING_APPROVAL,
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FILLED,
        SignalState.MANAGED,
    },
)

Log = Callable[[str, str], None]
Listener = Callable[["SignalsSnapshot"], None]
Quote = Callable[[str], tuple[float, float] | None]  # the live bid and ask of a symbol


class SignalStore(Protocol):
    def save(
        self,
        record: SignalRecord,
        params: Mapping[str, Any],
        account: str | None = None,
    ) -> bool: ...

    def exists(self, signal_id: str) -> bool: ...

    def recent(self, limit: int = ..., account: str | None = None) -> list[SignalRecord]: ...

    def results(self, strategy: str, limit: int = ...) -> list[TradeResult]: ...


class RiskHook(Protocol):
    """The risk manager (Phase 7): sizes a signal and checks every limit."""

    def evaluate(
        self,
        signal: Signal,
        spec: SymbolSpec | None,
        clock: BrokerClock,
        now: float,
    ) -> RiskDecision: ...

    def refresh(self, now: float | None = None, *, force: bool = False) -> None: ...


class ProbabilityModel(Protocol):
    """The active win-probability model (Phase 11, `app.ml.predictor.Predictor`)."""

    @property
    def source(self) -> str: ...

    def covers(self, strategy: str) -> bool: ...

    def predict(self, values: Mapping[str, float]) -> Prediction: ...


class ExecutionHook(Protocol):
    """The execution engine (Phase 8)."""

    def approval_block(self) -> str: ...

    def execute(self, record: SignalRecord, now: float | None = None) -> list[SignalUpdate]: ...

    def cycle(
        self,
        now: float | None = None,
        signals: Mapping[str, SignalRecord] | None = None,
    ) -> list[SignalUpdate]: ...


def add_risk_steps(trace: DecisionTrace, decision: RiskDecision, now: float) -> None:
    """The full sizing calculation and every limit check, one trace line each."""
    sizing = decision.sizing
    cur = decision.currency
    if sizing is None:
        trace.add("risk", "position size and limits", False, detail=decision.reason, at=now)
        return
    trace.add("risk", "equity", None, value=round(decision.equity, 2), detail=cur, at=now)
    loss = decision.loss_per_lot
    trace.add(
        "risk",
        "loss per lot at SL",
        loss is not None and math.isfinite(loss) and loss > 0,
        value=round(loss, 2) if loss is not None and math.isfinite(loss) else None,
        detail=f"{cur}, from MT5 order_calc_profit (never the tick value)",
    )
    trace.add(
        "risk",
        "commission per lot",
        None,
        value=round(decision.commission_per_lot, 2),
        detail=f"{cur}, {decision.commission_source}",
    )
    trace.add(
        "risk",
        "lot size",
        sizing.ok,
        value=sizing.volume if sizing.ok else None,
        detail=sizing.text(),
    )
    trace.add(
        "risk",
        "risk money",
        None,
        value=round(sizing.risk_money, 2),
        threshold=round(sizing.allowed_risk, 2),
        detail=f"{cur}: what this lot loses at the stop loss, commission included",
    )
    for check in decision.checks:
        trace.add(
            "risk",
            check.name,
            check.passed,
            value=check.value,
            threshold=check.threshold,
            detail=check.detail,
        )


@dataclass(frozen=True)
class SignalsSnapshot:
    signals: tuple[SignalRecord, ...] = ()  # newest first
    scanner: tuple[ScanEntry, ...] = ()
    strategies: tuple[str, ...] = ()
    message: str = "Waiting for the first closed bar"
    updated_at: float = 0.0
    approval_block: str = NO_EXECUTION_NOTE  # why Approve is disabled, "" when it works
    desk: tuple[DeskResult, ...] = ()  # the Signal desk's newest results first
    symbols: tuple[str, ...] = ()  # the analysed symbols (the Signal desk's choice)

    def pending(self) -> list[SignalRecord]:
        return [r for r in self.signals if r.signal.state is SignalState.PENDING_APPROVAL]


@dataclass(frozen=True)
class _Latest:
    """The newest analysis of a symbol, for a signal that arrives between two bars."""

    analysis: SymbolAnalysis
    clock: BrokerClock
    spec: SymbolSpec | None
    spread: float


@dataclass
class _History:
    wins: int = 0
    total: int = 0
    losses_in_row: int = 0
    last_loss: dict[str, float] = field(default_factory=dict)  # symbol -> close time
    streak_end: float | None = None  # close time of the newest loss of the losing streak


def _quiet(level: str, message: str) -> None:
    return None


def _utc_text(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")


def history_of(results: Sequence[TradeResult]) -> _History:
    """Win rate, the current losing streak and each symbol's last loss (newest first)."""
    found = _History()
    streak = True
    for result in results:
        if result.outcome in ("win", "loss"):
            found.total += 1
            found.wins += result.outcome == "win"
        if result.outcome == "loss":
            found.last_loss.setdefault(result.symbol, result.close_time)
            if streak:
                found.losses_in_row += 1
                if found.streak_end is None:
                    found.streak_end = result.close_time
        elif result.outcome == "win":
            streak = False
    return found


def _same_group(signal: Signal, other: Signal) -> bool:
    """Two legs of one Signal desk signal (one position per target): not duplicates."""
    group = signal.features.get(GROUP_FEATURE)
    return bool(group) and other.features.get(GROUP_FEATURE) == group


def entry_atr(ctx: MarketContext, period: int = 14) -> float:
    bars = ctx.entry
    if len(bars) <= period:
        return math.nan
    return last(atr(bars.high, bars.low, bars.close, period))


class SignalPipeline:
    def __init__(
        self,
        strategies: Callable[[], Sequence[Strategy]],
        filters: Callable[[], FilterSettings],
        *,
        store: SignalStore | None = None,
        account: Callable[[], str | None] | None = None,
        risk: RiskHook | None = None,
        executor: ExecutionHook | None = None,
        model: Callable[[], ProbabilityModel | None] | None = None,
        quote: Quote | None = None,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
    ) -> None:
        self._model = model or (lambda: None)
        self._quote = quote
        self._strategies = strategies
        self._filters = filters
        self._store = store
        self._risk = risk
        self._executor = executor
        self._account = account or (lambda: None)
        self._log = log
        self._now = utc_now
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._records: dict[str, SignalRecord] = {}
        self._scan: dict[tuple[str, str], ScanEntry] = {}
        self._evaluated: dict[tuple[str, str, str], int] = {}  # strategy+hash+symbol -> bar
        self._dismiss: list[str] = []
        self._approve: list[str] = []
        self._desk_queue: list[DeskRequest] = []
        self._desk: dict[str, DeskResult] = {}
        self._latest: dict[str, _Latest] = {}
        self._manual = ManualSignal()
        self._store_failed = False
        self._snapshot = SignalsSnapshot()

    # Public API (any thread) -----------------------------------------------------------
    @property
    def snapshot(self) -> SignalsSnapshot:
        with self._lock:
            return self._snapshot

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def dismiss(self, signal_id: str) -> None:
        """Ask to reject a pending signal; done on the next analysis cycle."""
        with self._lock:
            self._dismiss.append(signal_id)

    def approve(self, signal_id: str) -> None:
        """Ask to send a pending signal; re-checked and sent on the next analysis cycle."""
        with self._lock:
            self._approve.append(signal_id)

    def submit(self, request: DeskRequest) -> None:
        """A Signal desk signal; planned, sized and checked on the next analysis cycle. Its
        legs then wait for the user's confirmation like any pending signal."""
        with self._lock:
            self._desk_queue.append(request)

    def use_quotes(self, quote: Quote | None) -> None:
        """The live bid and ask the Signal desk plans on (the main window hands in the
        market watch's quotes); without them the newest analysis price and spread."""
        self._quote = quote

    # Analysis thread -------------------------------------------------------------------
    def load(self) -> int:
        """The newest saved signals, so the page and the expiry survive a restart."""
        if self._store is None:
            return 0
        try:
            records = self._store.recent(KEEP_SIGNALS)
        except Exception as error:
            self._log("WARNING", f"Saved signals could not be read: {type(error).__name__}")
            return 0
        for record in records:
            self._records.setdefault(record.id, record)
        self._publish("Saved signals loaded")
        return len(records)

    def on_cycle(self, now: float | None = None) -> None:
        """Dismissed and expired signals. Called on every analysis cycle."""
        moment = self._now() if now is None else now
        with self._lock:
            queued, self._dismiss = self._dismiss, []
            approved, self._approve = self._approve, []
            desks, self._desk_queue = self._desk_queue, []
        changed = False
        for request in desks:
            self._keep(self._desk_safe(request, moment))
            changed = True
        for signal_id in queued:
            changed |= self._finish(signal_id, SignalState.USER_REJECTED, moment, "dismissed")
        for signal_id in approved:
            changed |= self._send(signal_id, moment)
        for record in list(self._records.values()):
            signal = record.signal
            if signal.state is SignalState.PENDING_APPROVAL and signal.expires_at <= moment:
                reason = f"expired at {_utc_text(signal.expires_at)}"
                changed |= self._finish(signal.id, SignalState.EXPIRED, moment, reason)
        if self._executor is not None:
            try:
                updates = self._executor.cycle(moment, dict(self._records))
            except Exception as error:
                updates = []
                self._log("ERROR", f"Execution cycle failed: {type(error).__name__}: {error}")
            for update in updates:
                changed |= self._apply(update, moment)
        if changed or self._approval_block() != self._snapshot.approval_block:
            self._publish("Signals updated" if changed else self._snapshot.message)
        if self._risk is not None:
            try:
                self._risk.refresh(moment)
            except Exception as error:
                self._log("ERROR", f"Risk refresh failed: {type(error).__name__}: {error}")

    def on_analysis(
        self,
        analysis: SymbolAnalysis,
        *,
        clock: BrokerClock,
        spec: SymbolSpec | None,
        spread: float,
        now: float | None = None,
    ) -> list[SignalRecord]:
        """Run every enabled strategy on the symbol's newest closed bar."""
        moment = self._now() if now is None else now
        self._latest[analysis.symbol] = _Latest(analysis, clock, spec, spread)
        strategies = list(self._strategies())
        names = {strategy.name for strategy in strategies}
        self._scan = {key: entry for key, entry in self._scan.items() if key[1] in names}
        created: list[SignalRecord] = []
        for strategy in strategies:
            try:
                created.extend(self._run(strategy, analysis, clock, spec, spread, moment))
            except Exception as error:
                name = type(error).__name__
                self._log("ERROR", f"{strategy.name} failed on {analysis.symbol}: {name}: {error}")
        self._publish(f"Last closed bar checked at {_utc_text(moment)}")
        return created

    # Steps -----------------------------------------------------------------------------
    def _run(
        self,
        strategy: Strategy,
        analysis: SymbolAnalysis,
        clock: BrokerClock,
        spec: SymbolSpec | None,
        spread: float,
        now: float,
    ) -> list[SignalRecord]:
        timeframe = strategy.entry_timeframe
        entry = analysis.bars.get(timeframe)
        if entry is None or not len(entry):
            return []
        key = (strategy.name, strategy.params_hash, analysis.symbol)
        bar_time = int(entry.time[-1])
        if self._evaluated.get(key) == bar_time:
            return []
        self._evaluated[key] = bar_time
        point = spec.point if spec is not None and spec.point > 0 else 10.0**-analysis.digits
        ctx = build_context(analysis, timeframe, clock=clock, now=now, point=point, spread=spread)
        evaluation = strategy.evaluate(ctx)
        records = [
            self._process(strategy, signal, evaluation, ctx, analysis, spec, now)
            for signal in evaluation.signals
            if not self._known(signal.id)
        ]
        self._scan_entry(strategy, evaluation, records)
        return records

    def _scan_entry(
        self,
        strategy: Strategy,
        evaluation: Evaluation,
        records: Sequence[SignalRecord],
    ) -> None:
        best = records[0] if records else None
        probability = best.probability.value if best is not None else None
        self._scan[(evaluation.symbol, strategy.name)] = ScanEntry(
            symbol=evaluation.symbol,
            strategy=strategy.name,
            state=evaluation.state.value,
            passed=evaluation.passed,
            total=len(evaluation.conditions),
            bar_time=evaluation.bar_time,
            note=evaluation.note,
            direction=best.signal.direction.value if best is not None else "",
            probability=probability,
            expected_value=best.expected_value if best is not None else None,
        )

    def _process(
        self,
        strategy: Strategy,
        signal: Signal,
        evaluation: Evaluation,
        ctx: MarketContext,
        analysis: SymbolAnalysis,
        spec: SymbolSpec | None,
        now: float,
    ) -> SignalRecord:
        settings = self._filters()
        trace = DecisionTrace(signal.id, stable_id("trace", signal.id))
        counts = ", ".join(f"{tf} {len(bars)}" for tf, bars in ctx.bars.items())
        trace.add(
            "context",
            "closed bar",
            True,
            value=_utc_text(ctx.bar_time),
            detail=f"{ctx.symbol} {ctx.timeframe}; closed bars: {counts}",
            at=now,
        )
        trace.add("context", "data quality", analysis.quality.ok, detail=analysis.quality.text())
        trace.add("context", "session", None, value=ctx.session or "none")
        note = EXAMPLE_NOTE if strategy.example else strategy.description
        trace.add("strategy", f"{strategy.title} {strategy.version}", True, detail=note)
        trace.extend(evaluation.steps(now))
        trace.add("strategy", "signal", True, value=signal.summary(), detail=signal.reason)

        # Features ----------------------------------------------------------------------
        range_atr = entry_atr(ctx)
        features: dict[str, float | str] = dict(signal.features)
        features.update(self._features(signal, ctx, range_atr))
        values = model_features.compute(signal, ctx)
        features.update(model_features.stored(values))
        signal = replace(
            signal,
            features=features,
            config_id=config_id(strategy.name, strategy.version, strategy.params_hash),
        )
        for name, value in features.items():
            trace.add("features", name, None, value=value)

        # Probability and EV ------------------------------------------------------------
        history = self._history(strategy.name)
        estimate = baseline(history.wins, history.total, settings.baseline_min_samples)
        prediction = self._predict(strategy.name, values)
        detail = estimate.text()
        if prediction is not None:
            estimate = prediction.estimate
            detail = estimate.text()
            if prediction.factors:
                factors = "; ".join(prediction.factors)
                detail += f"; main factors: {factors}"
                signal = replace(
                    signal,
                    features={**signal.features, "probability_factors": factors},
                )
        trace.add(
            "probability",
            "win probability",
            None,
            value=round(estimate.value * 100, 1) if estimate.value is not None else None,
            detail=detail,
        )
        ev = self._expected_value(estimate, signal, ctx.spread)
        formula = "probability x reward - (1 - probability) - spread in R"
        trace.add(
            "ev",
            "expected value (R)",
            None,
            value=round(ev, 3) if ev is not None else None,
            detail=formula if ev is not None else "needs a known probability",
        )

        # Filters -----------------------------------------------------------------------
        other = self._pending_twin(signal)
        last_loss = history.last_loss.get(signal.symbol)
        seconds = TF_SECONDS[signal.timeframe]
        since_loss: int | None = None
        if last_loss is not None:
            since_loss = int((signal.created_at - last_loss) // seconds)
        age = now - signal.created_at
        fresh = analysis.quality.ok and age <= seconds + FRESH_GRACE_SECONDS
        end = history.streak_end
        since_streak = (signal.created_at - end) / 3600.0 if end is not None else None
        same_side = self._same_side(signal)
        same_bet = self._same_bet(signal)
        data = FilterInput(
            signal=signal,
            now=now,
            clock=ctx.clock,
            atr=range_atr,
            spread=ctx.spread,
            probability=estimate,
            expected_value=ev,
            duplicate=other is not None,
            open_positions=self._open_positions(signal),
            losses_in_row=history.losses_in_row,
            bars_since_loss=since_loss,
            hours_since_losses=since_streak,
            sessions=strategy.sessions,
            events=ctx.events,
            data_ok=fresh,
            data_text=f"signal bar closed {age / 60:.0f} min ago; {analysis.quality.text()}",
            trade_mode=spec.trade_mode if spec is not None else SymbolTradeMode.UNKNOWN,
            duplicate_of=other.id if other is not None else "",
            same_side=len(same_side),
            same_side_of=", ".join(sorted({item.strategy for item in same_side})),
            same_bet=len(same_bet),
            same_bet_of=", ".join(same_bet),
        )
        trace.extend(run_filters(data, settings))
        trace.add(
            "filter",
            "still valid",
            now < signal.expires_at,
            value=_utc_text(signal.expires_at),
            detail="the signal expires at this time",
        )

        # Risk, decision ----------------------------------------------------------------
        failed = [step.name for step in trace.failed() if step.stage == "filter"]
        decision: RiskDecision | None = None
        if self._risk is None:
            trace.add("risk", "position size and limits", None, detail=RISK_NOTE)
        elif failed:
            trace.add("risk", "position size and limits", None, detail=FILTERED_RISK_NOTE)
        else:
            decision = self._risk.evaluate(signal, spec, ctx.clock, now)
            add_risk_steps(trace, decision, now)
        if failed:
            state, reason = SignalState.FILTERED_OUT, "; ".join(failed)
            detail = f"filtered out by: {reason}"
        elif decision is not None and not decision.ok:
            state, reason = SignalState.RISK_REJECTED, decision.reason
            detail = f"rejected by risk: {reason}"
        else:
            state, reason = SignalState.PENDING_APPROVAL, "all filters passed"
            if decision is not None:
                reason = f"all filters passed; {decision.volume:g} lots within every risk limit"
            detail = "waiting for your approval"
        rejected = state is not SignalState.PENDING_APPROVAL
        trace.add("decision", state.value, not rejected, detail=detail)
        trace.add(
            "execution",
            "expires at",
            None,
            value=_utc_text(signal.expires_at),
            detail=EXECUTION_NOTE if self._executor is not None else NO_EXECUTION_NOTE,
        )
        trace.final_decision = state.value
        signal = signal.with_state(state, now, reason)
        record = SignalRecord(
            signal=signal,
            trace=trace,
            probability=estimate,
            expected_value=ev,
            spread=ctx.spread,
            atr=range_atr,
            reject_reason=reason if rejected else "",
            volume=decision.volume if decision is not None and decision.ok else None,
            risk_money=decision.risk_money if decision is not None and decision.ok else None,
        )
        if not trace.complete:
            missing = ", ".join(trace.missing_stages())
            self._log("ERROR", f"Decision trace of {signal.id} is missing: {missing}")
        self._remember(record)
        self._save(record, strategy)
        verdict = {
            SignalState.FILTERED_OUT: "filtered out",
            SignalState.RISK_REJECTED: "rejected by risk",
        }.get(state, "waiting for approval")
        why = f"Because: {reason}" if rejected else f"Why: {signal.reason}"
        self._log("INFO", f"Signal {verdict}: {signal.summary()} [{strategy.name}]. {why}.")
        return record

    def _features(
        self,
        signal: Signal,
        ctx: MarketContext,
        range_atr: float,
    ) -> dict[str, float | str]:
        moment = datetime.fromtimestamp(signal.created_at, UTC)
        found: dict[str, float | str] = {
            "rr": round(signal.rr, 3),
            "hour_utc": moment.hour,
            "weekday": moment.weekday(),
            "session": " + ".join(s.value for s in active_sessions(signal.created_at)) or "none",
        }
        if math.isfinite(range_atr) and range_atr > 0:
            found[f"sl_atr_{ctx.timeframe}"] = round(signal.risk / range_atr, 3)
            if math.isfinite(ctx.spread):
                found[f"spread_atr_{ctx.timeframe}"] = round(ctx.spread / range_atr, 4)
        return found

    def _predict(self, strategy: str, values: Mapping[str, float]) -> Prediction | None:
        """The active model's estimate, or None (then the baseline is used)."""
        try:
            model = self._model()
            if model is None or not model.covers(strategy):
                return None
            return model.predict(values)
        except Exception as error:
            self._log("WARNING", f"The model failed, the baseline is used: {error}")
            return None

    def _expected_value(
        self,
        estimate: ProbabilityEstimate,
        signal: Signal,
        spread: float,
    ) -> float | None:
        if estimate.value is None:
            return None
        cost = cost_in_r(spread, signal.risk)
        value = expected_value_r(estimate.value, signal.rr, cost if math.isfinite(cost) else 0.0)
        return value if math.isfinite(value) else None

    def _known(self, signal_id: str) -> bool:
        if signal_id in self._records:
            return True
        if self._store is None:
            return False
        try:
            return self._store.exists(signal_id)
        except Exception:
            return False

    def _history(self, strategy: str) -> _History:
        if self._store is None:
            return _History()
        try:
            return history_of(self._store.results(strategy))
        except Exception:
            return _History()

    def _pending_twin(self, signal: Signal) -> Signal | None:
        for record in self._records.values():
            other = record.signal
            if (
                other.state is SignalState.PENDING_APPROVAL
                and other.strategy == signal.strategy
                and other.symbol == signal.symbol
                and other.direction is signal.direction
                and other.id != signal.id
                and not _same_group(other, signal)
            ):
                return other
        return None

    def _open_positions(self, signal: Signal) -> int:
        return sum(
            1
            for record in self._records.values()
            if record.signal.state.open_position
            and record.signal.strategy == signal.strategy
            and record.signal.symbol == signal.symbol
            and not _same_group(record.signal, signal)
        )

    def _same_side(self, signal: Signal) -> list[Signal]:
        """Other strategies' signals on this symbol and side that are waiting, on their way
        or open: a second one would double the risk on the same move."""
        return [
            record.signal
            for record in self._records.values()
            if record.signal.state in LIVE_STATES
            and record.signal.strategy != signal.strategy
            and record.signal.symbol == signal.symbol
            and record.signal.direction is signal.direction
            and record.signal.id != signal.id
        ]

    def _same_bet(self, signal: Signal) -> list[str]:
        """Live trades on other symbols (any strategy) that bet the same way on a currency,
        as 'london_breakout EURUSD sell (long USD)'."""
        found: list[str] = []
        for record in self._records.values():
            other = record.signal
            if other.state not in LIVE_STATES or other.id == signal.id:
                continue
            bet = shared_bet(signal.symbol, signal.direction, other.symbol, other.direction)
            if bet:
                side = "buy" if other.direction.sign > 0 else "sell"
                found.append(f"{other.strategy} {other.symbol} {side} ({bet})")
        return sorted(found)

    # The Signal desk ----------------------------------------------------------------------
    def _desk_safe(self, request: DeskRequest, now: float) -> DeskResult:
        try:
            return self._desk_one(request, now)
        except Exception as error:
            text = f"the signal could not be checked: {type(error).__name__}: {error}"
            self._log("ERROR", f"Signal desk {request.id}: {text}")
            return DeskResult(request.id, False, text, at=now)

    def _desk_one(self, request: DeskRequest, now: float) -> DeskResult:
        parsed = request.parsed
        if not parsed.complete:
            missing = ", ".join(parsed.missing)
            return DeskResult(request.id, False, f"missing: {missing}", at=now)
        symbol = resolve_symbol(parsed.symbol, tuple(self._latest))
        latest = self._latest.get(symbol)
        if latest is None:
            why = f"{parsed.symbol} is not on the watchlist yet: add it on the Market page"
            return DeskResult(request.id, False, why, at=now)
        analysis, spec = latest.analysis, latest.spec
        point = spec.point if spec is not None and spec.point > 0 else 10.0**-analysis.digits
        strategy = self._manual
        ctx = build_context(
            analysis,
            strategy.entry_timeframe,
            clock=latest.clock,
            now=now,
            point=point,
            spread=latest.spread,
        )
        bid, ask = self._bid_ask(analysis, latest.spread)
        stops = spec.stops_level if spec is not None else 0
        market = Market(bid, ask, entry_atr(ctx), analysis.digits, point, stops, market_open(now))
        found = plan(replace(parsed, symbol=symbol), market)
        if not found.ok:
            return DeskResult(request.id, False, "; ".join(found.problems), found, at=now)
        parent = self._legs(request, ctx, found, (1.0,), now)[0]
        shares = self._shares(parent, len(found.tps), spec, latest.clock, now)
        signals = self._legs(request, ctx, found, shares, now)
        evaluation = strategy.checked(ctx, found, signals)
        records = [
            self._process(strategy, signal, evaluation, ctx, analysis, spec, now)
            for signal in signals
            if not self._known(signal.id)
        ]
        states = [record.signal.state.value for record in records]
        ok, message = summary(states, [record.reject_reason for record in records])
        self._log("INFO", f"Signal desk ({request.source}): {found.symbol}: {message}")
        return DeskResult(request.id, ok, message, found, tuple(r.id for r in records), now)

    def _legs(
        self,
        request: DeskRequest,
        ctx: MarketContext,
        found: OrderPlan,
        shares: Sequence[float],
        now: float,
    ) -> tuple[Signal, ...]:
        minutes = request.market_minutes
        if found.order is not OrderType.MARKET:
            minutes = request.pending_minutes
        return self._manual.legs(
            ctx,
            found,
            group=request.id,
            shares=shares,
            source=request.source,
            reason=desk_reason(request),
            created_at=now,
            expires_at=now + 60.0 * minutes,
        )

    def _shares(
        self,
        parent: Signal,
        targets: int,
        spec: SymbolSpec | None,
        clock: BrokerClock,
        now: float,
    ) -> tuple[float, ...]:
        """The whole signal is sized once (as one trade), then split over its targets."""
        if self._risk is None or spec is None:
            return leg_shares(math.nan, targets, 0.0, 0.0)
        decision = self._risk.evaluate(parent, spec, clock, now)
        if not decision.ok:
            return (1.0,)  # one leg, so the card shows the risk manager's reason once
        return leg_shares(decision.volume, targets, spec.volume_min, spec.volume_step)

    def _bid_ask(self, analysis: SymbolAnalysis, spread: float) -> tuple[float, float]:
        if self._quote is not None:
            with contextlib.suppress(Exception):
                found = self._quote(analysis.symbol)
                if found is not None:
                    return found
        gap = spread if math.isfinite(spread) and spread > 0 else 0.0
        return analysis.price, analysis.price + gap

    def _keep(self, result: DeskResult) -> None:
        self._desk[result.request_id] = result
        if len(self._desk) > KEEP_RESULTS:
            oldest = min(self._desk.values(), key=lambda item: item.at)
            del self._desk[oldest.request_id]

    def _finish(self, signal_id: str, state: SignalState, now: float, reason: str) -> bool:
        record = self._records.get(signal_id)
        if record is None or state not in TRANSITIONS[record.signal.state]:
            return False
        signal = record.signal.with_state(state, now, reason)
        trace = record.trace
        trace.add("decision", state.value, None, detail=reason, at=now)
        trace.final_decision = state.value
        updated = replace(record, signal=signal, trace=trace)
        self._records[signal_id] = updated
        self._save(updated, None)
        self._log("INFO", f"Signal {state.value.lower()}: {signal.summary()} ({reason})")
        return True

    def _send(self, signal_id: str, now: float) -> bool:
        record = self._records.get(signal_id)
        if record is None or record.signal.state is not SignalState.PENDING_APPROVAL:
            return False
        if self._executor is None:
            return self._finish(signal_id, SignalState.EXPIRED, now, NO_EXECUTION_NOTE)
        self._log("INFO", f"Signal approved by the user: {record.signal.summary()}")
        changed = False
        for update in self._executor.execute(record, now):
            changed |= self._apply(update, now)
        return changed

    def _apply(self, update: SignalUpdate, now: float) -> bool:
        """One state change from the execution engine, with its trace lines."""
        record = self._records.get(update.signal_id)
        if record is None or update.state not in TRANSITIONS[record.signal.state]:
            return False
        for check in update.checks:
            record.trace.add(
                "execution",
                check.name,
                check.passed,
                value=check.value,
                threshold=check.threshold,
                detail=check.detail,
                at=now,
            )
        changed = self._finish(update.signal_id, update.state, now, update.reason)
        group = record.signal.features.get("oco_group")
        if changed and update.state is SignalState.FILLED and group:
            for other in list(self._records.values()):
                twin = other.signal
                waiting = twin.state is SignalState.PENDING_APPROVAL
                if waiting and twin.features.get("oco_group") == group:
                    self._finish(twin.id, SignalState.EXPIRED, now, OCO_REASON)
        return changed

    def _remember(self, record: SignalRecord) -> None:
        self._records[record.id] = record
        if len(self._records) > KEEP_SIGNALS:
            ordered = sorted(self._records.values(), key=lambda item: item.signal.created_at)
            for old in ordered[: len(self._records) - KEEP_SIGNALS]:
                state = old.signal.state
                if state is not SignalState.PENDING_APPROVAL and not state.open_position:
                    del self._records[old.id]

    def _save(self, record: SignalRecord, strategy: Strategy | None) -> None:
        if self._store is None:
            return
        params: Mapping[str, Any] = {}
        if strategy is not None:
            params = strategy.params.model_dump(mode="json")
        try:
            self._store.save(record, params, self._account())
            self._store_failed = False
        except Exception as error:
            if not self._store_failed:
                name = type(error).__name__
                self._log("WARNING", f"Signal {record.id} could not be saved: {name}: {error}")
            self._store_failed = True

    def _approval_block(self) -> str:
        if self._executor is None:
            return NO_EXECUTION_NOTE
        try:
            return self._executor.approval_block()
        except Exception as error:
            return f"execution engine error: {type(error).__name__}"

    def _publish(self, message: str) -> None:
        records = sorted(
            self._records.values(),
            key=lambda item: (item.signal.created_at, item.signal.id),
            reverse=True,
        )
        names = tuple(strategy.name for strategy in self._strategies())
        snapshot = SignalsSnapshot(
            signals=tuple(records),
            scanner=tuple(rank(self._scan.values())),
            strategies=names,
            message=message,
            updated_at=self._now(),
            approval_block=self._approval_block(),
            desk=tuple(sorted(self._desk.values(), key=lambda item: item.at, reverse=True)),
            symbols=tuple(sorted(self._latest)),
        )
        with self._lock:
            self._snapshot = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(snapshot)
