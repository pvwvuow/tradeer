# Architecture

## Layers

```
app/
  __version__.py   single source of the version (bumped by release-please)
  main.py          composition root: CLI, self-check, crash test, GUI start
  cli.py           argument parsing, --self-check build verification, report output
  analysis/        pure market analysis on closed bars: indicators, trend, structure, levels, card (phase 5)
  calendar/        economic calendar: events, CSV import, store, MQL5 exporter (phase 5)
  core/            per-user paths, UI preferences, account profiles, credentials, instance lock, broker clock, watchlist
  domain/          pure logic: operating modes, validated defaults, trade history, signals, sizing (no Qt, MT5 or I/O)
  engine/          background workers: the market-analysis thread (phase 5), signal filters and pipeline (phase 6)
  mt5/             the MT5 gateway thread, connection checklist, diagnostics, smoke test, market data
  observability/   logging, trace context, masking, crash reports, watchdog (phase 2)
  risk/            risk settings and profiles, limits, currency exposure, limit state, risk manager (phase 7)
  storage/         SQLite database, migrations, outbox, cloud sync, history tracker (phase 4)
  strategies/      strategy interface, market context, the two example strategies (phase 6)
  ui/              Qt: theme tokens, navigation, command palette, pages, Logs, Connection, Data, Market, chart, Signals, Strategies, Risk
supabase/          SQL to run in the Supabase SQL Editor: schema, RLS, views, cleanup (phase 4)
tests/
  fakes/           FakeMT5 and FakeSupabase for tests only (never shipped)
  unit/            pure tests (no Qt, no loguru, no real MetaTrader5)
  integration/     the real loguru pipeline and the end-to-end crash test
  ui/              pytest-qt smoke tests (headless, QT_QPA_PLATFORM=offscreen)
scripts/ci/        check, build and release scripts used by the workflows
.github/workflows/ CI, codeql and release workflows (thin YAML; logic in scripts/ci)
installer/         Inno Setup script (per-user install, no admin rights)
```

The target layout for later phases is in `docs/SPEC.md` (D2).

### `app/observability`

| Module | Role | Imports loguru or Qt |
|---|---|---|
| `categories.py` | the 16 spec categories plus `update` | no |
| `levels.py` | runtime level per category, timed debug mode | no |
| `context.py` | session id, trace id and fields in `contextvars`, `propagate()` for threads | no |
| `masking.py` | `SecretMasker`: registered values, token formats, `key=value` pairs, URL passwords | no |
| `records.py` | the loguru patcher: level check, masking, JSON and readable line | no |
| `files.py` | `LogFileWriter`: category files, `all.log`, rotation, zip, retention, size cap | no |
| `buffer.py` | in-memory tail (5000 entries) for the Logs page and crash reports | no |
| `controls.py` | what the Logs page may change; every change is audit-logged | no |
| `crash_handler.py` | `CrashReporter`: hooks, masked JSON reports, `faulthandler` | no |
| `watchdog.py` | heartbeats, freeze detection with the stuck thread's stack, restarts | no |
| `crash_test.py` | the `--crash-test` acceptance check | no |
| `logger.py` | loguru handlers, `get_logger()`, `audit()`, `log_startup()` | loguru |
| `runtime.py` | `start_observability()`: wires everything for one run | loguru |

Qt parts live in `app/ui`: `logs_page.py`, `log_filter.py` (pure), `crash_dialog.py`, `qt_logging.py`.

### `app/mt5`

| Module | Role | Imports MetaTrader5 or Qt |
|---|---|---|
| `api.py` | `MT5Api` protocol (the subset of the package we use) and copied constants | no |
| `gateway.py` | `MT5Gateway`: the only thread that sends MT5 calls; queue, futures, timeouts, `busy()`, request log hook | no |
| `terminal_process.py` | `MT5Process`: the MT5 helper process that runs the package; pipe, call timeout, restart after a hang or crash (ADR 46) | MetaTrader5 (only inside the helper) |
| `errors.py` | `last_error()` codes and messages mapped to plain-language fixes | no |
| `models.py` | frozen snapshots: terminal, account, symbol spec, quote | no |
| `symbols.py` | resolves broker suffixes (`EURUSD` to `EURUSD.m`) | no |
| `checklist.py` | the Test-connection checklist (spec C1, I3) | no |
| `diagnostics.py` | Connection Diagnostics and the copyable, masked text report (spec I4); asks for fewer bars when MT5 refuses a big request (ADR 50) | no |
| `smoke_test.py` | `--mt5-smoke-test`: read-only account, quotes, bars and deals | no |
| `connection.py` | `ConnectionService`: state, heartbeat, reconnect with backoff, open-position alert | no |
| `terminals.py` | finds `terminal64.exe` installs and the servers each terminal knows | no |
| `privileges.py` | whether the app runs elevated (same-privilege hint) | no |
| `request_log.py` | writes every gateway request to the `mt5` log category | loguru |
| `market_data.py` | closed bars per symbol and timeframe, incremental, in one gateway request per poll; the poll tells when MT5 is still loading a symbol's bars (phase 5, ADR 49) | no |

`app/core` adds `profiles.py` (`profiles/<name>/account.json`, no password), `credentials.py` (Windows Credential Manager through `keyring`) and `single_instance.py` (an OS file lock per profile). The Qt part is `app/ui/connection_page.py`.

## Data flow

### Phase 1

`main.py` parses arguments. `--self-check` imports MetaTrader5 and Qt and exits 0 or 1. Otherwise it loads `UiPrefs` for the profile, builds `MainWindow`, and applies the theme generated from the design tokens. UI actions (view switch, theme switch) write `ui_prefs.json` atomically.

### Phase 2: a log line

1. Code calls `get_logger(LogCategory.MT5).info("Connected to {}", server)`.
2. loguru formats the message, then runs the patcher (`records.RecordPatcher`) in the calling thread. It drops the record if it is below the category level, adds the session id, trace id and trace fields from `context.py`, masks the message, every field and the stack trace, and renders one JSON line and one readable line. The raw exception is removed from the record.
3. Two handlers receive the masked record. The file handler (`enqueue=True`, one background thread) passes both lines to `LogFileWriter`. The tail handler appends the entry to `RecentLogBuffer` synchronously.
4. `LogFileWriter` appends to `logs/<category>/<YYYY-MM-DD>.jsonl` and `logs/all.log`, rotates on date change or size, zips rotated files, and runs housekeeping.
5. The Logs page polls the buffer every 250 ms (at most 4 updates per second, spec D4).

### Phase 2: a crash

`sys.excepthook` (main thread and Qt slots), `threading.excepthook` (worker threads), the Qt message handler (Qt fatal errors) and `faulthandler` (native crashes) feed `CrashReporter`. It writes `crash_reports/crash_<UTC time>.json` with the masked stack, the last 200 log entries, app state, versions and OS details, then notifies listeners: one logs a CRITICAL line with the masked stack, and `CrashNotifier` emits a Qt signal that opens the crash dialog on the GUI thread through a queued connection.

Files live per profile: `%APPDATA%\MT5TradingWorkstation\profiles\<profile>\logs` and `...\crash_reports`.

### Phase 3: an MT5 request

1. Code never calls `MetaTrader5` directly. It calls `gateway.call("symbol_info_tick", "EURUSD")` or submits a function with `gateway.run(name, fn)`; both return a `Future` result and raise `MT5Timeout` if the gateway thread does not answer in time (30 s by default).
2. The gateway thread (`mt5-gateway`) takes one request at a time from its queue, runs it against the real package (or `FakeMT5` in tests), and sends a heartbeat to the watchdog after every request and once a second when idle.
3. Every request produces a `RequestRecord` (name, masked arguments, duration, time spent in the queue, error) that `request_log.py` writes to the `mt5` category; requests slower than 1 s are logged as warnings.
4. Qt pages receive results through a queued signal, so the UI thread never waits for MT5.

### Phase 3: connecting

`ConnectionService.connect()` runs the checklist in the gateway thread: terminal found, `initialize(path)` (which also starts a closed terminal and waits), `login(login, password, server, timeout=60000)`, account info, broker connection (`terminal_info().connected`), Algo Trading (`terminal_info().trade_allowed`), account trading (`account_info().trade_allowed` and `trade_expert`), symbols, live quotes and history (bars plus the "Max bars in chart" setting). Each line shows the real value or a plain-language fix. After a successful connect the monitor polls a heartbeat every 5 s; a lost connection switches the status bar to "Reconnecting", logs a CRITICAL alert when positions are open, and retries with exponential backoff from 2 s up to 120 s.

On start the app takes `profiles/<name>/instance.lock`, loads `account.json`, reads the password from Credential Manager (and registers it with the log masker), and connects automatically when a profile is saved.

## Storage (Phase 4)

`app/storage/` owns the local database and its copy in Supabase. It never imports Qt or MetaTrader5, and a test checks that. `app/main.py` opens it with `open_storage()` and hands the `StorageRuntime` to the main window.

| Module | Role |
| --- | --- |
| `ids.py` | Row ids: random UUIDs, and uuid5 ids for accounts and trades |
| `sqlite_db.py` | `Database`: WAL mode, one connection per thread, transactions, `release()` |
| `schema.py` | Every table and column kind, indexes, conversion to SQLite and Supabase values |
| `migrations/` | Frozen migrations (`m0001_initial`), listed in `MIGRATIONS` |
| `migrate.py` | Applies migrations with checksums, after a backup |
| `outbox.py` | Versioned outbox entries: enqueue, batch, mark sent, park, retry |
| `repositories.py` | `Store`: upserts that enqueue in the same transaction, plus typed helpers |
| `backup.py` | Daily backups (last 7) and backups before migrating (last 3) |
| `cleanup.py` | Local retention rules |
| `log_store.py` | Log sink for WARNING and higher and for audit entries |
| `remote.py` | Error kinds, `AuthSession` and response classification, no network code |
| `supabase_client.py` | httpx client for PostgREST and Supabase Auth |
| `sync.py` | `SyncEngine` (one upload pass), `SyncWorker` thread, states and backoff |
| `cloud.py` | `cloud.json`, URL and key checks, sign-in, sign-up, sign-out, refresh token in Credential Manager |
| `tracker.py` | The connected account's row and snapshots, and the broker time offset |
| `runtime.py` | `open_storage()`: open, migrate, back up, start sync and maintenance |

Other Phase 4 modules: `app/domain/history.py` (pure deal and order models, trades rebuilt per position, manual, bot or external), `app/mt5/history_sync.py` (read-only history import through the gateway), `app/ui/data_page.py` (the "Data & cloud sync" settings tab) and `supabase/*.sql` (schema, RLS, views and cleanup to run in the Supabase SQL Editor).

### Storage data flow

```text
write (UI, MT5, logs) -> Store.upsert -> business row + outbox entry, one transaction
cloud-sync thread -> SyncEngine -> outbox batch (200 rows, parent tables first)
  -> SupabaseClient.upsert (PostgREST, RLS)
       ok      -> mark_sent, only if the version did not change
       refused -> split the batch, park the bad rows as failed
       offline -> back off 2 s .. 300 s, a paused project at least 600 s
       401     -> refresh once, then signed out
history import -> gateway deals and orders -> app.domain.history -> Store (mt5_deals, mt5_orders, trades)
logs -> LogPipeline -> LogStore sink (WARNING and higher, audit) -> app_logs, audit_log
```

## Decisions (ADR)

1. **Design tokens in one file, QSS generated** (`app/ui/theme.py`). Reason: one source of truth for colors, sizes and radii; themes are testable without Qt. Contrast is unit-tested against WCAG AA (4.5:1). The dark accent button uses dark text because white on `#5B8CFF` is only about 3.2:1.
2. **Pure helpers for UI logic** (`navigation.py`, `commands.py`, `log_filter.py`). Reason: page order, grouping, command ranking and log filtering are tested without a display.
3. **UI preferences as JSON per profile in `%APPDATA%`**, not the registry. Reason: portable, inspectable, easy to back up, safe defaults on corruption.
4. **`--self-check` plus a frozen-build guard.** Reason: spec I2 requires proof that the built exe can import MetaTrader5, and a production build must fail loudly instead of falling back to fake data. Windowed builds have no console, so the report can also be written to a file.
5. **Thin workflows that call `scripts/ci/*.ps1`.** Reason: some agent connections cannot modify `.github/workflows/`; keeping logic in scripts means the pipeline can still be maintained, and it removes duplication between CI and release.
6. **release-please and the release build in one workflow.** Reason: tags and pull requests created with the default `GITHUB_TOKEN` do not trigger other workflows, so a separate `on: push: tags` release workflow would never run after release-please. An optional `RELEASE_PLEASE_TOKEN` secret also lets CI run on release PRs.
7. **CI and build in one workflow file (`ci.yml`, jobs `ci` and `build`).** Reason: fewer workflow files to maintain; `ci` stays the required status check.
8. **Dependencies are added in the phase that needs them, pinned exactly.** Reason: faster, smaller CI and a smaller attack surface; the ML stack is not installed before Phase 11. A full lock file is generated when `uv` is introduced.
9. **Per-user installer (`PrivilegesRequired=lowest`).** Reason: the app must run as the same Windows user and privilege level as the MT5 terminal; mismatches are a common cause of `initialize()` failures.
10. **One loguru file handler plus our own `LogFileWriter`**, instead of one loguru file sink per category. Reason: seventeen `enqueue=True` sinks would mean seventeen writer threads and pipes; one handler keeps logging non-blocking with a single thread. Rotation, zip compression, 30-day retention and the total size cap (500 MB default; loguru has no size cap) are plain Python and unit-tested.
11. **Mask in the patcher, before any sink.** Reason: no sink can see unmasked text. The exception is rendered and masked into `stack_trace`, then removed from the record, so loguru never prints a raw traceback; `diagnose=False` keeps variable values out of tracebacks. Masking layers: values registered at runtime (passwords read from the keyring from Phase 3 on), token formats, sensitive `key=value` pairs and URL passwords. Over-masking is accepted; leaking is not.
12. **An `update` category in addition to the 16 categories of E3.** Reason: spec J4 requires update steps to be logged to `update`, which E3 does not list. Resolving the contradiction by adding it keeps both sections true.
13. **Crash reports are masked twice and throttled.** Reason: the report is masked as a whole after it is built, in case a field was missed. At most 5 reports per minute and the newest 50 are kept, so a crash loop cannot fill the disk. The state provider must return plain data, because hooks run on any thread and Qt widgets must not be touched off the GUI thread.
14. **`--crash-test` as an executable acceptance test.** Reason: spec principle A8 says nothing is claimed without a test. The command logs two throwaway secrets, crashes a worker thread on purpose, and checks the report and the log files; `build.ps1` runs it on the frozen exe, and the user can run it on any build.
15. **Watchdog with heartbeats, a restart callback and a stack dump.** Reason: a freeze is reported once (CRITICAL log plus a report without a dialog) with the stack of the stuck thread, which is what is needed to fix it. The UI thread sends a heartbeat every second from a `QTimer`; workers from Phase 3 register their own restart function.
16. **Local dates in log file names, UTC in records.** Reason: files are found by the user's calendar day; every record stores UTC time (spec C2 and E3).
17. **One gateway thread owns every MT5 call** (spec D3). Reason: the `MetaTrader5` package keeps one global connection per process and is not safe to call from several threads. A queue with futures and a timeout on every call means a hung terminal can never block the UI or another worker; the watchdog watches the thread under the name `mt5-gateway` (freeze limit 120 s, because `initialize()` may wait up to 60 s for a terminal to start). Since the first real run the package lives in a helper process (ADR 46): only `terminal_process.py` imports it, and an architecture test fails the build if another module does. The gateway thread is still the only sender.
18. **`MT5Api` protocol with copied constants.** Reason: the package ships Windows wheels only, so Linux CI cannot import it. The constants the app needs live in `api.py`; a Windows-only test compares them with the real package, and `FakeMT5` implements the same protocol.
19. **`initialize(path)` first, then `login()`**, instead of one `initialize(path, login, password, server)` call. Reason: the two steps fail for different reasons (terminal or IPC versus credentials), so the checklist can show the correct fix for each. The 60 s login timeout from spec I3 is kept.
20. **Never retry a failed first connection automatically.** Reason: retrying a wrong password can lock the account at the broker. Automatic reconnection with exponential backoff (2 s to 120 s) only starts after a connection that worked was lost.
21. **Investor password means Analysis-only.** Reason: an investor login reports `account_info().trade_allowed = False`; the app detects it, shows the `ANALYSIS-ONLY` badge and never offers trading on that connection.
22. **Profiles in `profiles/<name>/account.json`, passwords in Windows Credential Manager.** Reason: spec C1 and D5. The JSON holds login, server and terminal path, never the password. `keyring` is forced to `WinVaultKeyring` on Windows so a third-party backend cannot take over, and every password read or saved is registered with the log masker at once.
23. **One app instance per profile, enforced by an OS file lock.** Reason: two instances on the same account would fight over the terminal and the log files. The lock is released by the OS if the app crashes. Opening another profile starts a new app window (`--profile NAME`) instead of switching in place, so a running engine is never swapped under itself.
24. **Terminal discovery from four sources.** Reason: many users have several brokers installed. Program folders, `%APPDATA%\MetaQuotes\Terminal\*\origin.txt` (UTF-16 or UTF-8), the registry uninstall keys and running processes are merged, and the server dropdown is filled from the terminal's `bases` folder. "Browse" stays as the fallback.
25. **Read-only tools stay read-only.** Reason: the checklist, diagnostics and `--mt5-smoke-test` only read. `FakeMT5` records every call, and tests assert that none of these tools ever calls `order_send` or `order_check`.
26. **Failed CI checks are repeated as annotations.** Reason: the assistant's GitHub connection cannot read job logs, but check-run annotations are public. `scripts/ci/check.ps1` runs every check even after one fails and writes the output of each failed check as an annotation; a hook in `tests/conftest.py` does the same for failed tests on Linux and Windows.
27. **SQLite is the local source of truth.** Reason: the app must keep working when the internet or Supabase is down, and it must never lose trades, signals or logs. Every write goes to a local SQLite file first: `profiles/<profile>/data/workstation.db`. It runs in WAL mode with `synchronous=NORMAL`, a 5000 ms busy timeout, autocommit connections (`isolation_level=None`) and explicit `BEGIN IMMEDIATE` transactions. Each thread has its own connection, and a nested transaction joins the outer one. Result: readers never block the writer, and the cloud is only a copy. A background thread that used the database calls `Database.release()` before it ends.
28. **Frozen migrations with checksums.** Reason: users update the app over an existing database, and PyInstaller only bundles modules it can see. Each migration is a Python module in `app/storage/migrations/` with an `SQL` string, listed explicitly in `MIGRATIONS`. The `schema_migrations` table stores the version, name, sha256 checksum and time applied. All statements of one migration run in one transaction, and an existing database is backed up before migrating (the last 3 of these backups are kept). A changed checksum or a database newer than the app is refused with a clear error. Result: every schema change is a new migration module. `m0001_initial` never changes again; a test pins its checksum.
29. **Transactional outbox with versioned entries.** Reason: rows written offline must reach the cloud later, once, without double uploads. `Store` writes the business row and its `outbox` entry in the same transaction. There is one entry per table and row id, with a version counter: a newer write replaces the pending entry, and `mark_sent` removes the entry only if the version did not change during the upload. Rows the server refuses are parked as `failed` and can be retried from the UI. Writing an unchanged row does not enqueue it again. Result: every row reaches the cloud in its latest version. Uploads are idempotent upserts, so a lost response only sends the same row again.
30. **httpx instead of supabase-py (deviation from D1).** Reason: D1 names supabase-py. The app only needs idempotent upserts and email sign-in from Supabase. `app/storage/supabase_client.py` calls Supabase with `httpx==0.28.1`: `POST /rest/v1/<table>?on_conflict=id` with `Prefer: resolution=merge-duplicates,return=minimal` for uploads, `/auth/v1/token` (password or refresh token) for sign-in, and `/auth/v1/signup` for new accounts. Timeouts are explicit, and the tests use `httpx.MockTransport`. Result: one small dependency instead of the supabase-py tree, and full control over upserts and errors. The sync engine only knows the `Remote` protocol, so moving to supabase-py later means replacing this one module. This deviation needs the owner's approval.
31. **Ids are made on the PC, deterministic where it matters.** Row ids are UUIDs created on the PC. Accounts use uuid5 of server and login, and trades use uuid5 of account and position id. Other rows get a random UUID. Result: rows can be written offline, and reconnecting or importing history again never creates duplicates.
32. **No foreign keys.** Tables have no foreign keys, locally or in Supabase. Rows point to each other through indexed id columns. Result: rows can upload in any order, and a half-finished upload never fails on a constraint. Parent tables still upload first so the cloud stays tidy.
33. **Supabase Auth with email, RLS on every table.** The user signs in with email and password using the anon (publishable) key only; service_role and secret keys are refused. Every remote table has `user_id default auth.uid()` and RLS policies `user_id = auth.uid()`, and the views use `security_invoker = true`. The password is never saved. The refresh token is kept in Windows Credential Manager under `<profile>/supabase-refresh-token`, and `cloud.json` holds only the URL, the anon key, the email and the on/off switch. Result: one project can hold several users safely, and the anon key alone cannot read anybody's rows.
34. **Sync states and backoff.** The `cloud-sync` thread uploads batches of up to 200 rows, grouped by table, parent tables first. Failures back off from 2 s, doubling up to 300 s, and a paused project waits at least 600 s. A 401 refreshes the session once and then signs out. A refused batch is split in half until only the bad rows are parked. A missing table or column (404, PGRST204, PGRST205) means "setup needed" and keeps the rows. States: disabled, signed_out, up_to_date, syncing, offline, paused, setup_needed, error. Result: a paused free project or a bad network never loses rows, and the status bar always says what is going on.
35. **Warnings and audit entries go to the database.** A `LogStore` sink on the log pipeline writes entries of level WARNING and higher to `app_logs` and audit entries to `audit_log`, so they reach the outbox too. The sink never logs about itself. Result: problems on the user's PC show up in the cloud, while DEBUG and INFO stay in the local log files.
36. **Trades are rebuilt from deals.** History import reads deals and orders through the gateway: from 2000-01-01 the first time, then from 3 days before the newest stored deal. Trades are rebuilt from all deals of every position the import touched, in pure code (`app/domain/history.py`). Magic 0 means manual, the bot's magic numbers mean bot, and anything else is external. Server times are turned into UTC with the broker offset measured from fresh quotes, or 0 if it is unknown. Result: importing again is safe and gives the same trades. Since Phase 5 the conversion uses the broker clock with its summer-time rules (ADR 41), and a change of those rules imports everything again once.
37. **Backups and cleanup.** The database is backed up once a day with the SQLite backup API, and the last 7 backups are kept. Local cleanup removes `app_logs` and `mt5_requests` rows after 90 days and `health_checks` and `performance_metrics` rows after 30 days. It never deletes trades or signals and never deletes a row that still waits in the outbox. `supabase/cleanup.sql` has an optional cleanup function for the cloud. Result: the database stays small without losing anything that matters.
38. **One schema definition, checked three ways.** `app/storage/schema.py` lists every table and the kind of every column. `m0001_initial` and `supabase/schema.sql` were written from it, and a test checks that SQLite, `schema.py` and `supabase/schema.sql` agree. Result: the local and cloud schemas cannot drift apart silently.
39. **Analysis is pure numpy, no pandas yet.** `app/analysis/` works on `Bars` (numpy columns, UTC and server times) and never touches MT5, files, threads or the database; an architecture test enforces it. Reason: the same function must give the same result in live, paper and backtest (spec D3.4), and numpy alone is enough for these indicators. pandas arrives when a phase needs it (ML features).
40. **Closed bars only, one poll request.** Bars come from `copy_rates_from_pos(..., 1, n)`, so the forming bar is never used. Every 2 s a single gateway request reads each symbol's tick and newest closed M5 bar; only a new closed M5 bar triggers a full update and analysis of that symbol. Correlation and currency strength follow every new H1 bar. Result: about one MT5 request per 2 s while nothing closes, and evaluation strictly on closed candles (spec D3.3).
41. **Own summer-time rules for broker time.** Windows has no tz database, so `app/core/clock.py` writes out the US rules (since 2007, and 1987-2006) and the EU rules. The offset is measured from fresh ticks in half-hour steps and changes only after two agreeing ticks. UTC+2/+3 on US dates is taken as New York close time, UTC+1/+2 on EU dates as Central European time, anything else as fixed. A change outside a summer-time date is logged as a broker time jump and blocks the analysis for an hour. Bars keep server time in the cache and are converted on every read, so a corrected clock fixes the whole cache. The trading day is the broker day.
42. **Bad data skips the evaluation.** `app/analysis/quality.py` returns errors (no bars, broken bars, a spike over 10x ATR on the newest 3 bars, a stale price while bars move, a recent time jump) and warnings (missing bars, zero volume, late bars, a symbol not trading, an assumed clock). Weekend and holiday gaps are measured against FX opening hours and never reported. Any error makes the card "data problem" and is logged.
43. **The card is rules, never a signal.** The headline follows a fixed pattern (higher-timeframe trend, H1 context and nearest level in ATR, volatility, session, next news, verdict). Verdicts are only wait, watch, no clear direction and data problem, and every card ends with "Information only, not a trade signal."
44. **Calendar through an MQL5 service.** The Python package cannot read the MQL5 calendar (spec C3). `CalendarExporter.mq5` is a read-only service that writes `Common/Files/tradeer_calendar.csv` (UTF-8, UTC epoch times) through a temporary file and `FileMove`, every 5 minutes. The app installs the source into the terminal's `MQL5/Services`, reads the file when its time stamp or size changed, and stores events with a stable id (time, currency, title), so imports never duplicate. The `.mq5` is bundled with PyInstaller (`--add-data`) and checked by `--self-check`.
45. **pyqtgraph 0.14.0, without subclassing.** 0.13.7 crashes with Qt 6.10 when experimental options are on; 0.14.0 supports Qt 6.8+. Candles are a `BarGraphItem` plus wick lines (`connect="pairs"`), the x axis counts bars (no weekend gaps) with local-time labels from `setTicks`. No pyqtgraph class is subclassed, because pyqtgraph has no type information and mypy strict refuses subclasses of `Any`.
46. **MetaTrader5 runs in a helper process** (`app/mt5/terminal_process.py`). Reason: the first log from a real PC showed the window frozen for 12 s and 16 s exactly while MT5 answered slowly (connecting, the first history download), and the watchdog thread stopped too, so the package keeps Python's global lock while it waits. The helper is started with `multiprocessing` (spawn; `freeze_support()` in `run_app.py` and `app/__main__.py`). Each call is one message through a pipe; named results come back as named tuples with the same fields, numpy arrays unchanged. The waiting gateway thread beats the watchdog every second. A call longer than 120 s, or a helper that dies, restarts the helper; the heartbeat then sees the terminal as lost and logs in again. A heartbeat that waits behind another long request is postponed, not counted as a lost connection. `--self-check` starts the helper once, so the frozen build proves it works.
47. **The spread check compares bar spread with bar spread.** Reason: MT5 stores the minimum spread of each bar (MQL5 book, `MqlRates.spread`). Comparing the live spread with those minimums made GBPUSD and XAUUSD "very wide" ("wait") all day on a real account. The status now uses the last closed M5 bar's spread against the median bar spread of the same UTC hour; the live spread is still shown.
48. **Repeated Qt messages are collapsed** (`app/observability/repeats.py`). Reason: one harmless Qt font warning was 95% of a real log. A message is logged once, then counted for 60 s and logged again with the count; fatal messages always pass. The cause, font sizes in px (Qt then reports point size -1), was removed: the stylesheet uses pt.
49. **The card waits while MT5 still loads a symbol's bars.** Reason: in the second real run MT5 answered the first XAUUSD request with bars two hours old (the same levels as the run before) and caught up two seconds later; the first card said "wait, spread 3.2x typical" from those old bars. A live price always sits in the forming M5 bar, so the poll reads positions 1 and 0 of M5 (`copy_rates_from_pos(symbol, M5, 0, 2)`): the newest closed bar as before, and the open time of the forming bar, only to compare it with the price; its prices are never used. When the forming bar is more than two bars older than the price while the FX market is open, the symbol is not analysed: the card says LOADING, the log says why, and after 60 s a warning asks the user to open a chart of the symbol in MT5. The symbol is analysed as soon as the bars arrive. A card now also follows every new analysis, not only a new bar time, so a calendar change shows at once.
50. **The history probe asks again with fewer bars.** Reason: a real terminal (Max bars in chart 100,000) refused `copy_rates_from_pos(..., 0, 100000)` for every symbol and timeframe, so Diagnostics showed 0 bars. The probe asks for at most Max bars in chart, then 50,000, 10,000 and 1,000 bars, reports `N+` when it got all it asked for, and shows the MT5 error when nothing comes back. The MT5 helper also reports every start, so the log shows its pid and whether it restarted.
51. **London breakout times are London local time.** Reason: the spec names "Asia range" and "London open" without a clock. In London time the session keeps its meaning through both summer-time changes; `clock` can be set to `broker` or `utc`. Range 00:00 to 07:00, stop orders at 08:00, cancel at 11:00.
52. **"ATR (D1-normalized)" means ATR14(D1) x sqrt(range hours / 24).** Reason: the Asia range covers 7 hours, not a day; volatility grows with the square root of time, so the D1 ATR is scaled to the range length. The width must be 0.5 to 1.5 of it; buffer 0.1, SL cap 1.5.
53. **The breakout is one OCO pair: two signals.** A buy stop and a sell stop share `features["oco_group"]`; the signal id contains the direction, so "one signal per symbol, strategy and bar" counts per side. Phase 8 cancels the other order when one fills.
54. **Probability is a baseline until a model exists.** The strategy's win rate from resolved signals with a Wilson 95% interval, "unknown" below 30 samples. With `require_probability` off (default) the probability and EV filters are recorded as "not applied" instead of passing silently.
55. **The pipeline runs in the analysis thread.** `MarketWatch` calls it after a symbol was analysed on a new closed bar; the UI only queues a dismiss, which the next cycle applies. Reason: one writer thread, no locks around the database, and the trace order is the real order.
56. **Signal ids are uuid5 of strategy, params hash and side, symbol, timeframe and bar time.** Reason: evaluating the same bar again (a restart, a second poll) gives the same id, so a signal is never duplicated and the save is idempotent.
57. **No new column for the expiry.** The `signals` table of E2 has no `expires_at`; the expiry, bar close time, digits, params hash and state history are kept in `features_json`, and the trace records "expires at" as an execution step. Reason: no schema change and no migration in Phase 6.
58. **Risk is a placeholder step until Phase 7.** Every trace has a `risk` step marked "not applied", so the acceptance check (every stage present) holds now and Phase 7 only replaces that step. Replaced in Phase 7 (ADR 62).
59. **Settings forms come from the params models.** `app/core/param_fields.py` reads the pydantic JSON schema (type, limits, choices, description), and the model validates what the user typed. Reason: a new parameter needs no UI code, and invalid values never reach a strategy.
60. **Size from `order_calc_profit`, never from the tick value.** Reason: on a real FIBO account XAUUSD reported a tick value of 0.1 USD where its contract (100 oz) makes a point worth 1 USD, so tick-value sizing would risk 10x too much. `order_calc_profit(BUY or SELL, symbol, 1 lot, entry, SL)` returns the loss in the account currency and converts JPY, gold and cross-currency accounts itself. Stop and limit orders use BUY or SELL too (the calc functions accept only those). An architecture test keeps `tick_value` out of the sizing code.
61. **Volume rounds down; the minimum lot never rounds up.** When the minimum lot would risk more than allowed, the trade is rejected (spec C6). The commission per lot is the history estimate (all commissions of the symbol's deals / lots opened) or the setting.
62. **The risk manager runs in the analysis thread.** The pipeline calls it for every signal that passed the filters; `on_cycle` refreshes the usage every 30 s. Account, positions and today's deals come from one gateway call. The UI queues Stop and Re-enable, applied in that thread. Reason: one writer for the limit state, like ADR 55.
63. **The limit state is local key-value JSON.** One `sync_state` key per account (`risk_state:<account id>`), no migration and nothing uploaded; events go to the synced `risk_events` table. A damaged value stops trading ("could not be read") instead of resetting a hit limit.
64. **Daily loss = start-of-day equity + today's deposits - equity.** The trading day is the broker date (`BrokerClock.broker_date`). When the app saw the rollover (state updated within 15 minutes) the start is the equity then; otherwise balance - today's realized - today's deposits, marked "estimated" (it ignores positions opened earlier, so it can only overstate the loss). Deposits and withdrawals also move the drawdown basis.
65. **Currency exposure is risk-weighted.** Each position is +risk on its base and -risk on its quote currency (`currency_margin`, `currency_profit`); the limit is the largest net value in % of capital on a currency the new trade touches (default 1% = two 0.5% trades in one direction). A position without a SL counts with its current loss.
66. **One magic number per strategy, never reused.** `MAGIC_NUMBERS` in the registry; anything else (magic 0 or another EA) is "manual" for the limits and counts when `count_manual_trades` is on.
67. **Profiles are presets of one settings model.** `risk.json` stores the profile name and all values; an edited preset becomes "custom". The model holds the hard caps (1% per trade, 5% open risk, 10% daily, 50% drawdown), so neither the file nor the form can go above them; an unreadable file gives Normal.

## Logging rules for new code

- `log = get_logger(LogCategory.X)` at module level. `print()` is only for CLI reports.
- Dynamic text goes in as an argument, never into the format string: `log.info("Order sent: {}", text)`. A brace inside a format string with arguments breaks the call.
- Extra fields: `log.bind(symbol="EURUSD").info(...)`. Fields named like `password`, `token`, `api_key` or `pin` are masked automatically.
- Register every secret the moment it is read: `MASKER.register(value)`.
- Wrap work for one signal in `with trace(signal_id=..., symbol=...):`, and pass work to other threads with `propagate(fn)`.
- Setting changes go through `audit(action, before=..., after=...)`.
- Measure durations with `time.perf_counter()`. Before Python 3.13, `time.monotonic()` on Windows ticks in 15.6 ms steps.

## MT5 rules for new code

- Call MT5 only through the gateway: `gateway.call(...)` for one function, `gateway.run(name, fn)` for several calls that belong together. Never import `MetaTrader5` outside `app/mt5/terminal_process.py`; results cross a process boundary, so read them by field name.
- Never block the UI thread on a gateway future; deliver results with a queued Qt signal.
- Never judge bars that trail the live price: check `PollResult.loading()` first (ADR 49). MT5 may refuse very large bar requests; ask again with fewer bars (ADR 50).
- Never size a position from `trade_tick_value`: FIBO reports 0.1 USD for XAUUSD although a point is worth 1 USD. Use `order_calc_profit` (Phase 7).
- Check `None` results and read `last_error()` through `app/mt5/errors.py`, which maps it to a plain-language fix.
- Use `resolve_symbol()` for every symbol a user types (brokers add suffixes), then `symbol_select()` it.
- Test with `FakeMT5` from `tests/fakes/fake_mt5.py`; it never ships with the app.

## Storage rules

- Write synced rows only through `Store`, so the outbox entry lands in the same transaction.
- A schema change is a new migration module plus the matching change in `schema.py`, `supabase/schema.sql` and `supabase/rls.sql`. Never edit `m0001_initial`.
- A thread that used the database calls `Database.release()` before it ends.
- Never delete trades or signals. Cleanup skips rows that still wait in the outbox.
- Only the anon key may reach the app. Never add code that accepts a service key.
- `app/storage` and `app/domain` never import Qt or MetaTrader5.

## Analysis rules

- `app/analysis`, `app/calendar`, `app/engine` and `app/core` never import Qt or MetaTrader5; `app/analysis` never imports `app.mt5`, `app.storage` or `app.engine`, and does no I/O.
- Evaluate on closed bars only. Never use bar position 0.
- Store UTC. Convert server time only with `BrokerClock`.
- A new data problem is a `quality.py` issue with a test, never a silent skip.
- MT5 bar spreads are minimum spreads: compare them only with other bar spreads.

## Strategy and signal rules

- A strategy is pure: it reads only its `MarketContext` and returns an `Evaluation` with every rule as a `Condition`. No MT5, storage, engine, UI, files or threads (architecture test).
- Bump a strategy's `version` when its logic changes; a params change gives a new params hash and config id by itself.
- Every signal goes through `SignalPipeline`; never save a signal without its decision trace, and never change a state except through `Signal.with_state`.
- A new filter returns a `TraceStep` with value, threshold and pass, fail or "not applied" (`None`) with the reason.
- Strategy examples always carry `EXAMPLE_NOTE`: they are not proven to be profitable.

## Risk rules

- Every signal that passed the filters goes through `RiskManager.evaluate`; nothing may size, send or approve a trade around it. It never raises: an error rejects the trade.
- Size only from `order_calc_profit` and the symbol's volume limits; never read `tick_value` (architecture test).
- Sizing, limits, exposure and the limit state are pure (architecture test); MT5 reads live in `app/mt5/risk_reads.py` and run in the gateway thread.
- Every limit is a `LimitCheck` with value, threshold and detail, and becomes one line of the decision trace. A new blocking check names its `risk_events` type.
- The limit state is written only in the analysis thread and saved after every change; never clear a stop without the user's typed confirmation (except a daily stop at the next trading day).
