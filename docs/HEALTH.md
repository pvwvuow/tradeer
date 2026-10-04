# Health (Phase 14a)

Spec E3 and F3 page 13. Advanced > System > Health.

## The checks (every minute)

| Check | OK | Warning | Problem |
|---|---|---|---|
| MT5 connected | connected | not connected, connecting, failed | reconnecting after a loss |
| Algo Trading on | the MT5 button is on | button off, or API trading disabled | |
| Quotes fresh | newest price <= 5 min old (or the FX weekend) | > 5 min | > 15 min |
| Broker offset stable | measured, no jump | a jump in the last 24 h | |
| Supabase reachable | uploading or up to date | offline, paused, signed out, setup needed, error | |
| Sync queue | <= 1,000 rows waiting, none refused | > 1,000 waiting or any refused | > 10,000 |
| Disk space | >= 5 GB free | < 5 GB | < 1 GB |
| Log size | <= 1 GB | > 1 GB | |
| Latency to the broker | MT5 ping <= 250 ms | > 250 ms | > 1,000 ms |
| PC clock | no tick ahead of this PC | a tick > 30 s in the future (PC clock behind) | > 120 s |
| Background workers | all send heartbeats | | a worker stopped answering |

"n/a" means the check cannot be measured right now (no connection, cloud sync off, no prices
yet); it is never counted as a problem.

The first run is 30 seconds after the start, so the automatic MT5 connection can finish.
"Check now" asks the monitor's thread to run at once; the page itself never calls MT5.

## Logs and storage

- A check that turns Warning is logged as WARNING, a Problem as ERROR (which also shows a
  Windows notification), and the way back to OK as INFO.
- Saved to the synced `health_checks` table: a check whenever its status changes, and all
  checks every 15 minutes (not every minute: that would be 14,000 rows a day).
- The page lists the recent warnings and problems from that table.

## Code

- `app/observability/health.py`: `HealthInputs`, the rules, `evaluate`, `overall`,
  `summary`, `HealthRecorder`.
- `app/engine/health_monitor.py`: `HealthMonitor` (thread `health`, watchdog heartbeat),
  `folder_size`.
- `app/storage/health_store.py`: `HealthRepository`.
- `app/ui/health_page.py`: `HealthPage`, `HealthContext`.
- `app/main.py`: `_health_context` collects the inputs from the connection, the market
  watch, the broker clock, the sync engine, the disk, the log folder and the watchdog.
- Tests: `tests/unit/test_health.py`, `tests/unit/test_health_monitor.py`,
  `tests/unit/test_health_store.py`, `tests/ui/test_health_page.py`.

## Next (14b, 14c)

Performance metrics (CPU, RAM, latencies against the D4 budgets), the debug bundle, the
Go-Live gate's "all health checks green" item, and the 24-hour soak test.
