# Architecture

## Layers

```
app/
  __version__.py   single source of the version (bumped by release-please)
  main.py          composition root: CLI, self-check, crash test, GUI start
  cli.py           argument parsing, --self-check build verification, report output
  core/            per-user paths, UI preferences, account profiles, credentials, instance lock
  domain/          pure logic: operating modes, validated defaults (no Qt, MT5 or I/O)
  mt5/             the MT5 gateway thread, connection checklist, diagnostics, smoke test (phase 3)
  observability/   logging, trace context, masking, crash reports, watchdog (phase 2)
  ui/              Qt: theme tokens, navigation, command palette, pages, Logs, Connection, crash dialog
tests/
  fakes/           FakeMT5 for tests only (never shipped)
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
