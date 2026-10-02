# Progress

Continue in a new chat with: "Read docs/SPEC.md and docs/PROGRESS.md and continue."

## Completed phases

- Phase 1: Foundation, merged into `main` through pull request #1 on 2026-10-01.
- Phase 2: Observability, pull request #10, CI green, waiting for review.
- Phase 3: MT5 connection, pull request #13, CI green, waiting for review.
- Phase 4: Storage, pull request #14, CI green, waiting for review.
- Phase 5: Market data & analysis, pull request #15, updated with the fixes from the first real run, waiting for review.

## Phase 1: Foundation (pull request #1, merged)

Built: Python 3.11 project with exact pins, ruff, mypy (strict), pytest, pytest-qt, pre-commit; the desktop shell (Simple view by default, Advanced view with the grouped sidebar and 14 pages that name their phase); design tokens with a generated stylesheet, dark and light theme; status bar with the `PAPER` badge and the kill switch (inactive); command palette (Ctrl+K); `--self-check`, `--report-file`, `--profile`, `--version`; build, release and installer scripts; agent rules; the full specification in `docs/SPEC.md`.

Follow-up: the full pipeline (mypy, frozen self-check and crash test, installer, CodeQL, release-please) was enabled in `.github/workflows/` by the CI maintainer through pull request #11.

## Current phase: 5 Market data & analysis (branch `phase/05-market-analysis`, pull request #15)

Status: pull request #15 into `phase/04-storage`, waiting for review. Updated after the first run on a real PC (see below).

### Found in your first real run (fixed)

Your log (FIBO Group demo, 2 October 2026) showed the connection, the history import (11 deals, 5 trades), the broker time (UTC+2/+3) and all 3 cards working. It also showed 4 problems:

1. **The window froze for 12 s and 16 s** while MT5 answered slowly (connecting took 11.4 s, the first bar download 15.7 s). Even the watchdog stopped, so the MetaTrader5 package blocks the whole Python process while it waits. Fix: the package runs in its own helper process (`app/mt5/terminal_process.py`). A call that hangs for 120 s restarts the helper and the app logs in again; a heartbeat stuck behind a long request is postponed, not reported as a lost connection.
2. **95% of the log was one Qt warning** (`QFont::setPointSize: Point size <= 0 (-1)`, 956 times). Fix: font sizes in pt instead of px, and a repeated message is logged once a minute with a count.
3. **GBPUSD and XAUUSD always said "wait".** MT5 stores the minimum spread of each bar, and the check compared the live spread with those minimums. Fix: the last closed M5 bar's spread is compared with the typical bar spread of this hour. The log line of each card now also says why.
4. **Trade times were saved in server time**, because no fresh price had arrived when the history import ran ("offset unknown (market closed)" although the market was open). Fix: the import uses the broker clock the analysis measured or saved, and runs again by itself once the clock is measured.

### What was built

- Closed bars only: `copy_rates_from_pos(symbol, tf, 1, n)` for M5, M15, H1, H4 and D1, cached per symbol and timeframe, with only the new bars read after the first download (`app/mt5/market_data.py`).
- One cheap MT5 request every 2 s reads each watched symbol's price and newest closed M5 bar. A symbol is analysed again only when a new M5 bar has closed (`app/engine/market_watch.py`, thread `market-analysis`, watched by the watchdog).
- Broker time: the offset to UTC is measured from fresh ticks, with US or EU summer-time rules written out in code (Windows has no tz database). Everything is stored in UTC; the trading day is the broker day (`app/core/clock.py`). The measured clock is saved per server.
- Data checks with logging: missing bars, zero-volume bars, spikes over 10x ATR, stale prices, symbols not trading, late bars and broker time jumps. Weekend gaps are normal. Errors skip the evaluation and the card says "data problem".
- All C3 modules in `app/analysis/` (pure, numpy only): indicators, multi-timeframe trend matrix with a bias of -100 to +100 and reasons, market structure (confirmed swings, HH/HL/LH/LL, BOS and CHoCH, no look-ahead), key levels (swing clusters, previous day and week, session highs and lows, round numbers, distance in ATR), volatility (ATR percentile over 100 days, ADR used today, regime), sessions and the session clock, correlation matrix, currency strength meter, spread monitor, candle patterns (information only) and a plain-language analysis card per symbol.
- Economic calendar: manual entry, CSV import, and `CalendarExporter.mq5` (full source, an MT5 service) that writes the MT5 calendar to `Common/Files/tradeer_calendar.csv` every 5 minutes in UTC. The app reads the file every 3 minutes when it changed and saves the events in `calendar_events`. High-impact news within an hour means "wait" on the card.
- The Market page: watchlist (up to 10 symbols, saved per profile), analysis cards, trend matrix, an interactive pyqtgraph chart (timeframe switch, zoom and pan, crosshair, EMA 20/50/200, levels, swings, session shading), correlation, currency strength and the calendar with countdowns.
- The status bar shows the session clock and the next high-impact news.
- Phase 4 limitation fixed: history import now converts deal times with the broker's summer-time rules, and imports everything again once when those rules change.
- The opportunity scanner (ranking by setup state and probability x EV) needs strategies and the probability model, so it arrives with Phase 6.

### Checklist

- ✓ Analysis cards for 3 symbols update on closed bars: `tests/unit/test_market_watch.py` runs the real loop against FakeMT5 through the gateway; the same bar is not analysed twice, and the next closed M5 bar renews all 3 cards.
- ✓ Indicators match hand-calculated values and a step-by-step Wilder ADX reference.
- ✓ Market structure never looks ahead: the result for the first k bars equals the full result cut at bar k.
- ✓ Summer-time dates for the USA and the EU (2005, 2024, 2026), UTC conversion and the broker day are unit-tested.
- ✓ Data checks, levels, volatility, sessions, correlation, currency strength, spread, patterns, card rules, calendar CSV and store are unit-tested.
- ✓ Architecture tests: `analysis`, `calendar`, `engine` and `core` never import Qt or MetaTrader5, the analysis is pure, the market-data code never trades, and only the helper process imports MetaTrader5.
- ✓ In the sandbox: 304 unit tests passed, 3 skipped (the Market page UI tests run in CI). New tests run the helper process for real: package shapes, a hanging call that restarts it, a crash, a missing package, the gateway beating while it waits, a postponed heartbeat, the repeat filter, the spread rule from your account and the history import running again.
- ✓ CI before the log fixes: green on Linux and Windows, including the build job with the frozen `--self-check` and `--crash-test`. After the log fixes: the Linux tests are green; see the checks on pull request #15 for the Windows job and the build, which now also starts the MT5 helper in the frozen app.
- ✗ `CalendarExporter.mq5` was not compiled here (no MetaEditor). Compile it on your PC (see below).
- ✓ First run against a real MT5 demo account (your log): connection, history, broker time and 3 cards worked; the 4 problems above are fixed.
- ✗ The fixes are not yet checked on your PC. Please run the test below again and send the new log.
- ✗ `docs/ARCHITECTURE.md` still needs ADRs 46 to 48 (helper process, spread rule, repeated Qt messages); the reasons are in this file and in the commit messages.

### Test on your PC

1. Start the app, connect MT5, switch to the Advanced view and open Market. Within a few seconds 3 cards (EURUSD, GBPUSD, XAUUSD) show a headline. Note the "Updated at the HH:MM M5 close" line; after the next 5-minute close it changes.
2. Open the Chart tab: switch M5 to D1, drag to pan, use the wheel to zoom and move the mouse for the crosshair.
3. Open the Calendar tab and click Install MT5 exporter. In MT5 press F4 (MetaEditor), open Services > CalendarExporter.mq5 and press F7 (Compile). Back in MT5, Navigator > Services, right-click CalendarExporter > Add service, then start it. Click Read MT5 calendar now: upcoming events appear with countdowns.
4. Add a USD high-impact event 30 minutes from now by hand: the EURUSD and XAUUSD cards say "wait" after the next bar.
5. Check the status bar: the session clock and the next news.
6. While the cards load for the first time, move the window and switch pages: it must not freeze. In Task Manager you see a second `MT5TradingWorkstation` process (the MT5 helper). Then send `all.log` again: no repeated font warnings, and each card line ends with "Why: ...".

### Limitations

- The analysis card is information only and never a trade signal. Signals arrive in Phase 6.
- The session clock uses FX hours (Sunday to Friday, New York 17:00). Symbols with their own hours (indices) show "not trading right now" when their prices stop.
- The broker clock assumes UTC+2/+3 (New York close) until the first fresh price arrives; the card says so.
- The chart draws the bars kept by the analysis (600 per timeframe); deeper history arrives with backtesting.
- The spread status follows closed M5 bars, so a sudden spread jump shows after the bar closes; the live spread is shown on the card right away.

## Phase 4: Storage (branch `phase/04-storage`, pull request #14)

Status: pull request #14 into `phase/03-mt5-connection`, CI green, waiting for review.

### What was built

- A local SQLite database per profile (`profiles/<profile>/data/workstation.db`) in WAL mode, with frozen migrations, checksums and a backup before migrating.
- All E2 tables locally and in Supabase, plus the local `outbox`, `sync_state`, `mt5_deals` and `mt5_orders` tables.
- A transactional outbox and a background cloud sync: batches of 200, backoff from 2 s to 300 s, paused-project handling, refused rows parked with "Retry refused rows".
- Supabase SQL in `supabase/`: schema, Row Level Security, four views (`security_invoker`) and a cleanup function.
- Email sign-in and sign-up with the anon key only. The refresh token is kept in Credential Manager and the password is never saved.
- Log entries of level WARNING and higher and audit entries are saved in `app_logs` and `audit_log` and synced.
- Trade history import (deals and orders): trades are rebuilt per position and marked manual, bot or external.
- Daily backups (last 7 kept) and a local cleanup that never deletes trades, signals or rows waiting to upload.
- Settings has a new "Data & cloud sync" tab, and the status bar shows the cloud state.
- Supabase is called with httpx instead of supabase-py (ADR 30, needs your approval).

### Checklist

- ✓ Offline writes sync later without duplicates (unit tests with a fake Supabase: offline then online, lost response, refused rows, expired token, paused project).
- ✓ Migrations on a new and an existing database, running twice, changed checksum and a newer database (unit tests).
- ✓ Importing history twice creates no duplicates (unit tests with FakeMT5).
- ✓ SQLite, `schema.py` and `supabase/schema.sql` agree (unit test).
- ✓ In the sandbox: 225 unit tests passed, 3 skipped (the httpx client, UI and integration tests run in CI).
- ✓ CI is green on this pull request: `ruff check`, `ruff format --check`, `mypy` (strict) and pytest on Linux and Windows, including the httpx client, Data page and log storage tests; the build job builds the app and runs the frozen `--self-check` and `--crash-test`. The first run had found 4 mypy errors and a Windows-only file lock on a damaged database; both were fixed.
- ✗ Not tested with a real Supabase project (see "Test on your PC").
- ✗ The SQL files have not been run on a real Postgres database.
- ✓ (Phase 5) History import worked against your real MT5 demo account.
- ✓ (Fixed in Phase 5) Server time to UTC now follows the broker's summer-time rules.

### Test on your PC

1. Create a free Supabase project and run `supabase/schema.sql`, `rls.sql`, `views.sql` and `cleanup.sql` in its SQL Editor.
2. Start the app, open Settings, tab "Data & cloud sync", paste the project URL and the anon key, then click Create account or Sign in.
3. Connect MT5 and click Import history now. Note the trade count, click it again: the count must not change.
4. Turn the internet off for a few minutes while the app runs. The status bar must show "Cloud: offline" with rows waiting.
5. Turn the internet back on. The status must go to "Cloud: up to date", and in the Supabase Table Editor every row must appear once.

### Limitations

- Sync goes one way, from the PC to the cloud. Restoring from the cloud is not built.
- The daylight-saving limitation was fixed in Phase 5.
- Supabase is reached with httpx, not supabase-py (ADR 30).

## Phase 3: MT5 connection (branch `phase/03-mt5-connection`, stacked on pull request #10)

Built:

- `MT5Gateway`: one thread owns every call to the real `MetaTrader5` package, through a queue with a timeout on every call, a watchdog heartbeat and a log record (masked arguments, duration, error) for every request in the `mt5` category. (Phase 5: the package itself now runs in a helper process.)
- `FakeMT5` in `tests/fakes/` for tests only. An architecture test fails if any module except the MT5 helper module imports `MetaTrader5`, or if `app/` imports the fakes.
- The Test-connection checklist (spec C1, I3): terminal found, terminal running, login, account info, broker connection, Algo Trading, account trading permission, symbols, live quotes and history, each with the real value or a plain-language fix. `last_error()` is mapped to friendly messages (wrong password or server, IPC errors, MT4 or 32-bit terminal, not logged in, no connection), with a same-user and same-privilege hint.
- Account details shown: broker, server, login, name, `DEMO` / `REAL` / `CONTEST`, currency, balance, equity, leverage, hedging or netting, stop-out level and mode, terminal build.
- Investor password: detected from `account_info().trade_allowed`, switches to Analysis-only (status bar badge `ANALYSIS-ONLY`).
- Account profiles: `profiles/<name>/account.json` without the password; the password lives in Windows Credential Manager (`keyring`) and is registered with the log masker. One instance per profile (OS file lock); another profile opens in a new window. Auto-connect on start when a profile is saved.
- Terminal discovery: program folders, `%APPDATA%\MetaQuotes\Terminal\*`, registry, running processes; a server dropdown from the terminal's known servers; Browse as fallback.
- Connection service: heartbeat every 5 s, status bar text, reconnect with exponential backoff (2 s to 120 s) after a lost connection, a CRITICAL alert if positions are open while disconnected. A failed first connect is never retried on its own.
- Connection page (Advanced view, System, Settings): profile, terminal and account cards, Connect, Re-check, Disconnect, live bid/ask of 3 symbols every second, Run diagnostics, Copy report.
- Connection Diagnostics (spec I4): every checklist step plus ping, bars per symbol and timeframe, symbol specs, broker time offset and permission flags; the report never contains the password.
- `--mt5-smoke-test` (spec I4): read-only, prints the account, 3 ticks, the last 10 EURUSD M15 bars and the last 10 deals, exit code 0 or 1.
- Command palette: "Connect to MT5", "Run connection diagnostics".
- New dependency: `keyring==25.7.0` (the build adds its Windows backend as a hidden import).

Acceptance checklist (spec G3 phase 3):

- ✓ Gateway, checklist, diagnostics, smoke test, connection service, profiles, credentials, single-instance lock and terminal discovery pass their unit tests against `FakeMT5` in the agent sandbox (Python 3.12, without PySide6, loguru or keyring).
- ✓ The read-only tools never call `order_send` or `order_check` (tests check every call `FakeMT5` received).
- ✓ **The real connection works** (your log from 2 October 2026): FIBO Group demo, real login, balance, leverage and hedging mode.
- ✓ CI is green on this pull request: `ruff check`, `ruff format --check`, `mypy` (strict) and pytest on Linux and Windows, including the Qt tests of the Connection page and the integration test of the request log; the build job builds the app and runs the frozen `--self-check` and `--crash-test`.
- ✗ Investor mode, reconnect and the open-position alert are verified with `FakeMT5` only.
- ✗ Partly Windows-only: the test that compares the copied constants with the real package runs only in the Windows CI job. Terminal discovery and Credential Manager are tested with temporary folders and an in-memory store, not with a real installed terminal or the real vault.

## Phase 2: Observability (pull request #10, in review)

Built:

- Structured JSON logs through loguru with 17 categories (the 16 of spec E3 plus `update` for spec J4), each with its own files and its own level that can change at runtime.
- Files `logs/<category>/<date>.jsonl` and a readable `logs/all.log`, non-blocking (`enqueue=True`), rotated daily and at 20 MB, zipped, kept 30 days, total size capped at 500 MB.
- Every record has UTC time, level, category, module, function, line, thread, session id and, when set, trace id, signal id, trade id, ticket, symbol and strategy.
- Trace context (`trace()`, `propagate()` for threads).
- Secret masking in every log line, field, stack trace and crash report.
- Debug mode: all categories at DEBUG for 30 minutes, then automatic return.
- Crash handler: `sys.excepthook`, `threading.excepthook`, the Qt message handler and `faulthandler` write `crash_reports/crash_<time>.json` (masked stack, last 200 log entries, state, versions, OS) and open a friendly dialog.
- Watchdog: heartbeats, freeze detection with the stuck thread's stack, CRITICAL log, restart callback. The UI thread is watched from the start.
- Basic Logs page: live tail, filters (category, level, text or regex, one trace), JSON detail, level per category, debug mode, open folder.
- Startup log (versions, OS, profile, package versions, defaults) and audit entries for log level and debug mode changes.
- `--crash-test`: forces a crash and checks the report and the logs for leaked secrets; exit code 0 or 1. The build script runs it on the frozen exe.
- Command palette: "Toggle debug mode", "Open log folder", "Test the crash reporter".

Acceptance checklist (spec G3 phase 2):

- ✓ A forced exception produces a crash report: unit tests, the Qt tests and the end-to-end `--crash-test` integration test pass in CI on Linux (Python 3.11, real loguru and PySide6) and on Windows (`windows-latest`).
- ✓ Secrets are masked: unit tests cover token formats, key/value pairs, URL passwords, registered values, structured fields, stack traces and crash reports; the integration tests check the real log files and crash report; a negative test proves the crash test fails when masking is broken.
- ✓ The full pipeline is enabled on `main` and runs on this pull request after the sync: `ruff check`, `ruff format --check`, `mypy` (strict), pytest with coverage (Windows `ci` job) and the frozen `--self-check` + `--crash-test` build job (see maintainer PR #11).

## Known issues

- Resolved: the staged `ci/workflows/` folder is gone; `.github/workflows/` is maintained by the CI maintainer agent, so Brain does not need the `workflow` permission.
- No lock file yet; direct dependencies are pinned exactly.
- Tick freshness and data checks arrived in Phase 5 (Market page); the connection checklist itself still only checks that history exists and warns when "Max bars in chart" is low.
- "Start MT5 automatically" and "Start the app with Windows" (spec I5) are not built yet.
- `--mt5-trade-test` arrives with execution in Phase 8.
- Profiles cannot be switched inside a running window; another profile opens in a new window.
- Health checks, performance metrics, the debug bundle and the full Logs page (trace timeline, time filters, export) are Phase 14 items.

## Next steps

1. Run "Test on your PC" of Phase 5 again with the fixes and send the new `all.log`.
2. Review and merge the stacked pull requests in order: Phase 2 (#10), Phase 3 (#13), Phase 4 (#14), then Phase 5 (#15). Each one is retargeted to `main` after the one before it is merged.
3. Phase 6 (strategies and signals, plus the opportunity scanner) after Phase 5 is reviewed.
