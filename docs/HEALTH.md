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
| Rows waiting to sync | <= 1,000 (n/a while cloud sync is off) |
| Start-up (launch to window) | < 5 s |

Over a budget is a WARNING in the `perf` log (INFO when back within), never a problem.
Saved to `performance_metrics`: changes at once, all every 15 minutes.

## Debug bundle

Health > Create debug bundle writes `<profile>/debug/debug-<UTC time>.zip` in the background:
`README_DEBUG.md` (with the AI prompt "Find the root cause of this problem."), versions,
health, performance, the last 50 decision traces, the settings files, the logs of the last
2 days (newest 8 MB per file, 24 MB in all) and the newest 20 crash reports. Everything is
masked again. Since 0.18.1 personal values become `***` in every file, log lines and crash
reports included: the account login and holder name, the Windows user name, Telegram chat
ids, and the `pin_hash` / `pin_salt` settings.

## Go-Live

The gate's "Health checks now" item passes only when the last health check was all green.
The "Errors and health" item counts CRITICAL log lines and unresolved CRITICAL health checks
(not followed by an OK of the same check) in the last 7 days; warnings and n/a checks do
not count there (fixed in 0.18.1).

## Checks

See the table in the 0.17.0 notes: MT5 connected, Algo Trading, quotes fresh, broker offset,
Supabase, sync queue, disk space, log size, latency, PC clock, background workers. While
cloud sync is off the sync queue shows n/a: the rows wait on this PC (0.18.1).

## Fixed in 0.18.1 (from the first real debug bundle)

- Personal values in the bundle (see above).
- No sync-queue warning while cloud sync is off (check and metric).
- Pressing Connect while connected no longer sends a "MT5 disconnected" alert.
- The Go-Live 7-day item no longer counts warnings and n/a checks.

## Check it on the PC (0.18.1)

1. Health: Sync queue and Rows waiting to sync show n/a with cloud sync off.
2. Create debug bundle: search the zip for your login and name; both are `***`.
3. Connection: press Connect while connected; no "MT5 disconnected" toast or Telegram.

## Next (14c)

The 24-hour soak test on demo with a report, and the full Logs page check.
