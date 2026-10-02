# Progress

Continue in a new chat with: "Read docs/SPEC.md and docs/PROGRESS.md and continue."

## Completed phases

- Phase 1: Foundation, merged into `main` through pull request #1 on 2026-10-01.
- Phase 2: Observability, pull request #10, CI green, waiting for review.
- Phase 3: MT5 connection, pull request #13, CI green, waiting for review.

## Phase 1: Foundation (pull request #1, merged)

Built: Python 3.11 project with exact pins, ruff, mypy (strict), pytest, pytest-qt, pre-commit; the desktop shell (Simple view by default, Advanced view with the grouped sidebar and 14 pages that name their phase); design tokens with a generated stylesheet, dark and light theme; status bar with the `PAPER` badge and the kill switch (inactive); command palette (Ctrl+K); `--self-check`, `--report-file`, `--profile`, `--version`; build, release and installer scripts; agent rules; the full specification in `docs/SPEC.md`.

Follow-up: the full pipeline (mypy, frozen self-check and crash test, installer, CodeQL, release-please) was enabled in `.github/workflows/` by the CI maintainer through pull request #11.

## Current phase: 4 Storage (branch `phase/04-storage`)

Status: built on `phase/04-storage`, pull request into `phase/03-mt5-connection`, waiting for review.

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
- ✗ History import not checked against a real MT5 account.
- ✗ Server time to UTC uses today's broker offset, so trades from before a daylight-saving change can be one hour off.

### Test on your PC

1. Create a free Supabase project and run `supabase/schema.sql`, `rls.sql`, `views.sql` and `cleanup.sql` in its SQL Editor.
2. Start the app, open Settings, tab "Data & cloud sync", paste the project URL and the anon key, then click Create account or Sign in.
3. Connect MT5 and click Import history now. Note the trade count, click it again: the count must not change.
4. Turn the internet off for a few minutes while the app runs. The status bar must show "Cloud: offline" with rows waiting.
5. Turn the internet back on. The status must go to "Cloud: up to date", and in the Supabase Table Editor every row must appear once.

### Limitations

- Sync goes one way, from the PC to the cloud. Restoring from the cloud is not built.
- The daylight-saving limitation above stays until Phase 5.
- Supabase is reached with httpx, not supabase-py (ADR 30).

## Phase 3: MT5 connection (branch `phase/03-mt5-connection`, stacked on pull request #10)

Built:

- `MT5Gateway`: one thread owns every call to the real `MetaTrader5` package, through a queue with a timeout on every call, a watchdog heartbeat and a log record (masked arguments, duration, error) for every request in the `mt5` category.
- `FakeMT5` in `tests/fakes/` for tests only. An architecture test fails if any module except `app/mt5/gateway.py` imports `MetaTrader5`, or if `app/` imports the fakes.
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
- ✗ **The real connection is not verified.** The spec criterion is that the downloaded build connects to your real MT5 demo account and shows real broker, login, balance and live bid/ask. Only you can check this on your PC (see the pull request, "Test on your PC").
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
- The deal and order history import arrived in Phase 4. Tick freshness checks belong to Phase 5; the checklist only checks that history exists and warns when "Max bars in chart" is low.
- "Start MT5 automatically" and "Start the app with Windows" (spec I5) are not built yet.
- `--mt5-trade-test` arrives with execution in Phase 8.
- Profiles cannot be switched inside a running window; another profile opens in a new window.
- Health checks, performance metrics, the debug bundle and the full Logs page (trace timeline, time filters, export) are Phase 14 items.

## Next steps

1. Done: the full pipeline is enabled on `main` (pull request #11) and merged into Phase 2; fix anything it reports on this pull request.
2. Review and merge the stacked pull requests in order: Phase 2 (#10), Phase 3 (#13), then Phase 4. Each one is retargeted to `main` after the one before it is merged.
3. Run "Test on your PC" from the Phase 3 and Phase 4 pull requests against your MT5 demo account and a free Supabase project, and report the result.
4. Phase 5 from the spec's phase list (docs/SPEC.md, G3), after Phase 4 is reviewed.
