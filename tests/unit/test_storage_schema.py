"""The local schema, `app/storage/schema.py` and `supabase/*.sql` must agree."""

import re
from pathlib import Path

from app.storage.migrations import MIGRATIONS
from app.storage.schema import KINDS, TABLES, indexes, sqlite_ddl, to_local, to_remote
from tests.unit.storage_helpers import temporary_store

ROOT = Path(__file__).resolve().parents[2]
SUPABASE = ROOT / "supabase"
CREATE = re.compile(r"create table if not exists public\.(\w+) \(\n(.*?)\n\);", re.DOTALL)
# Migration 1 is frozen: its checksum is stored in every database that was ever created.
MIGRATION_1_SHA256 = "f59d9a11501562a232e7b39a6c958590a3d69ae73a14c92879312984eba8abff"


def remote_tables() -> dict[str, list[tuple[str, str]]]:
    text = (SUPABASE / "schema.sql").read_text(encoding="utf-8")
    found: dict[str, list[tuple[str, str]]] = {}
    for table, body in CREATE.findall(text):
        columns = []
        for line in body.splitlines():
            name, rest = line.strip().rstrip(",").split(" ", 1)
            sql_type = rest.split(" not null")[0].split(" primary key")[0].split(" default")[0]
            columns.append((name, sql_type))
        found[table] = columns
    return found


def test_the_local_tables_have_exactly_the_schema_columns() -> None:
    with temporary_store() as store:
        for table, columns in TABLES.items():
            rows = store.db.query(f"PRAGMA table_info({table})")
            local = [(str(row["name"]), str(row["type"])) for row in rows]
            assert local == [(name, KINDS[kind][0]) for name, kind in columns.items()], table


def test_migration_1_is_frozen_and_holds_the_schema_it_was_generated_from() -> None:
    assert MIGRATIONS[0].checksum == MIGRATION_1_SHA256
    assert sqlite_ddl() in MIGRATIONS[0].sql


def test_the_cloud_tables_match_the_schema_plus_the_user_id() -> None:
    remote = remote_tables()
    assert list(remote) == list(TABLES)
    for table, columns in TABLES.items():
        expected = [(name, KINDS[kind][1]) for name, kind in columns.items()]
        expected.insert(1, ("user_id", "uuid"))
        assert remote[table] == expected, table


def test_every_cloud_table_has_row_level_security_for_its_owner() -> None:
    text = (SUPABASE / "rls.sql").read_text(encoding="utf-8")
    for table in TABLES:
        assert f"alter table public.{table} enable row level security;" in text
        policy = f'create policy "own rows" on public.{table}'
        assert policy in text
    assert text.count("using (user_id = (select auth.uid()))") == len(TABLES)
    assert text.count("with check (user_id = (select auth.uid()))") == len(TABLES)


def test_the_views_exist_and_run_with_the_reader_s_permissions() -> None:
    text = (SUPABASE / "views.sql").read_text(encoding="utf-8")
    pattern = r"create or replace view public\.(\w+)\nwith \(security_invoker = true\)"
    views = re.findall(pattern, text)
    expected = ["v_trade_full", "v_daily_performance", "v_performance_by_bucket"]
    assert views == [*expected, "v_strategy_config_compare"]


def test_every_index_exists_locally_and_in_the_cloud() -> None:
    schema_text = (SUPABASE / "schema.sql").read_text(encoding="utf-8")
    with temporary_store() as store:
        rows = store.db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
        local = {str(row["name"]) for row in rows}
    for table, columns in indexes():
        name = f"{table}_{'_'.join(columns)}_idx"
        assert name in local
        assert f"create index if not exists {name}\n    on public.{table}" in schema_text


def test_the_cleanup_never_touches_trades_or_signals() -> None:
    text = (SUPABASE / "cleanup.sql").read_text(encoding="utf-8").lower()
    for table in ("trades", "signals", "decision_traces", "trade_events", "journal", "audit_log"):
        assert f"delete from public.{table} " not in text


def test_values_round_trip_between_python_sqlite_and_the_cloud() -> None:
    row = {"id": "x", "is_active": True, "params_json": {"b": 2, "a": [1]}, "version": 3}
    local = to_local("strategy_configs", row)
    assert local == {"id": "x", "is_active": 1, "params_json": '{"a":[1],"b":2}', "version": "3"}
    remote = to_remote("strategy_configs", local)
    assert remote["is_active"] is True and remote["params_json"] == {"a": [1], "b": 2}
    assert remote["notes"] is None and set(remote) == set(TABLES["strategy_configs"])
