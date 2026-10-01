-- Reporting views (spec E2). Run after schema.sql and rls.sql.
-- `security_invoker = true` makes every view apply the row-level security of the user who
-- reads it, so a view never shows another user's rows.

create or replace view public.v_trade_full
with (security_invoker = true) as
select
    t.id,
    t.user_id,
    t.account_id,
    a.broker,
    a.server,
    a.login,
    a.type as account_type,
    a.currency,
    t.mode,
    t.source,
    t.ticket,
    t.position_id,
    t.magic,
    t.symbol,
    t.direction,
    t.volume,
    t.requested_price,
    t.open_price,
    t.slippage,
    t.open_time,
    t.sl_initial,
    t.tp_initial,
    t.risk_money,
    t.close_time,
    t.close_price,
    t.profit,
    t.commission,
    t.swap,
    t.fee,
    t.net_profit,
    t.r_multiple,
    t.outcome,
    t.exit_reason,
    t.duration_sec,
    t.mfe_r,
    t.mae_r,
    t.predicted_probability,
    t.session_label,
    t.signal_id,
    s.strategy,
    s.strategy_version,
    s.config_id,
    s.tf,
    s.win_probability,
    s.probability_source,
    s.expected_value,
    s.reason as signal_reason,
    s.trace_id,
    d.final_decision,
    d.steps_json as decision_steps
from public.trades t
left join public.accounts a on a.id = t.account_id
left join public.signals s on s.id = t.signal_id
left join lateral (
    select dt.final_decision, dt.steps_json
    from public.decision_traces dt
    where dt.signal_id = t.signal_id
    order by dt.created_at desc
    limit 1
) d on true;

create or replace view public.v_daily_performance
with (security_invoker = true) as
select
    t.user_id,
    t.account_id,
    t.mode,
    (t.close_time at time zone 'utc')::date as trade_date,
    count(*) as trades,
    count(*) filter (where t.net_profit > 0) as wins,
    count(*) filter (where t.net_profit < 0) as losses,
    round((count(*) filter (where t.net_profit > 0))::numeric / nullif(count(*), 0), 4)
        as win_rate,
    sum(t.net_profit) as net_profit,
    coalesce(sum(t.net_profit) filter (where t.net_profit > 0), 0) as gross_profit,
    coalesce(sum(t.net_profit) filter (where t.net_profit < 0), 0) as gross_loss,
    sum(coalesce(t.commission, 0) + coalesce(t.swap, 0) + coalesce(t.fee, 0)) as costs,
    avg(t.r_multiple) as avg_r
from public.trades t
where t.close_time is not null
group by t.user_id, t.account_id, t.mode, (t.close_time at time zone 'utc')::date;

-- Calibration check: how often trades won per bucket of the predicted probability (5 points).
create or replace view public.v_performance_by_bucket
with (security_invoker = true) as
select
    t.user_id,
    t.account_id,
    s.strategy,
    floor(t.predicted_probability * 20) / 20 as probability_bucket,
    count(*) as trades,
    avg(t.predicted_probability) as avg_predicted,
    avg(case when t.net_profit > 0 then 1.0 else 0.0 end) as win_rate,
    avg(t.r_multiple) as avg_r,
    sum(t.net_profit) as net_profit
from public.trades t
left join public.signals s on s.id = t.signal_id
where t.close_time is not null and t.predicted_probability is not null
group by t.user_id, t.account_id, s.strategy, floor(t.predicted_probability * 20) / 20;

create or replace view public.v_strategy_config_compare
with (security_invoker = true) as
select
    c.user_id,
    c.id as config_id,
    c.strategy,
    c.version,
    c.created_by,
    c.parent_config_id,
    c.is_active,
    c.params_hash,
    count(t.id) as trades,
    count(t.id) filter (where t.net_profit > 0) as wins,
    round((count(t.id) filter (where t.net_profit > 0))::numeric / nullif(count(t.id), 0), 4)
        as win_rate,
    coalesce(sum(t.net_profit), 0) as net_profit,
    avg(t.r_multiple) as avg_r,
    sum(t.net_profit) filter (where t.net_profit > 0)
        / nullif(-sum(t.net_profit) filter (where t.net_profit < 0), 0) as profit_factor
from public.strategy_configs c
left join public.signals s on s.config_id = c.id
left join public.trades t on t.signal_id = s.id and t.close_time is not null
group by
    c.user_id,
    c.id,
    c.strategy,
    c.version,
    c.created_by,
    c.parent_config_id,
    c.is_active,
    c.params_hash;
