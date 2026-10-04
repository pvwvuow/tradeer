# Health (Phase 14)

Spec E3, D4 and F3 page 13. Advanced > System > Health. Checks since version 0.17.0;
performance metrics, the debug bundle and the Go-Live health item since 0.18.0.

## Performance metrics (with every check)

| Metric | Budget |
|---|---|
| CPU (this process, average of 5 minutes) | < 3 % of the PC when idle |
| Memory (working set, ctypes on Windows) | < 500 MB |
| Closed-bar processing p50 / p95 | < 1 s per 10 symbols |
| MT5 call p50 / p95 | < 1,000 ms |
| MT5 requests waiting | <= 20 |
| Rows waiting to sync | <= 1,000 |
| Start-up (launch to window) | < 5 s |

Over a budget is a WARNING in the `perf` log (INFO when back within), never a problem.
Saved to `performance_metrics`: changes at once, all every 15 minutes.

## Debug bundle

Health > Create debug bundle writes `<profile>/debug/debug-<UTC time>.zip` in the background:
`README_DEBUG.md` (with the AI prompt "Find the root cause of this problem."), versions,
health, performance, the last 50 decision traces, the settings files, the logs of the last
2 days (newest 8 MB per file, 24 MB in all) and the newest 20 crash reports. Everything is
masked again; account logins become `***`.

## Go-Live

The gate's "Health checks now" item passes only when the last health check was all green.

## Checks

See the table in the 0.17.0 notes: MT5 connected, Algo Trading, quotes fresh, broker offset,
Supabase, sync queue, disk space, log size, latency, PC clock, background workers.

## Check it on the PC (0.18.0)

1. Health: after about 30 seconds the Performance table fills; Memory well under 500 MB.
2. Create debug bundle, then Open folder: the zip is there; README_DEBUG.md shows no secret.
3. Strategies > Go-Live: the checklist has "Health checks now".

## Next (14c)

The 24-hour soak test on demo with a report, and the full Logs page check.
