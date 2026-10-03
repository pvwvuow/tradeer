"""Reads and writes of the app's tables (spec E2).

Every write of a synced table also puts the full row in the outbox, inside the same
transaction. A row that did not change is neither written nor uploaded again, so importing
the same MT5 history twice costs nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from app.domain.history import (
    Deal,
    Order,
    PositionSummary,
    trade_source,
)
from app.mt5.models import AccountSnapshot
from app.storage import outbox
from app.storage.ids import account_id, new_id, trade_id
from app.storage.outbox import OutboxCounts
from app.storage.schema import TABLES, to_local, to_remote
from app.storage.sqlite_db import Database, iso_utc

CHUNK = 500
DEAL_COLUMNS = (
    "ticket, order_ticket, time, time_msc, type, entry, magic, position_id, reason, volume, "
    "price, commission, swap, profit, fee, symbol, comment"
)
ORDER_COLUMNS = (
    "ticket, time_setup, time_done, type, state, magic, position_id, volume_initial, "
    "volume_current, price_open, sl, tp, price_current, symbol, comment"
)
LOG_FIELDS = (
    "session_id",
    "level",
    "category",
    "module",
    "function",
    "line",
    "message",
    "trace_id",
    "signal_id",
    "trade_id",
    "symbol",
    "error_code",
    "exception_type",
    "stack_trace",
)


def _chunks(values: Sequence[Any], size: int = CHUNK) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _upsert_sql(table: str, names: Sequence[str], conflict: str = "id") -> str:
    keys = {key.strip() for key in conflict.split(",")}
    updates = ", ".join(f"{name} = excluded.{name}" for name in names if name not in keys)
    placeholders = ", ".join("?" for _ in names)
    columns = ", ".join(names)
    action = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    insert = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
    return f"{insert} ON CONFLICT ({conflict}) {action}"


class Store:
    def __init__(self, db: Database, clock: Callable[[], str] = iso_utc) -> None:
        self.db = db
        self._now = clock

    # Generic --------------------------------------------------------------------------
    def upsert(self, table: str, row: Mapping[str, Any]) -> bool:
        """Insert or update one synced row by `id`. Returns False when nothing changed."""
        with self.db.transaction() as connection:
            return self.upsert_in(connection, table, row)

    def upsert_in(self, connection: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> bool:
        """Like `upsert`, inside the caller's transaction."""
        values = to_local(table, row)
        if "id" not in values:
            raise KeyError(f"A {table} row needs an id")
        existing = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (values["id"],))
        current: dict[str, Any] | None = existing.fetchone()
        if current is not None:
            changed = [name for name in values if values[name] != current.get(name)]
            if not [name for name in changed if name not in ("created_at", "updated_at")]:
                return False
            values["created_at"] = current["created_at"]
        else:
            values.setdefault("created_at", self._now())
        values["updated_at"] = self._now()
        names = list(values)
        connection.execute(_upsert_sql(table, names), [values[name] for name in names])
        full: dict[str, Any] = dict.fromkeys(TABLES[table])
        full.update(current or {})
        full.update(values)
        outbox.enqueue(connection, table, to_remote(table, full))
        return True

    def get(self, table: str, row_id: str) -> dict[str, Any] | None:
        if table not in TABLES:
            raise KeyError(table)
        rows = self.db.query(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        return rows[0] if rows else None

    def count(self, table: str, account: str | None = None) -> int:
        if table not in TABLES and table not in ("mt5_deals", "mt5_orders"):
            raise KeyError(table)
        if account is None:
            return int(self.db.scalar(f"SELECT COUNT(*) FROM {table}"))
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE account_id = ?", (account,)))

    # Sync state -------------------------------------------------------------------------
    def get_state(self, key: str) -> str | None:
        value = self.db.scalar("SELECT value FROM sync_state WHERE key = ?", (key,))
        return str(value) if value is not None else None

    def set_state(self, key: str, value: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                _upsert_sql("sync_state", ("key", "value", "updated_at"), "key"),
                (key, value, self._now()),
            )

    def outbox_counts(self) -> OutboxCounts:
        return outbox.counts(self.db.connection())

    def retry_failed(self) -> int:
        with self.db.transaction() as connection:
            return outbox.retry_failed(connection)

    # Accounts, sessions and snapshots ----------------------------------------------------
    def upsert_account(self, account: AccountSnapshot) -> str:
        identifier = account_id(account.server, account.login)
        row = {
            "id": identifier,
            "broker": account.company,
            "server": account.server,
            "login": account.login,
            "name": account.name,
            "type": account.kind.value,
            "currency": account.currency,
            "leverage": account.leverage,
            "margin_mode": account.margin_mode.value,
        }
        self.upsert("accounts", row)
        return identifier

    def start_session(
        self,
        session_id: str,
        *,
        app_version: str,
        profile: str,
        mode: str,
        settings: Mapping[str, Any],
    ) -> None:
        row = {
            "id": session_id,
            "app_version": app_version,
            "profile": profile,
            "mode": mode,
            "started_at": self._now(),
            "settings_json": dict(settings),
        }
        self.upsert("sessions", row)

    def set_session_account(self, session_id: str, account: str) -> None:
        self.upsert("sessions", {"id": session_id, "account_id": account})

    def end_session(self, session_id: str) -> None:
        self.upsert("sessions", {"id": session_id, "ended_at": self._now()})

    def record_snapshot(self, account: str, snapshot: AccountSnapshot, positions: int) -> str:
        identifier = new_id()
        row = {
            "id": identifier,
            "account_id": account,
            "time": self._now(),
            "balance": snapshot.balance,
            "equity": snapshot.equity,
            "margin": snapshot.margin,
            "free_margin": snapshot.margin_free,
            "margin_level": snapshot.margin_level,
            "open_positions": positions,
        }
        self.upsert("account_snapshots", row)
        return identifier

    # Logs and audit ---------------------------------------------------------------------
    def record_log(self, entry: Mapping[str, Any], account: str | None = None) -> str:
        """A WARNING+ log entry (already masked) as an `app_logs` row."""
        identifier = new_id()
        row: dict[str, Any] = {"id": identifier, "account_id": account, "time": entry.get("time")}
        for field in LOG_FIELDS:
            value = entry.get(field)
            row[field] = str(value) if value is not None and field != "line" else value
        row["context_json"] = entry.get("context")
        self.upsert("app_logs", row)
        return identifier

    def record_audit(self, entry: Mapping[str, Any], account: str | None = None) -> str:
        """An audit entry (category `audit`, already masked) as an `audit_log` row."""
        context = entry.get("context")
        details: Mapping[str, Any] = context if isinstance(context, Mapping) else {}
        identifier = new_id()
        row = {
            "id": identifier,
            "account_id": account,
            "session_id": entry.get("session_id"),
            "time": entry.get("time"),
            "source": str(details.get("source") or "user"),
            "action": str(details.get("action") or entry.get("message") or ""),
            "before_json": details.get("before"),
            "after_json": details.get("after"),
        }
        self.upsert("audit_log", row)
        return identifier

    # MT5 history ------------------------------------------------------------------------
    def upsert_deals(self, account: str, deals: Sequence[Deal]) -> int:
        """Save raw deals (local only). Returns how many were new."""
        before = self.count("mt5_deals", account)
        names = ["account_id", *(name.strip() for name in DEAL_COLUMNS.split(",")), "imported_at"]
        sql = _upsert_sql("mt5_deals", names, "account_id, ticket")
        now = self._now()
        with self.db.transaction() as connection:
            for chunk in _chunks(deals):
                rows = [(account, *_deal_values(deal), now) for deal in chunk]
                connection.executemany(sql, rows)
        return self.count("mt5_deals", account) - before

    def upsert_orders(self, account: str, orders: Sequence[Order]) -> int:
        before = self.count("mt5_orders", account)
        names = ["account_id", *(name.strip() for name in ORDER_COLUMNS.split(",")), "imported_at"]
        sql = _upsert_sql("mt5_orders", names, "account_id, ticket")
        now = self._now()
        with self.db.transaction() as connection:
            for chunk in _chunks(orders):
                rows = [(account, *asdict(order).values(), now) for order in chunk]
                connection.executemany(sql, rows)
        return self.count("mt5_orders", account) - before

    def deals_for_positions(self, account: str, positions: Collection[int]) -> list[Deal]:
        found: list[Deal] = []
        ordered = sorted(positions)
        for chunk in _chunks(ordered):
            marks = ", ".join("?" for _ in chunk)
            rows = self.db.query(
                f"SELECT {DEAL_COLUMNS} FROM mt5_deals WHERE account_id = ? "
                f"AND position_id IN ({marks})",
                (account, *chunk),
            )
            found += [_deal_from_row(row) for row in rows]
        return found

    def upsert_trades(
        self,
        account: str,
        summaries: Sequence[PositionSummary],
        *,
        to_utc: Callable[[int], str],
        bot_magics: Collection[int] = (),
    ) -> int:
        """Save rebuilt trades. Returns how many were new or changed.

        `to_utc` turns a broker server time into UTC text (it knows the summer-time rules).
        """
        changed = 0
        with self.db.transaction() as connection:
            for summary in summaries:
                row = _trade_row(account, summary, to_utc, bot_magics)
                changed += self.upsert_in(connection, "trades", row)
        return changed


def _deal_values(deal: Deal) -> tuple[Any, ...]:
    values = asdict(deal)
    values["order_ticket"] = values.pop("order")
    names = [name.strip() for name in DEAL_COLUMNS.split(",")]
    return tuple(values[name] for name in names)


def _deal_from_row(row: Mapping[str, Any]) -> Deal:
    return Deal(
        ticket=int(row["ticket"]),
        order=int(row["order_ticket"] or 0),
        time=int(row["time"]),
        time_msc=int(row["time_msc"] or 0),
        type=int(row["type"]),
        entry=int(row["entry"] or 0),
        magic=int(row["magic"] or 0),
        position_id=int(row["position_id"] or 0),
        reason=int(row["reason"] or 0),
        volume=float(row["volume"] or 0.0),
        price=float(row["price"] or 0.0),
        commission=float(row["commission"] or 0.0),
        swap=float(row["swap"] or 0.0),
        profit=float(row["profit"] or 0.0),
        fee=float(row["fee"] or 0.0),
        symbol=str(row["symbol"] or ""),
        comment=str(row["comment"] or ""),
    )


def _trade_row(
    account: str,
    summary: PositionSummary,
    to_utc: Callable[[int], str],
    bot_magics: Collection[int],
) -> dict[str, Any]:
    close_time = summary.close_time
    return {
        "id": trade_id(account, summary.position_id),
        "account_id": account,
        "mode": "live",
        "source": trade_source(summary.magic, bot_magics),
        "ticket": summary.position_id,
        "position_id": summary.position_id,
        "magic": summary.magic,
        "symbol": summary.symbol,
        "direction": summary.direction,
        "volume": summary.volume,
        "open_price": summary.open_price,
        "open_time": to_utc(summary.open_time),
        "close_time": to_utc(close_time) if close_time else None,
        "close_price": summary.close_price,
        "profit": summary.profit,
        "commission": summary.commission,
        "swap": summary.swap,
        "fee": summary.fee,
        "net_profit": summary.net_profit,
        "outcome": summary.outcome,
        "exit_reason": summary.exit_reason,
        "duration_sec": summary.duration_sec,
    }
