# Progress

Continue in a new chat with: "Read docs/SPEC.md and docs/PROGRESS.md and continue."

## Completed phases

None merged yet.

## Current phase: 1 Foundation (pull request #1, branch `phase/01-foundation`)

Built:

- Python 3.11 project (`pyproject.toml`) with exact pins, ruff, mypy (strict), pytest, pytest-qt, pre-commit.
- Desktop shell: Simple view (default) and Advanced view with the grouped sidebar (Trade, Analyze, System) and 14 empty pages that name their delivery phase.
- Design tokens with a generated stylesheet, dark (default) and light theme, WCAG AA contrast tests.
- Status bar with connection state, the `PAPER` mode badge, bot state, version and the kill switch (inactive until execution exists).
- Command palette (Ctrl+K, Advanced only) with a tested fuzzy filter.
- `--self-check`, `--report-file`, `--profile`, `--version`; frozen builds refuse to start without MetaTrader5.
- Paper-first validated defaults (spec B4) and operating modes (spec B3).
- Build and release scripts, Inno Setup installer, release-please configuration, Dependabot, PR and issue templates, agent rules, security policy.

Acceptance checklist (spec G3 phase 1):

- ✓ App launches: covered by pytest-qt smoke tests (`tests/ui`). ✗ Not yet run in CI.
- ✓ Theme switches and is remembered: covered by tests. ✗ Not yet run in CI.
- ✓ Unit tests pass (32 passed in the assistant's sandbox with Python 3.12).
- ✗ Ruff, mypy and the Qt tests have not run yet: the assistant's sandbox has no internet access to install them. CI is their first real run.
- CI enabled: the workflows now live in `.github/workflows/` (CI, codeql, release). Windows jobs run `scripts/ci/check.ps1` (ruff check, ruff format --check, mypy strict, pytest with coverage) and `scripts/ci/build.ps1` (PyInstaller + frozen self-check + portable zip).
- ✗ Build artifact downloads and runs: depends on CI.

## Known issues

- Resolved: the staged `ci/workflows/` folder is gone; `.github/workflows/` is maintained by the CI maintainer agent, so Brain does not need the `workflow` permission.
- No lock file yet; direct dependencies are pinned exactly.

## Next steps

1. ~~Move the workflows~~ Done: CI is enabled and green (see the full-pipeline PR).
2. Review and merge pull request #1.
3. Phase 2: observability (logging, categories, trace ids, masking, crash handler, watchdog, Logs page).
