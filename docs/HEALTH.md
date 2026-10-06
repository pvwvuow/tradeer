# Health (Phase 14)

Spec E3, D4 and F3 pages 12 and 13. Advanced > System > Health and Logs. Checks since
version 0.17.0; performance metrics, the debug bundle and the Go-Live health item since
0.18.0; the soak test report and the full Logs page since 0.19.0; the demo test since 0.23.0.

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
5. Start the run by Thursday morning so all 24 hours fall before the market closes on Friday.

## Demo test (0.23.0)

Health > Demo test places real orders on the DEMO account through the bot's own order code
(`LiveBroker` on the MT5 gateway thread), so each step proves one thing the bot needs. It
refuses a real or read-only account and stops at once when Algo Trading is off or MT5 blocks
the Python API. Pick a symbol or All watched symbols and press Run demo test; Stop ends it
after the running step. About 2 to 4 minutes per symbol.

| Step | What it proves |
|---|---|
| MT5 and the demo account | DEMO, Algo Trading on, trading from Python allowed |
| Symbol and price | broker name, stops and freeze level, lots, filling and expiry modes |
| Lot size and margin | MT5 works out the loss at the stop and the margin |
| Market buy with SL and TP | an order opens with its SL and TP in place |
| Move the stop loss | breakeven and the trailing stop can move the SL |
| Close part of the position | partial take-profit |
| Close the position | a full close |
| Closed trade in the history | the journal finds the deals |
| Market sell, then close | the short side |
| Pending order with an expiry | the London breakout's orders with an expiry time |
| Pending orders: buy and sell, limit and stop | all four pending types with SL and TP |
| Cancel pending orders | cancelling a breakout's other side or an expired signal |
| Breakout pair | one side fills, the other is cancelled (skipped if the price stays still 90 s) |
| Pending order expires by itself | MT5 removes an expired order without the app |
| Refused order is not sent again | a bad order is refused once, never retried |
| Close everything, like the kill switch | everything closes at once |

Minimum lot, magic number 26070098 (no strategy uses it, so the engine never manages these
trades; the journal lists them as external), comment `tw-demo-test`. At the end, also after a
failure or Stop, everything of that magic number is closed. A closed market (10018) skips the
order steps. The report is saved as `<profile>/reports/demo-test-<UTC time>.md` (no login,
no name) and every step goes to the `execution` log.

The first run on the PC (5 October 2026, 0.23.0) failed every order step: MT5 answered
nothing with the last error "-2 Unnamed arguments not allowed". Since 0.23.1 the MT5 helper
asks again with the request's fields named (MT5 refuses such a call before it reaches the
trade server, so nothing is sent twice). Run the demo test again after updating.
