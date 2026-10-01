# Architecture

## Layers

```
app/
  __version__.py   single source of the version (bumped by release-please)
  main.py          composition root: CLI, self-check, GUI start
  cli.py           argument parsing and the --self-check build verification
  core/            per-user paths and persisted UI preferences
  domain/          pure logic: operating modes, validated defaults (no Qt, MT5 or I/O)
  ui/              Qt: theme tokens, navigation registry, command palette, pages, main window
tests/
  unit/            pure tests (no Qt)
  ui/              pytest-qt smoke tests (headless, QT_QPA_PLATFORM=offscreen)
scripts/ci/        check, build and release scripts used by the workflows
ci/workflows/      staged GitHub Actions workflows (move to .github/workflows/)
installer/         Inno Setup script (per-user install, no admin rights)
```

The target layout for later phases is in `docs/SPEC.md` (D2).

## Data flow in Phase 1

`main.py` parses arguments. `--self-check` imports MetaTrader5 and Qt and exits 0 or 1. Otherwise it loads `UiPrefs` for the profile, builds `MainWindow`, and applies the theme generated from the design tokens. UI actions (view switch, theme switch) write `ui_prefs.json` atomically.

## Decisions (ADR)

1. **Design tokens in one file, QSS generated** (`app/ui/theme.py`). Reason: one source of truth for colors, sizes and radii; themes are testable without Qt. Contrast is unit-tested against WCAG AA (4.5:1). The dark accent button uses dark text because white on `#5B8CFF` is only about 3.2:1.
2. **Pure helpers for UI logic** (`navigation.py`, `commands.py`). Reason: page order, grouping and command ranking are tested without a display.
3. **UI preferences as JSON per profile in `%APPDATA%`**, not the registry. Reason: portable, inspectable, easy to back up, safe defaults on corruption.
4. **`--self-check` plus a frozen-build guard.** Reason: spec I2 requires proof that the built exe can import MetaTrader5, and a production build must fail loudly instead of falling back to fake data. Windowed builds have no console, so the report can also be written to a file.
5. **Thin workflows that call `scripts/ci/*.ps1`.** Reason: some agent connections cannot modify `.github/workflows/`; keeping logic in scripts means the pipeline can still be maintained, and it removes duplication between CI and release.
6. **release-please and the release build in one workflow.** Reason: tags and pull requests created with the default `GITHUB_TOKEN` do not trigger other workflows, so a separate `on: push: tags` release workflow would never run after release-please. An optional `RELEASE_PLEASE_TOKEN` secret also lets CI run on release PRs.
7. **CI and build in one workflow file (`ci.yml`, jobs `ci` and `build`).** Reason: fewer workflow files to maintain; `ci` stays the required status check.
8. **Dependencies are added in the phase that needs them, pinned exactly.** Reason: faster, smaller CI and a smaller attack surface; the ML stack is not installed before Phase 11. A full lock file is generated when `uv` is introduced.
9. **Per-user installer (`PrivilegesRequired=lowest`).** Reason: the app must run as the same Windows user and privilege level as the MT5 terminal; mismatches are a common cause of `initialize()` failures.
