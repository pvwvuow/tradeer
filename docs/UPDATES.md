# In-app updates and releases

Spec Part J (planned as Phase 15) was done early, before the UI rework and Phase 13, so every
build reaches the PC through the app itself. This file holds the decisions (ADRs 113 to 117),
the rules for new code and the status; `ARCHITECTURE.md` and `PROGRESS.md` point here.

## Decisions
113. **In-app updates with Velopack, not a silent Inno Setup installer (spec J1).** J1 asks to evaluate Velopack first. It fits: the PyInstaller one-folder build is packed by `vpk pack` into a full package and a delta package (zstd binary patches of every changed file), a per-user `Setup.exe` (no admin rights, `%LocalAppData%\MT5TradingWorkstation`) and a portable zip. The app asks GitHub for the newest release, downloads the deltas between the installed and the newest version (or the full package when that is smaller), rebuilds and SHA-256-checks the package, and swaps it in after the app exits. A code change downloads a few MB instead of the whole build. Inno Setup and its script are gone; data stays in `%APPDATA%` as before. `velopack==1.2.0` is the only new dependency and `vpk 1.2.0` (a .NET tool) runs only in the release job. Only `updates/velopack_backend.py` and `updates/bootstrap.py` load it (architecture test).
114. **The update service runs in its own thread and never decides alone to restart.** It checks 60 s after start and then every N hours (default 6), downloads a non-major version in the background (setting), and never a major one without a click. A failed automatic check is a log line only; a manual one says why. Applying is always one click ("Restart to update") with a warning when trades are open: their SL/TP stay at the broker while the app restarts (J2.5). Every step goes to the `update` log category; installs and rollbacks are also audited.
115. **Launch history for rollback (J4).** `update_state.json` (one per install, next to the profiles) records the current and previous version and the last launches. A launch is healthy after 10 minutes or a normal close; two unhealthy launches of a version in a row offer "Roll back to vPrevious". A frozen build that fails its start-up self-check after an update offers the rollback before anything connects to MT5 (J2.6). A rollback reads the older release's own feed with downgrades allowed and installs its full package.
116. **Releases from `main`, merged by the agent.** Since 3 October 2026 the owner lets the agent merge into `main` once CI is green and publish every build (this replaces "you never merge" in spec H4). Flow: PR into `main`, CI green, merge; release-please updates its release PR; the agent pushes a commit to that PR so CI runs (PRs made with the default token start no workflows), merges it after CI is green; release-please tags the release and `scripts/ci/release.ps1` builds, packs with `vpk` (delta from the previous release's full package, downloaded with `gh`) and uploads to the release with `vpk upload github --merge`.
117. **The first release is 0.12.0** (`release-as` in `release-please-config.json`, removed again afterwards): it holds Phases 1 to 12 plus the updater, and after it release-please numbers versions from the commit messages.

## Rules for new code

- Only `app/updates/velopack_backend.py` and `app/updates/bootstrap.py` load `velopack`; the service sees the `UpdateBackend` protocol only.
- `run_velopack_hooks()` stays right after `multiprocessing.freeze_support()` in the entry points.
- Never restart or apply an update without the user's click; never download a major version by itself.
- Keep the `vpk` version in `scripts/ci/release.ps1` equal to the `velopack` pin.

## Status: in-app updates and the first release (version 0.12.0, branch `phase/15-updates`)

Status: built and tested with unit tests (versions, settings, launch history and crash-loop detection, the update service from check to apply, rollback, the Velopack adapter on a fake library) and Qt tests (Settings > Updates, the banner, Restart to update); the release job packs with Velopack. The owner decided on 3 October 2026 that the agent merges into `main` and publishes every build. Not yet run on your PC.

### What was built

- **Velopack** (ADR 113): the release job turns the build into `MT5TradingWorkstation-win-Setup.exe` (per-user install), a full package, a delta package from the previous release and `releases.win.json`, uploaded to the GitHub release. Inno Setup is gone.
- **Update service** (`app/updates/`): checks a minute after start and every 6 hours, downloads a non-major version in the background (only the changed parts), says "ready" in a banner and on Settings > Updates, and installs on **Restart to update** (one click, one restart). Major versions need a click to download. Pause updates, automatic checks and the interval are settings.
- **Safety** (J2, J4): a warning when trades are open; a rollback to the previous version, offered by itself when a new version ended early twice or fails its start-up self-check; every step logged in the `update` category, installs and rollbacks audited.
- **Merge of the phase stack into `main`**, the newer tool pins from `main` (ruff 0.16.9, mypy 2.3.1, pytest 9.1.1, numpy 2.4.6), and `release-as: 0.12.0` for the first release.
- ADRs 113 to 117 above.

### Checklist

- ✓ Service: background download of a minor version, never of a major one, quiet automatic failures, loud manual ones, a failed download is never "ready", apply hands over the downloaded version (`tests/unit/test_updates.py`)
- ✓ Launch history: an update is noticed once, two early ends in a row offer the rollback, a damaged file is ignored
- ✓ Velopack adapter: delta size, GitHub source or the latest-release fallback, rollback feed with downgrade, outside an install it explains itself
- ✓ Settings > Updates, the banner and Restart to update (Qt tests, CI)
- ✓ Architecture test: only the update modules load velopack; the update code never trades or imports Qt
- ✗ **Not yet run on your PC**: the acceptance check (an installed old version finds a newer release and updates itself with one click) needs the second release
- ✗ Releases are not code-signed, so Windows SmartScreen warns on the first install

### Test on your PC

1. Download `MT5TradingWorkstation-win-Setup.exe` from the 0.12.0 release and run it. The app starts by itself; Settings > Updates says "Installed version: 0.12.0".
2. Click **Check now**: "You have the newest version (0.12.0)".
3. When 0.13.0 is published (the UI rework), keep the app open: within a minute of starting (or after Check now) the bar says "Version 0.13.0 is ready". Click **Restart to update**: the app closes, updates and starts again as 0.13.0. Settings > Updates then says "Updated from 0.12.0 to 0.13.0".
4. Send `logs/update/` and `all.log` if anything fails.

