# Architecture

## Layers

```
app/
  __version__.py   single source of the version (bumped by release-please)
  main.py          composition root: CLI, self-check, crash test, GUI start
  cli.py           argument parsing, --self-check build verification, report output
  core/            per-user paths and persisted UI preferences
  domain/          pure logic: operating modes, validated defaults (no Qt, MT5 or I/O)
  observability/   logging, trace context, masking, crash reports, watchdog (phase 2)
  ui/              Qt: theme tokens, navigation, command palette, pages, Logs page, crash dialog
tests/
  unit/            pure tests (no Qt, no loguru)
  integration/     the real loguru pipeline and the end-to-end crash test
  ui/              pytest-qt smoke tests (headless, QT_QPA_PLATFORM=offscreen)
scripts/ci/        check, build and release scripts used by the workflows
ci/workflows/      staged GitHub Actions workflows (move to .github/workflows/)
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

## Logging rules for new code

- `log = get_logger(LogCategory.X)` at module level. `print()` is only for CLI reports.
- Dynamic text goes in as an argument, never into the format string: `log.info("Order sent: {}", text)`. A brace inside a format string with arguments breaks the call.
- Extra fields: `log.bind(symbol="EURUSD").info(...)`. Fields named like `password`, `token`, `api_key` or `pin` are masked automatically.
- Register every secret the moment it is read: `MASKER.register(value)`.
- Wrap work for one signal in `with trace(signal_id=..., symbol=...):`, and pass work to other threads with `propagate(fn)`.
- Setting changes go through `audit(action, before=..., after=...)`.
