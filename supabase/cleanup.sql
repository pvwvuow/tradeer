-- Clean-up of old low-level rows in the cloud (spec E1). Run after schema.sql.
-- Trades, signals, decision traces, trade events, journal and the audit log are never deleted.
-- Performance metrics older than `metric_days` are first aggregated into one row per day and
-- name (marked "aggregated" in details_json), then the raw rows are removed.
--
-- Run it by hand:      select * from public.cleanup_old_rows();
-- Or schedule it daily (Database > Extensions: enable pg_cron), once:
--   select cron.schedule('tradeer-cleanup', '30 3 * * *', 'select public.cleanup_old_rows()');

create or replace function public.cleanup_old_rows(
    log_days integer default 90,
    metric_days integer default 30
)
returns table (table_name text, deleted bigint)
language plpgsql
security invoker
set search_path = public
as $$
declare
    removed bigint;
    log_cutoff timestamptz := now() - make_interval(days => log_days);
    metric_cutoff timestamptz := now() - make_interval(days => metric_days);
begin
    delete from public.app_logs where time < log_cutoff;
    get diagnostics removed = row_count;
    table_name := 'app_logs';
    deleted := removed;
    return next;

    delete from public.mt5_requests where created_at < log_cutoff;
    get diagnostics removed = row_count;
    table_name := 'mt5_requests';
    deleted := removed;
    return next;

    delete from public.health_checks where time < metric_cutoff;
    get diagnostics removed = row_count;
    table_name := 'health_checks';
    deleted := removed;
    return next;

    insert into public.performance_metrics (
        id, user_id, account_id, time, name, value, unit, details_json
    )
    select
        gen_random_uuid(),
        pm.user_id,
        pm.account_id,
        date_trunc('day', pm.time),
        pm.name,
        avg(pm.value),
        max(pm.unit),
        jsonb_build_object(
            'aggregated', true,
            'samples', count(*),
            'min', min(pm.value),
            'max', max(pm.value)
        )
    from public.performance_metrics pm
    where pm.time < metric_cutoff
        and coalesce(pm.details_json ->> 'aggregated', 'false') <> 'true'
    group by pm.user_id, pm.account_id, date_trunc('day', pm.time), pm.name;

    delete from public.performance_metrics pm
    where pm.time < metric_cutoff
        and coalesce(pm.details_json ->> 'aggregated', 'false') <> 'true';
    get diagnostics removed = row_count;
    table_name := 'performance_metrics';
    deleted := removed;
    return next;
end;
$$;
