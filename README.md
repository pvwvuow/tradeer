# MT5 Trading Workstation

A Windows desktop app that will connect to **your own** MetaTrader 5 terminal, analyze the market, and later, behind strict safety gates, help you trade with risk limits that cannot be bypassed.

> **Honest expectations.** This app is infrastructure, not an edge. It does not find or guarantee a profitable strategy. Any win probability it shows is an uncertain estimate with a sample size and a confidence interval. Paper (practice) mode is the default.

## Status

Phase 2 of 16 (Observability). The app opens with a Simple view and an Advanced view, a dark and a light theme, a grouped sidebar, an always-visible status bar and a command palette (Ctrl+K). It writes structured, masked logs, saves a crash report for every unexpected error, and has a Logs page. It does **not** connect to MetaTrader 5 and it never places orders yet.

- Full specification: `docs/SPEC.md`
- Progress and next steps: `docs/PROGRESS.md`
- Architecture and decisions: `docs/ARCHITECTURE.md`
- Daily use: `docs/USER_GUIDE.md`

## Try it from source (Windows)

1. Install **Python 3.11, 64-bit** from python.org. During setup tick "Add python.exe to PATH".
2. Open PowerShell in the project folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m app --self-check
python -m app
```

`--self-check` prints `Result: PASS` when Python, MetaTrader5 and Qt load correctly.

## Logs and crash reports

- Logs: `%APPDATA%\MT5TradingWorkstation\profiles\<profile>\logs`. One folder per category with one `.jsonl` file per day, plus a readable `all.log`. Old files are zipped, deleted after 30 days, and the folder never grows past 500 MB.
- Crash reports: `...\profiles\<profile>\crash_reports\crash_<time>.json`.
- Passwords, keys and tokens are masked before anything is written.
- To prove crash reporting works on your PC, run:

```powershell
python -m app --crash-test
```

It crashes a background thread on purpose and prints `Result: PASS` when a crash report was written and no secret leaked into the report or the logs. The built app supports the same flag: `MT5TradingWorkstation.exe --crash-test --report-file crash-test.txt`.

## Run the checks

```powershell
ruff check .
ruff format --check .
mypy
pytest
```

## CI

CI is on. `.github/workflows/` contains four workflows:

- **CI** (`ci.yml`) — every push to `main` and every pull request:
  - `Tests (Linux, Qt offscreen)`: fast pytest run with `QT_QPA_PLATFORM=offscreen`.
  - `ci`: Windows job running `scripts/ci/check.ps1` — install, `ruff check`, `ruff format --check`, `mypy` (strict) and `pytest` with coverage; uploads `coverage.xml` as an artifact.
  - `build`: pull requests only — Windows job running `scripts/ci/build.ps1`, which builds the one-folder PyInstaller app, runs the frozen `--self-check`, zips it and uploads the portable build artifact (download it from the pull request under Checks, then Artifacts).
- **codeql** (`codeql.yml`) — Python security analysis on `main`, pull requests and a weekly schedule.
- **release** (`release.yml`) — release-please keeps a Release PR up to date; merging it tags `vX.Y.Z` and the `publish` job runs `scripts/ci/release.ps1` to attach the installer, portable zip, `checksums.txt` and `latest.json` to the GitHub Release.

The workflow YAML stays thin; all CI logic lives in `scripts/ci/*.ps1`.

## Protect the main branch

A branch ruleset on `main` enforces this (configured in repository settings):

1. **Require a pull request before merging**.
2. **Require status checks to pass**: `ci`, `build` and `Tests (Linux, Qt offscreen)`.
3. **Block force pushes** and **restrict deletions**.

Every phase arrives as a pull request that you review and merge yourself.

## Releases

Releases are automated by release-please:

1. Settings, then Actions, then General, then Workflow permissions: enable **Allow GitHub Actions to create and approve pull requests** (done in repository settings).
2. Optional but recommended: add a repository secret `RELEASE_PLEASE_TOKEN` (a fine-grained token with Contents and Pull requests read/write on this repository). Without it, release pull requests do not trigger CI and need an admin merge.
3. Merging a phase into `main` updates a "Release PR". Merging that Release PR creates the tag `vX.Y.Z`, builds the installer, and attaches the installer, a portable zip, `checksums.txt` and `latest.json` to the GitHub Release.

## Safety notes

- Paper mode is the default. Real accounts and Auto mode will require typed confirmation and the Go-Live gate.
- Every live order will carry a server-side stop loss. No martingale, grid or averaging down, ever.
- Never paste passwords or tokens into chats, issues or commits.
