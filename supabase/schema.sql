-- Cloud tables for MT5 Trading Workstation (spec E2). Run this file first in the Supabase
-- SQL editor, then rls.sql, views.sql and cleanup.sql. Every statement can run again safely.
--
-- The app keeps its own SQLite database as the source of truth and uploads rows with upserts
-- by id (client-generated UUIDs), so a retry never creates a duplicate. There are no foreign
-- keys: rows may arrive in any order after the app was offline. `user_id` is filled from the
-- signed-in user; row-level security (rls.sql) limits every user to their own rows.
--
-- Generated from app/storage/schema.py; tests/unit/test_storage_schema.py keeps both in step.

create table if not exists public.accounts (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    broker text,
    server text,
    login bigint,
    name text,
    type text,
    currency text,
    leverage bigint,
    margin_mode text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists accounts_user_id_idx
    on public.accounts (user_id);
create table if not exists public.sessions (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    app_version text,
    profile text,
    mode text,
    started_at timestamptz,
    ended_at timestamptz,
    settings_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists sessions_user_id_idx
    on public.sessions (user_id);
create table if not exists public.strategy_configs (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    strategy text,
    version text,
    params_json jsonb,
    params_hash text,
    created_by text,
    parent_config_id uuid,
    notes text,
    is_active boolean,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists strategy_configs_user_id_idx
    on public.strategy_configs (user_id);
create table if not exists public.signals (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    bar_time timestamptz,
    symbol text,
    tf text,
    strategy text,
    strategy_version text,
    config_id uuid,
    direction text,
    order_type text,
    entry double precision,
    sl double precision,
    tp double precision,
    rr double precision,
    spread double precision,
    atr double precision,
    win_probability double precision,
    prob_ci_low double precision,
    prob_ci_high double precision,
    probability_source text,
    expected_value double precision,
    model_version text,
    features_json jsonb,
    shap_top_json jsonb,
    reason text,
    state text,
    decision text,
    reject_reason text,
    trace_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists signals_user_id_idx
    on public.signals (user_id);
create table if not exists public.decision_traces (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    signal_id uuid,
    trace_id text,
    steps_json jsonb,
    final_decision text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists decision_traces_user_id_idx
    on public.decision_traces (user_id);
create table if not exists public.trades (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    signal_id uuid,
    mode text,
    source text,
    ticket bigint,
    position_id bigint,
    magic bigint,
    symbol text,
    direction text,
    volume double precision,
    requested_price double precision,
    open_price double precision,
    slippage double precision,
    open_time timestamptz,
    sl_initial double precision,
    tp_initial double precision,
    risk_money double precision,
    close_time timestamptz,
    close_price double precision,
    profit double precision,
    commission double precision,
    swap double precision,
    fee double precision,
    net_profit double precision,
    r_multiple double precision,
    outcome text,
    exit_reason text,
    duration_sec bigint,
    mfe_r double precision,
    mae_r double precision,
    predicted_probability double precision,
    session_label text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists trades_user_id_idx
    on public.trades (user_id);
create table if not exists public.trade_events (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    trade_id uuid,
    time timestamptz,
    type text,
    old_value text,
    new_value text,
    reason text,
    payload_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists trade_events_user_id_idx
    on public.trade_events (user_id);
create table if not exists public.mt5_requests (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    trace_id text,
    action text,
    request_json jsonb,
    retcode bigint,
    retcode_text text,
    result_json jsonb,
    last_error text,
    latency_ms double precision,
    attempt bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists mt5_requests_user_id_idx
    on public.mt5_requests (user_id);
create table if not exists public.account_snapshots (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    time timestamptz,
    balance double precision,
    equity double precision,
    margin double precision,
    free_margin double precision,
    margin_level double precision,
    open_positions bigint,
    open_risk double precision,
    daily_pnl double precision,
    drawdown_pct double precision,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists account_snapshots_user_id_idx
    on public.account_snapshots (user_id);
create table if not exists public.risk_events (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    time timestamptz,
    type text,
    details_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists risk_events_user_id_idx
    on public.risk_events (user_id);
create table if not exists public.model_versions (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    strategy text,
    features_json jsonb,
    schema_hash text,
    train_period text,
    symbols jsonb,
    metrics_json jsonb,
    file_hash text,
    is_active boolean,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists model_versions_user_id_idx
    on public.model_versions (user_id);
create table if not exists public.backtest_runs (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    config_id uuid,
    period text,
    costs_json jsonb,
    metrics_json jsonb,
    walk_forward_json jsonb,
    monte_carlo_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists backtest_runs_user_id_idx
    on public.backtest_runs (user_id);
create table if not exists public.journal (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    trade_id uuid,
    narrative text,
    snapshots jsonb,
    notes text,
    tags jsonb,
    rating bigint,
    emotion text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists journal_user_id_idx
    on public.journal (user_id);
create table if not exists public.audit_log (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    session_id text,
    time timestamptz,
    source text,
    action text,
    before_json jsonb,
    after_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists audit_log_user_id_idx
    on public.audit_log (user_id);
create table if not exists public.app_logs (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    session_id text,
    time timestamptz,
    level text,
    category text,
    module text,
    function text,
    line bigint,
    message text,
    trace_id text,
    signal_id text,
    trade_id text,
    symbol text,
    error_code text,
    exception_type text,
    stack_trace text,
    context_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists app_logs_user_id_idx
    on public.app_logs (user_id);
create table if not exists public.health_checks (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    time timestamptz,
    name text,
    status text,
    value double precision,
    details_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists health_checks_user_id_idx
    on public.health_checks (user_id);
create table if not exists public.performance_metrics (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    time timestamptz,
    name text,
    value double precision,
    unit text,
    details_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists performance_metrics_user_id_idx
    on public.performance_metrics (user_id);
create table if not exists public.daily_reports (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    report_date date,
    summary_json jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists daily_reports_user_id_idx
    on public.daily_reports (user_id);
create table if not exists public.calendar_events (
    id uuid primary key,
    user_id uuid not null default auth.uid(),
    account_id uuid,
    time timestamptz,
    currency text,
    impact text,
    title text,
    actual text,
    forecast text,
    previous text,
    source text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists calendar_events_user_id_idx
    on public.calendar_events (user_id);
create index if not exists sessions_account_id_started_at_idx
    on public.sessions (account_id, started_at);
create index if not exists strategy_configs_account_id_created_at_idx
    on public.strategy_configs (account_id, created_at);
create index if not exists signals_account_id_bar_time_idx
    on public.signals (account_id, bar_time);
create index if not exists decision_traces_account_id_created_at_idx
    on public.decision_traces (account_id, created_at);
create index if not exists trades_account_id_open_time_idx
    on public.trades (account_id, open_time);
create index if not exists trade_events_account_id_time_idx
    on public.trade_events (account_id, time);
create index if not exists mt5_requests_account_id_created_at_idx
    on public.mt5_requests (account_id, created_at);
create index if not exists account_snapshots_account_id_time_idx
    on public.account_snapshots (account_id, time);
create index if not exists risk_events_account_id_time_idx
    on public.risk_events (account_id, time);
create index if not exists model_versions_account_id_created_at_idx
    on public.model_versions (account_id, created_at);
create index if not exists backtest_runs_account_id_created_at_idx
    on public.backtest_runs (account_id, created_at);
create index if not exists journal_account_id_created_at_idx
    on public.journal (account_id, created_at);
create index if not exists audit_log_account_id_time_idx
    on public.audit_log (account_id, time);
create index if not exists app_logs_account_id_time_idx
    on public.app_logs (account_id, time);
create index if not exists health_checks_account_id_time_idx
    on public.health_checks (account_id, time);
create index if not exists performance_metrics_account_id_time_idx
    on public.performance_metrics (account_id, time);
create index if not exists daily_reports_account_id_report_date_idx
    on public.daily_reports (account_id, report_date);
create index if not exists calendar_events_account_id_time_idx
    on public.calendar_events (account_id, time);
create index if not exists accounts_server_login_idx
    on public.accounts (server, login);
create index if not exists signals_symbol_bar_time_idx
    on public.signals (symbol, bar_time);
create index if not exists signals_strategy_bar_time_idx
    on public.signals (strategy, bar_time);
create index if not exists trades_symbol_open_time_idx
    on public.trades (symbol, open_time);
create index if not exists trades_position_id_idx
    on public.trades (position_id);
create index if not exists trades_signal_id_idx
    on public.trades (signal_id);
create index if not exists trade_events_trade_id_idx
    on public.trade_events (trade_id);
create index if not exists decision_traces_signal_id_idx
    on public.decision_traces (signal_id);
create index if not exists strategy_configs_strategy_idx
    on public.strategy_configs (strategy);
create index if not exists model_versions_strategy_idx
    on public.model_versions (strategy);
create index if not exists journal_trade_id_idx
    on public.journal (trade_id);
create index if not exists calendar_events_currency_time_idx
    on public.calendar_events (currency, time);
