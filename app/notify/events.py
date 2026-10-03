"""What the app tells you about (spec C14), found by comparing two snapshots of the engine.

Pure: each function takes the previous and the new snapshot of one part (execution, signals,
risk, connection, cloud sync) and returns the notices for what changed. The first snapshot
of a run never notifies (nothing "changed" when the app starts).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from app.engine.execution import ExecutionSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.risk.risk_manager import RiskSnapshot
from app.storage.sync import SyncState, SyncStatus


class EventKind(StrEnum):
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    APPROVAL_NEEDED = "approval_needed"
    LIMIT_HIT = "limit_hit"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    SYNC_FAILING = "sync_failing"
    DRIFT = "drift"
    DAILY_REPORT = "daily_report"

    @property
    def title(self) -> str:
        return TITLES[self]

    @property
    def urgent(self) -> bool:
        """Urgent alerts are delivered during quiet hours too."""
        return self in URGENT


TITLES = {
    EventKind.TRADE_OPENED: "Trade opened",
    EventKind.TRADE_CLOSED: "Trade closed",
    EventKind.APPROVAL_NEEDED: "Approval needed",
    EventKind.LIMIT_HIT: "Trading stopped by a limit",
    EventKind.DISCONNECTED: "MT5 disconnected",
    EventKind.ERROR: "Error",
    EventKind.SYNC_FAILING: "Cloud sync is failing",
    EventKind.DRIFT: "The model may need training",
    EventKind.DAILY_REPORT: "Report",
}
URGENT = frozenset({EventKind.LIMIT_HIT, EventKind.DISCONNECTED, EventKind.ERROR})
FAILING_SYNC = frozenset({SyncState.OFFLINE, SyncState.ERROR, SyncState.SETUP_NEEDED})


@dataclass(frozen=True)
class Notice:
    kind: EventKind
    text: str
    key: str = ""  # the same key is sent once per quiet period (repeats are dropped)
    at: float = field(default_factory=time.time)

    @property
    def title(self) -> str:
        return self.kind.title


def execution_notices(old: ExecutionSnapshot | None, new: ExecutionSnapshot) -> list[Notice]:
    if old is None:
        return []
    found: list[Notice] = []
    before = {(p.mode, p.ticket): p for p in old.positions if not p.pending}
    after = {(p.mode, p.ticket): p for p in new.positions if not p.pending}
    for key, view in after.items():
        if key not in before:
            side = "Buy" if view.direction in ("buy", "long") else "Sell"
            text = (
                f"{side} {view.volume:g} {view.symbol} at {view.entry:g} ({view.mode}), "
                f"SL {view.sl:g}, TP {view.tp:g} [{view.strategy}]"
            )
            found.append(Notice(EventKind.TRADE_OPENED, text, f"open:{key}"))
    for key, view in before.items():
        if key not in after:
            result = f", last result {view.profit:+,.2f}" if view.profit is not None else ""
            text = f"{view.symbol} position {view.ticket} ({view.mode}) closed{result}"
            found.append(Notice(EventKind.TRADE_CLOSED, text, f"close:{key}"))
    if new.stopped and not old.stopped:
        found.append(Notice(EventKind.LIMIT_HIT, f"Kill switch: {new.stopped}", "kill"))
    return found


def signal_notices(old: SignalsSnapshot | None, new: SignalsSnapshot) -> list[Notice]:
    if old is None:
        return []
    known = {record.id for record in old.pending()}
    found: list[Notice] = []
    for record in new.pending():
        if record.id in known:
            continue
        signal = record.signal
        chance = (
            f", chance {record.probability.value * 100:.0f}%"
            if record.probability.value is not None
            else ""
        )
        ask = f"Approve or skip it before it expires. Id {signal.id[:8]}"
        text = f"{signal.summary()}{chance}. {ask}"
        found.append(Notice(EventKind.APPROVAL_NEEDED, text, f"approve:{signal.id}"))
    return found


def risk_notices(old: RiskSnapshot | None, new: RiskSnapshot) -> list[Notice]:
    if old is None or not new.halted or new.halted == old.halted:
        return []
    reason = new.usage.halted_reason if new.usage is not None else ""
    text = f"New entries are stopped: {reason or new.halted}"
    return [Notice(EventKind.LIMIT_HIT, text, f"limit:{new.halted}")]


def connection_notices(old: ConnectionStatus | None, new: ConnectionStatus) -> list[Notice]:
    if old is None or not old.connected or new.connected:
        return []
    if new.state is ConnectionState.DISCONNECTED and not new.open_positions:
        return []  # the user disconnected on purpose
    extra = f" with {new.open_positions} open position(s)" if new.open_positions else ""
    text = f"The connection to MetaTrader 5 was lost{extra}. {new.message}"
    return [Notice(EventKind.DISCONNECTED, text, "disconnect")]


def sync_notices(old: SyncStatus | None, new: SyncStatus) -> list[Notice]:
    if old is None or new.state not in FAILING_SYNC or old.state in FAILING_SYNC:
        return []
    text = f"{new.message} ({new.pending:,} row(s) waiting; nothing is lost)"
    return [Notice(EventKind.SYNC_FAILING, text, f"sync:{new.state.value}")]
