"""What the Telegram commands do (spec C14), on the same engine as the app's buttons.

`/pause` asks the risk manager to stop new entries; `/resume` lifts only a stop that
`/pause` made (any other stop needs the typed ENABLE on the Risk page); `/approve` queues
the same approval as the Signals page; `/killswitch` is the app's kill switch.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

from app.engine.execution import ExecutionSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.connection import ConnectionStatus
from app.risk.limits_state import MANUAL_STOP
from app.risk.risk_manager import RiskSnapshot

PAUSE_REASON = "paused from Telegram"
KILL_REPLY = "Kill switch: closing the bot's positions, cancelling its orders, stopping entries."


class Engine(Protocol):
    @property
    def snapshot(self) -> ExecutionSnapshot: ...

    def request_kill(self, reason: str = ...) -> None: ...


class Pipeline(Protocol):
    @property
    def snapshot(self) -> SignalsSnapshot: ...

    def approve(self, signal_id: str) -> None: ...


class Risk(Protocol):
    @property
    def snapshot(self) -> RiskSnapshot: ...

    def request_stop(self, reason: str = ...) -> None: ...

    def request_enable(self) -> None: ...


class EngineRemote:
    def __init__(
        self,
        engine: Engine,
        pipeline: Pipeline,
        risk: Risk,
        connection: Callable[[], ConnectionStatus],
        today: Callable[[], tuple[float, int]],  # closed net and trade count today
    ) -> None:
        self._engine = engine
        self._pipeline = pipeline
        self._risk = risk
        self._connection = connection
        self._today = today

    def status(self) -> str:
        status = self._connection()
        snapshot = self._engine.snapshot
        risk = self._risk.snapshot
        bot = f"stopped ({snapshot.stopped})" if snapshot.stopped else "running"
        halted = risk.usage.halted_reason if risk.usage is not None and risk.halted else ""
        lines = [
            f"Mode: {snapshot.mode.label}",
            f"MT5: {status.status_bar_text()}",
            f"Bot: {bot}",
            f"New entries: {'stopped: ' + halted if halted else 'allowed'}",
            f"Open positions: {sum(1 for p in snapshot.positions if not p.pending)}",
            f"Waiting for approval: {len(self._pipeline.snapshot.pending())}",
        ]
        return "\n".join(lines)

    def positions(self) -> str:
        views = self._engine.snapshot.positions
        if not views:
            return "No open positions or orders."
        lines = []
        for view in views:
            side = "Buy" if view.direction in ("buy", "long") else "Sell"
            result = f" {view.profit:+,.2f}" if view.profit is not None else ""
            kind = " (pending)" if view.pending else ""
            lines.append(
                f"{side} {view.volume:g} {view.symbol} at {view.entry:g}{kind}, SL {view.sl:g}, "
                f"TP {view.tp:g}{result} [{view.mode}]"
            )
        return "\n".join(lines)

    def pnl(self) -> str:
        net, count = self._today()
        views = self._engine.snapshot.positions
        open_profit = sum(p.profit for p in views if p.profit is not None and not p.pending)
        usage = self._risk.snapshot.usage
        text = f"Today: {net:+,.2f} from {count} closed trade(s); open {open_profit:+,.2f}"
        if usage is not None and math.isfinite(usage.equity):
            loss = f"daily loss {usage.daily_loss_percent:.2f}%"
            text += f"\nEquity {usage.equity:,.2f} {usage.currency}, {loss}"
        return text

    def pause(self) -> str:
        self._risk.request_stop(PAUSE_REASON)
        return "New entries are stopped. Open trades keep their stop loss and take profit."

    def resume(self) -> str:
        usage = self._risk.snapshot.usage
        if usage is None or not usage.halted:
            return "New entries are already allowed."
        if usage.halted != MANUAL_STOP or usage.halted_reason != PAUSE_REASON:
            reason = usage.halted_reason or usage.halted
            return f"Stopped by: {reason}. Re-enable it on the Risk page."
        self._risk.request_enable()
        return "New entries are allowed again."

    def approve(self, signal_id: str) -> str:
        wanted = signal_id.strip().lower()
        matches = [r for r in self._pipeline.snapshot.pending() if r.id.lower().startswith(wanted)]
        if not matches:
            return "No waiting signal has that id."
        if len(matches) > 1:
            return "More than one signal matches: send more characters of the id."
        record = matches[0]
        self._pipeline.approve(record.id)
        return f"Approved: {record.signal.summary()}. It is checked again at the live price."

    def kill(self) -> str:
        self._engine.request_kill("kill switch from Telegram")
        return KILL_REPLY
