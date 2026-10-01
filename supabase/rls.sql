-- Row-level security (spec D5): every table only shows and accepts the signed-in user's
-- rows. The app uses the anon key and the user's sign-in token, never the service key.
-- Run after schema.sql. Every statement can run again safely.

alter table public.accounts enable row level security;
drop policy if exists "own rows" on public.accounts;
create policy "own rows" on public.accounts
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.sessions enable row level security;
drop policy if exists "own rows" on public.sessions;
create policy "own rows" on public.sessions
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.strategy_configs enable row level security;
drop policy if exists "own rows" on public.strategy_configs;
create policy "own rows" on public.strategy_configs
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.signals enable row level security;
drop policy if exists "own rows" on public.signals;
create policy "own rows" on public.signals
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.decision_traces enable row level security;
drop policy if exists "own rows" on public.decision_traces;
create policy "own rows" on public.decision_traces
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.trades enable row level security;
drop policy if exists "own rows" on public.trades;
create policy "own rows" on public.trades
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.trade_events enable row level security;
drop policy if exists "own rows" on public.trade_events;
create policy "own rows" on public.trade_events
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.mt5_requests enable row level security;
drop policy if exists "own rows" on public.mt5_requests;
create policy "own rows" on public.mt5_requests
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.account_snapshots enable row level security;
drop policy if exists "own rows" on public.account_snapshots;
create policy "own rows" on public.account_snapshots
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.risk_events enable row level security;
drop policy if exists "own rows" on public.risk_events;
create policy "own rows" on public.risk_events
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.model_versions enable row level security;
drop policy if exists "own rows" on public.model_versions;
create policy "own rows" on public.model_versions
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.backtest_runs enable row level security;
drop policy if exists "own rows" on public.backtest_runs;
create policy "own rows" on public.backtest_runs
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.journal enable row level security;
drop policy if exists "own rows" on public.journal;
create policy "own rows" on public.journal
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.audit_log enable row level security;
drop policy if exists "own rows" on public.audit_log;
create policy "own rows" on public.audit_log
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.app_logs enable row level security;
drop policy if exists "own rows" on public.app_logs;
create policy "own rows" on public.app_logs
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.health_checks enable row level security;
drop policy if exists "own rows" on public.health_checks;
create policy "own rows" on public.health_checks
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.performance_metrics enable row level security;
drop policy if exists "own rows" on public.performance_metrics;
create policy "own rows" on public.performance_metrics
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.daily_reports enable row level security;
drop policy if exists "own rows" on public.daily_reports;
create policy "own rows" on public.daily_reports
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
alter table public.calendar_events enable row level security;
drop policy if exists "own rows" on public.calendar_events;
create policy "own rows" on public.calendar_events
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
