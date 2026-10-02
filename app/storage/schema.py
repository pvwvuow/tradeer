"""The synced tables of spec E2 and the kind of every column.

One definition serves the local SQLite schema (migration 1), the payloads sent to Supabase
and `supabase/schema.sql`. `tests/unit/test_storage_schema.py` checks that all three agree, so
a column can never exist on one side only. Change a table only together with a new migration
and the matching `supabase/` change.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# Column kind -> (SQLite type, PostgreSQL type).
KINDS: dict[str, tuple[str, str]] = {
    "uuid": ("TEXT", "uuid"),
    "text": ("TEXT", "text"),
    "int": ("INTEGER", "bigint"),
    "real": ("REAL", "double precision"),
    "bool": ("INTEGER", "boolean"),
    "time": ("TEXT", "timestamptz"),
    "date": ("TEXT", "date"),
    "json": ("TEXT", "jsonb"),
}
SYSTEM_COLUMNS = ("id", "created_at", "updated_at")


def _table(spec: str, *, account: bool = True) -> dict[str, str]:
    items = ["id uuid", *(["account_id uuid"] if account else []), *spec.split(",")]
    items += ["created_at time", "updated_at time"]
    columns: dict[str, str] = {}
    for item in items:
        name, kind = item.split()
        if kind not in KINDS:
            raise ValueError(f"Unknown column kind {kind!r} for {name}")
        columns[name] = kind
    return columns


# Parents first: the sync uploads tables in this order.
TABLES: dict[str, dict[str, str]] = {
    "accounts": _table(
        "broker text, server text, login int, name text, type text, currency text, "
        "leverage int, margin_mode text",
        account=False,
    ),
    "sessions": _table(
        "app_version text, profile text, mode text, started_at time, ended_at time, "
        "settings_json json",
    ),
    "strategy_configs": _table(
        "strategy text, version text, params_json json, params_hash text, created_by text, "
        "parent_config_id uuid, notes text, is_active bool",
    ),
    "signals": _table(
        "bar_time time, symbol text, tf text, strategy text, strategy_version text, "
        "config_id uuid, direction text, order_type text, entry real, sl real, tp real, "
        "rr real, spread real, atr real, win_probability real, prob_ci_low real, "
        "prob_ci_high real, probability_source text, expected_value real, model_version text, "
        "features_json json, shap_top_json json, reason text, state text, decision text, "
        "reject_reason text, trace_id text",
    ),
    "decision_traces": _table(
        "signal_id uuid, trace_id text, steps_json json, final_decision text",
    ),
    "trades": _table(
        "signal_id uuid, mode text, source text, ticket int, position_id int, magic int, "
        "symbol text, direction text, volume real, requested_price real, open_price real, "
        "slippage real, open_time time, sl_initial real, tp_initial real, risk_money real, "
        "close_time time, close_price real, profit real, commission real, swap real, fee real, "
        "net_profit real, r_multiple real, outcome text, exit_reason text, duration_sec int, "
        "mfe_r real, mae_r real, predicted_probability real, session_label text",
    ),
    "trade_events": _table(
        "trade_id uuid, time time, type text, old_value text, new_value text, reason text, "
        "payload_json json",
    ),
    "mt5_requests": _table(
        "trace_id text, action text, request_json json, retcode int, retcode_text text, "
        "result_json json, last_error text, latency_ms real, attempt int",
    ),
    "account_snapshots": _table(
        "time time, balance real, equity real, margin real, free_margin real, margin_level real, "
        "open_positions int, open_risk real, daily_pnl real, drawdown_pct real",
    ),
    "risk_events": _table("time time, type text, details_json json"),
    "model_versions": _table(
        "strategy text, features_json json, schema_hash text, train_period text, symbols json, "
        "metrics_json json, file_hash text, is_active bool",
    ),
    "backtest_runs": _table(
        "config_id uuid, period text, costs_json json, metrics_json json, "
        "walk_forward_json json, monte_carlo_json json",
    ),
    "journal": _table(
        "trade_id uuid, narrative text, snapshots json, notes text, tags json, rating int, "
        "emotion text",
    ),
    "audit_log": _table(
        "session_id text, time time, source text, action text, before_json json, after_json json",
    ),
    "app_logs": _table(
        "session_id text, time time, level text, category text, module text, function text, "
        "line int, message text, trace_id text, signal_id text, trade_id text, symbol text, "
        "error_code text, exception_type text, stack_trace text, context_json json",
    ),
    "health_checks": _table("time time, name text, status text, value real, details_json json"),
    "performance_metrics": _table(
        "time time, name text, value real, unit text, details_json json",
    ),
    "daily_reports": _table("report_date date, summary_json json"),
    "calendar_events": _table(
        "time time, currency text, impact text, title text, actual text, forecast text, "
        "previous text, source text",
    ),
}

# The time column each table is indexed and cleaned up by (default `created_at`).
TIME_COLUMNS: dict[str, str] = {
    "sessions": "started_at",
    "signals": "bar_time",
    "trades": "open_time",
    "trade_events": "time",
    "account_snapshots": "time",
    "risk_events": "time",
    "audit_log": "time",
    "app_logs": "time",
    "health_checks": "time",
    "performance_metrics": "time",
    "daily_reports": "report_date",
    "calendar_events": "time",
}

EXTRA_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("accounts", ("server", "login")),
    ("signals", ("symbol", "bar_time")),
    ("signals", ("strategy", "bar_time")),
    ("trades", ("symbol", "open_time")),
    ("trades", ("position_id",)),
    ("trades", ("signal_id",)),
    ("trade_events", ("trade_id",)),
    ("decision_traces", ("signal_id",)),
    ("strategy_configs", ("strategy",)),
    ("model_versions", ("strategy",)),
    ("journal", ("trade_id",)),
    ("calendar_events", ("currency", "time")),
)


def time_column(table: str) -> str:
    return TIME_COLUMNS.get(table, "created_at")


def indexes() -> list[tuple[str, tuple[str, ...]]]:
    """(table, columns) of every index, the same locally and in the cloud."""
    found: list[tuple[str, tuple[str, ...]]] = [
        (table, ("account_id", time_column(table)))
        for table, columns in TABLES.items()
        if "account_id" in columns
    ]
    return [*found, *EXTRA_INDEXES]


def index_name(table: str, columns: tuple[str, ...]) -> str:
    return f"{table}_{'_'.join(columns)}_idx"


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def to_local(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Python values -> SQLite values: JSON as text, booleans as 0 or 1."""
    kinds = TABLES[table]
    unknown = sorted(set(row) - set(kinds))
    if unknown:
        raise KeyError(f"Unknown columns for {table}: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for name, value in row.items():
        kind = kinds[name]
        if value is None:
            values[name] = None
        elif kind == "json":
            values[name] = json_text(value)
        elif kind in ("bool", "int"):
            values[name] = int(value)
        elif kind == "real":
            values[name] = float(value)
        else:
            values[name] = str(value)
    return values


def to_remote(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """A full local row -> the JSON object PostgREST receives (every column, same keys)."""
    payload: dict[str, Any] = {}
    for name, kind in TABLES[table].items():
        value = row.get(name)
        if value is None:
            payload[name] = None
        elif kind == "json":
            payload[name] = json.loads(value)
        elif kind == "bool":
            payload[name] = bool(value)
        else:
            payload[name] = value
    return payload


def sqlite_ddl() -> str:
    """CREATE statements for the synced tables. Migration 1 holds a frozen copy."""
    parts: list[str] = []
    for table, columns in TABLES.items():
        lines = []
        for name, kind in columns.items():
            sql_type = KINDS[kind][0]
            if name == "id":
                lines.append(f"    id {sql_type} PRIMARY KEY")
            elif name in SYSTEM_COLUMNS:
                lines.append(f"    {name} {sql_type} NOT NULL")
            else:
                lines.append(f"    {name} {sql_type}")
        parts.append(f"CREATE TABLE {table} (\n" + ",\n".join(lines) + "\n);")
    for table, indexed in indexes():
        name = index_name(table, indexed)
        parts.append(f"CREATE INDEX {name}\n    ON {table} ({', '.join(indexed)});")
    return "\n".join(parts) + "\n"
