# Architecture

## Layers

```
app/
  __version__.py   single source of the version (bumped by release-please)
  main.py          composition root: CLI, self-check, crash test, GUI start
  cli.py           argument parsing, --self-check build verification, report output
  core/            per-user paths, UI preferences, account profiles, credentials, instance lock
  domain/          pure logic: operating modes, validated defaults, trade history (no Qt, MT5 or I/O)
  mt5/             the MT5 gateway thread, connection checklist, diagnostics, smoke test (phase 3)
  observability/   logging, trace context, masking, crash reports, watchdog (phase 2)
  storage/         SQLite database, migrations, outbox, cloud sync, history tracker (phase 4)
  ui/              Qt: theme tokens, navigation, command palette, pages, Logs, Connection, Data, crash dialog
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
| `gateway.py` | `MT5Gateway`: the only thread that touches MT5; queue, futures, timeouts, request log hook | MetaTrader5 (lazy, in `load_mt5()`) |
| `errors.py` | `last_error()` codes and messages mapped to plain-language fixes | no |
| `models.py` | frozen snapshots: terminal, account, symbol spec, quote | no |
| `symbols.py` | resolves broker suffixes (`EURUSD` to `EURUSD.m`) | no |
| `checklist.py` | the Test-connection checklist (spec C1, I3) | no |
| `diagnostics.py` | Connection Diagnostics and the copyable, masked text report (spec I4) | no |
| `smoke_test.py` | `--mt5-smoke-test`: read-only account, quotes, bars and deals | no |
| `connection.py` | `ConnectionService`: state, heartbeat, reconnect with backoff, open-position alert | no |
| `terminals.py` | finds `terminal64.exe` installs and the servers each terminal knows | no |
| `privileges.py` | whether the app runs elevated (same-privilege hint) | no |
| `request_log.py` | writes every gateway request to the `mt5` log category | loguru |

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
17. **One gateway thread owns every MT5 call** (spec D3). Reason: the `MetaTrader5` package keeps one global connection per process and is not safe to call from several threads. A queue with futures and a timeout on every call means a hung terminal can never block the UI or another worker; the watchdog watches the thread under the name `mt5-gateway` (freeze limit 120 s, because `initialize()` may wait up to 60 s for a terminal to start). Only `gateway.py` imports the package, and an architecture test fails the build if another module does.
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
36. **Trades are rebuilt from deals.** History import reads deals and orders through the gateway: from 2000-01-01 the first time, then from 3 days before the newest stored deal. Trades are rebuilt from all deals of every position the import touched, in pure code (`app/domain/history.py`). Magic 0 means manual, the bot's magic numbers mean bot, and anything else is external. Server times are turned into UTC with the broker offset measured from fresh quotes, or 0 if it is unknown. Result: importing again is safe and gives the same trades. Known limitation: the offset is today's, so trades from the other side of a daylight-saving change can be one hour off until Phase 5.
37. **Backups and cleanup.** The database is backed up once a day with the SQLite backup API, and the last 7 backups are kept. Local cleanup removes `app_logs` and `mt5_requests` rows after 90 days and `health_checks` and `performance_metrics` rows after 30 days. It never deletes trades or signals and never deletes a row that still waits in the outbox. `supabase/cleanup.sql` has an optional cleanup function for the cloud. Result: the database stays small without losing anything that matters.
38. **One schema definition, checked three ways.** `app/storage/schema.py` lists every table and the kind of every column. `m0001_initial` and `supabase/schema.sql` were written from it, and a test checks that SQLite, `schema.py` and `supabase/schema.sql` agree. Result: the local and cloud schemas cannot drift apart silently.

## Logging rules for new code

- `log = get_logger(LogCategory.X)` at module level. `print()` is only for CLI reports.
- Dynamic text goes in as an argument, never into the format string: `log.info("Order sent: {}", text)`. A brace inside a format string with arguments breaks the call.
- Extra fields: `log.bind(symbol="EURUSD").info(...)`. Fields named like `password`, `token`, `api_key` or `pin` are masked automatically.
- Register every secret the moment it is read: `MASKER.register(value)`.
- Wrap work for one signal in `with trace(signal_id=..., symbol=...):`, and pass work to other threads with `propagate(fn)`.
- Setting changes go through `audit(action, before=..., after=...)`.

## MT5 rules for new code

- Call MT5 only through the gateway: `gateway.call(...)` for one function, `gateway.run(name, fn)` for several calls that belong together. Never import `MetaTrader5` outside `app/mt5/gateway.py`.
- Never block the UI thread on a gateway future; deliver results with a queued Qt signal.
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
