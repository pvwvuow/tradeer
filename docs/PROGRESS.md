# Progress

Continue in a new chat with: "Read docs/SPEC.md and docs/PROGRESS.md and continue."

## Completed phases

- Phase 1: Foundation, merged into `main` through pull request #1 on 2026-10-01.

## Phase 1: Foundation (pull request #1, merged)

Built: Python 3.11 project with exact pins, ruff, mypy (strict), pytest, pytest-qt, pre-commit; the desktop shell (Simple view by default, Advanced view with the grouped sidebar and 14 pages that name their phase); design tokens with a generated stylesheet, dark and light theme; status bar with the `PAPER` badge and the kill switch (inactive); command palette (Ctrl+K); `--self-check`, `--report-file`, `--profile`, `--version`; build, release and installer scripts; agent rules; the full specification in `docs/SPEC.md`.

Open items: `main` already has a basic `.github/workflows/ci.yml` (ruff, Linux tests, Windows smoke and exe artifact), added by another agent. The full pipeline from this project (mypy, frozen self-check and crash test, installer, CodeQL, release-please) is staged in `ci/workflows/` and still has to be merged into `.github/workflows/` (README, "Turn on CI").

## Current phase: 2 Observability (branch `phase/02-observability`, pull request into `main`)

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

- ✓ A forced exception produces a crash report: unit tests (thread hook, sys hook) and the `--crash-test` logic pass in the assistant's sandbox (Python 3.12). ✗ Not yet run with the real loguru pipeline, Qt or on Windows: `tests/integration` and `tests/ui` need CI.
- ✓ Secrets are masked: unit tests cover token formats, key/value pairs, URL passwords, registered values, structured fields, stack traces and crash reports; a negative test proves the crash test fails when masking is broken. ✗ Same CI caveat for the real pipeline.
- ✓ Unit tests: 95 passed, 1 skipped (Windows-only) in the sandbox.
- ✗ Ruff, mypy, the integration tests and the Qt tests have not run yet: the sandbox has no ruff, mypy, loguru or PySide6, and no internet access to install them. The code follows the configured style by hand. CI is their first real run.
- ✗ CI green: not known yet. The basic `ci.yml` on `main` runs ruff and the tests on this pull request; the frozen crash test runs only once the staged `build` job is in `.github/workflows/`.

## Known issues

- Workflow files cannot be pushed by the assistant's GitHub connection (it lacks the `workflow` permission). They are staged in `ci/workflows/`.
- No lock file yet; direct dependencies are pinned exactly.
- Two app instances on the same profile would write to the same log files. Single-instance protection arrives with the MT5 connection.
- Health checks, performance metrics, the debug bundle and the full Logs page (trace timeline, time filters, export) are Phase 14 items.

## Next steps

1. Let CI run on the Phase 2 pull request and fix anything it reports on the same branch.
2. Merge the staged workflows from `ci/workflows/` into `.github/workflows/`.
3. Review and merge Phase 2.
4. Phase 3: the real MT5 connection (gateway thread, test-connection checklist, profiles, investor mode, diagnostics, `--mt5-smoke-test`).
