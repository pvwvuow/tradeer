"""Migration 1: the synced tables of spec E2, the outbox, the sync state and the MT5 history.

Frozen: never edit it after a release (its checksum is stored in every database). The synced
tables were generated from `app.storage.schema`; later changes need a new migration.
"""

SQL = """
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    broker TEXT,
    server TEXT,
    login INTEGER,
    name TEXT,
    type TEXT,
    currency TEXT,
    leverage INTEGER,
    margin_mode TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    app_version TEXT,
    profile TEXT,
    mode TEXT,
    started_at TEXT,
    ended_at TEXT,
    settings_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE strategy_configs (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    strategy TEXT,
    version TEXT,
    params_json TEXT,
    params_hash TEXT,
    created_by TEXT,
    parent_config_id TEXT,
    notes TEXT,
    is_active INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE signals (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    bar_time TEXT,
    symbol TEXT,
    tf TEXT,
    strategy TEXT,
    strategy_version TEXT,
    config_id TEXT,
    direction TEXT,
    order_type TEXT,
    entry REAL,
    sl REAL,
    tp REAL,
    rr REAL,
    spread REAL,
    atr REAL,
    win_probability REAL,
    prob_ci_low REAL,
    prob_ci_high REAL,
    probability_source TEXT,
    expected_value REAL,
    model_version TEXT,
    features_json TEXT,
    shap_top_json TEXT,
    reason TEXT,
    state TEXT,
    decision TEXT,
    reject_reason TEXT,
    trace_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE decision_traces (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    signal_id TEXT,
    trace_id TEXT,
    steps_json TEXT,
    final_decision TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    signal_id TEXT,
    mode TEXT,
    source TEXT,
    ticket INTEGER,
    position_id INTEGER,
    magic INTEGER,
    symbol TEXT,
    direction TEXT,
    volume REAL,
    requested_price REAL,
    open_price REAL,
    slippage REAL,
    open_time TEXT,
    sl_initial REAL,
    tp_initial REAL,
    risk_money REAL,
    close_time TEXT,
    close_price REAL,
    profit REAL,
    commission REAL,
    swap REAL,
    fee REAL,
    net_profit REAL,
    r_multiple REAL,
    outcome TEXT,
    exit_reason TEXT,
    duration_sec INTEGER,
    mfe_r REAL,
    mae_r REAL,
    predicted_probability REAL,
    session_label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE trade_events (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_id TEXT,
    time TEXT,
    type TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE mt5_requests (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    trace_id TEXT,
    action TEXT,
    request_json TEXT,
    retcode INTEGER,
    retcode_text TEXT,
    result_json TEXT,
    last_error TEXT,
    latency_ms REAL,
    attempt INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE account_snapshots (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    time TEXT,
    balance REAL,
    equity REAL,
    margin REAL,
    free_margin REAL,
    margin_level REAL,
    open_positions INTEGER,
    open_risk REAL,
    daily_pnl REAL,
    drawdown_pct REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE risk_events (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    time TEXT,
    type TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE model_versions (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    strategy TEXT,
    features_json TEXT,
    schema_hash TEXT,
    train_period TEXT,
    symbols TEXT,
    metrics_json TEXT,
    file_hash TEXT,
    is_active INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE backtest_runs (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    config_id TEXT,
    period TEXT,
    costs_json TEXT,
    metrics_json TEXT,
    walk_forward_json TEXT,
    monte_carlo_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE journal (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_id TEXT,
    narrative TEXT,
    snapshots TEXT,
    notes TEXT,
    tags TEXT,
    rating INTEGER,
    emotion TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    session_id TEXT,
    time TEXT,
    source TEXT,
    action TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE app_logs (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    session_id TEXT,
    time TEXT,
    level TEXT,
    category TEXT,
    module TEXT,
    function TEXT,
    line INTEGER,
    message TEXT,
    trace_id TEXT,
    signal_id TEXT,
    trade_id TEXT,
    symbol TEXT,
    error_code TEXT,
    exception_type TEXT,
    stack_trace TEXT,
    context_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE health_checks (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    time TEXT,
    name TEXT,
    status TEXT,
    value REAL,
    details_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE performance_metrics (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    time TEXT,
    name TEXT,
    value REAL,
    unit TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE daily_reports (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    report_date TEXT,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE calendar_events (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    time TEXT,
    currency TEXT,
    impact TEXT,
    title TEXT,
    actual TEXT,
    forecast TEXT,
    previous TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX sessions_account_id_started_at_idx
    ON sessions (account_id, started_at);
CREATE INDEX strategy_configs_account_id_created_at_idx
    ON strategy_configs (account_id, created_at);
CREATE INDEX signals_account_id_bar_time_idx
    ON signals (account_id, bar_time);
CREATE INDEX decision_traces_account_id_created_at_idx
    ON decision_traces (account_id, created_at);
CREATE INDEX trades_account_id_open_time_idx
    ON trades (account_id, open_time);
CREATE INDEX trade_events_account_id_time_idx
    ON trade_events (account_id, time);
CREATE INDEX mt5_requests_account_id_created_at_idx
    ON mt5_requests (account_id, created_at);
CREATE INDEX account_snapshots_account_id_time_idx
    ON account_snapshots (account_id, time);
CREATE INDEX risk_events_account_id_time_idx
    ON risk_events (account_id, time);
CREATE INDEX model_versions_account_id_created_at_idx
    ON model_versions (account_id, created_at);
CREATE INDEX backtest_runs_account_id_created_at_idx
    ON backtest_runs (account_id, created_at);
CREATE INDEX journal_account_id_created_at_idx
    ON journal (account_id, created_at);
CREATE INDEX audit_log_account_id_time_idx
    ON audit_log (account_id, time);
CREATE INDEX app_logs_account_id_time_idx
    ON app_logs (account_id, time);
CREATE INDEX health_checks_account_id_time_idx
    ON health_checks (account_id, time);
CREATE INDEX performance_metrics_account_id_time_idx
    ON performance_metrics (account_id, time);
CREATE INDEX daily_reports_account_id_report_date_idx
    ON daily_reports (account_id, report_date);
CREATE INDEX calendar_events_account_id_time_idx
    ON calendar_events (account_id, time);
CREATE INDEX accounts_server_login_idx
    ON accounts (server, login);
CREATE INDEX signals_symbol_bar_time_idx
    ON signals (symbol, bar_time);
CREATE INDEX signals_strategy_bar_time_idx
    ON signals (strategy, bar_time);
CREATE INDEX trades_symbol_open_time_idx
    ON trades (symbol, open_time);
CREATE INDEX trades_position_id_idx
    ON trades (position_id);
CREATE INDEX trades_signal_id_idx
    ON trades (signal_id);
CREATE INDEX trade_events_trade_id_idx
    ON trade_events (trade_id);
CREATE INDEX decision_traces_signal_id_idx
    ON decision_traces (signal_id);
CREATE INDEX strategy_configs_strategy_idx
    ON strategy_configs (strategy);
CREATE INDEX model_versions_strategy_idx
    ON model_versions (strategy);
CREATE INDEX journal_trade_id_idx
    ON journal (trade_id);
CREATE INDEX calendar_events_currency_time_idx
    ON calendar_events (currency, time);
CREATE TABLE outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    row_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (table_name, row_id)
);
CREATE INDEX outbox_state_id_idx
    ON outbox (state, id);
CREATE TABLE sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE mt5_deals (
    account_id TEXT NOT NULL,
    ticket INTEGER NOT NULL,
    order_ticket INTEGER,
    time INTEGER NOT NULL,
    time_msc INTEGER,
    type INTEGER NOT NULL,
    entry INTEGER,
    magic INTEGER,
    position_id INTEGER,
    reason INTEGER,
    volume REAL,
    price REAL,
    commission REAL,
    swap REAL,
    profit REAL,
    fee REAL,
    symbol TEXT,
    comment TEXT,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (account_id, ticket)
);
CREATE INDEX mt5_deals_account_id_position_id_idx
    ON mt5_deals (account_id, position_id);
CREATE INDEX mt5_deals_account_id_time_idx
    ON mt5_deals (account_id, time);
CREATE TABLE mt5_orders (
    account_id TEXT NOT NULL,
    ticket INTEGER NOT NULL,
    time_setup INTEGER,
    time_done INTEGER,
    type INTEGER,
    state INTEGER,
    magic INTEGER,
    position_id INTEGER,
    volume_initial REAL,
    volume_current REAL,
    price_open REAL,
    sl REAL,
    tp REAL,
    price_current REAL,
    symbol TEXT,
    comment TEXT,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (account_id, ticket)
);
CREATE INDEX mt5_orders_account_id_position_id_idx
    ON mt5_orders (account_id, position_id);
"""
