# Health (Phase 14)

Spec E3, D4 and F3 pages 12 and 13. Advanced > System > Health and Logs. Checks since
version 0.17.0; performance metrics, the debug bundle and the Go-Live health item since
0.18.0; the soak test report and the full Logs page since 0.19.0.

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

## Soak test (24 hours)

Leave the app running on a demo account for at least 24 hours on market days (bars must
close, so not over a weekend), then press Health > Create soak report. The report finds the
latest continuous run in the saved metrics (a gap of more than 35 minutes ends a run; a
restart for an update is no gap) and checks it:

| Check | Needed |
|---|---|
| Duration | >= 24 hours |
| Memory | < 500 MB at every sample |
| No memory leak | < 50 MB per day growth after the first hour (least squares) |
| CPU | < 3 % on average |
| MT5 calls | p95 < 1,000 ms at every sample |
| Bar processing | never over its budget |
| Stability | no CRITICAL log line, no crash report, no frozen worker |

ERROR lines and lost MT5 connections are listed as notes. The report is saved as
`<profile>/reports/soak-<UTC time>.md`; Open folder shows it.

## Logs page

Category tabs (All and the 16 categories), the live tail, filters for level, text (or
regex), symbol, strategy and time range, the JSON detail of a line, the level of each
category, debug mode for 30 minutes, Export and Open log folder.

- **This session** shows the live lines kept in memory. **Last 15 minutes** to **Last 30
  days** also read the saved files, zipped ones too, in a background thread, and merge them
  with the live lines (at most 2,000 lines, the newest).
- **Show only this trace** isolates one trace id across the session and every saved file
  and shows its timeline (each line with the time since the first) in the detail pane.
- **Export** saves the lines shown as `<profile>/exports/logs-<UTC time>.jsonl` and `.csv`.
  The lines were masked when they were written.

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

## Check it on the PC (0.19.0)

1. Logs: click a few category tabs; type a symbol; choose Last 24 hours and see older lines.
2. Logs: select a line with a trace id, Show only this trace: the timeline appears below.
3. Logs: Export, then Open log folder > `..\exports`: the `.csv` opens in Excel.
4. Monday to Friday: leave the app on demo for 24 hours, then Health > Create soak report.
