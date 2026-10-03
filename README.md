# MT5 Trading Workstation

A Windows desktop app that will connect to **your own** MetaTrader 5 terminal, analyze the market, and later, behind strict safety gates, help you trade with risk limits that cannot be bypassed.

> **Honest expectations.** This app is infrastructure, not an edge. It does not find or guarantee a profitable strategy. Any win probability it shows is an uncertain estimate with a sample size and a confidence interval. Paper (practice) mode is the default.

## Status

Phase 11 of 16 (ML win probability). The Model page trains a LightGBM model on every signal the strategies made on MT5 history, validates it with purged walk-forward tests against the baseline (the strategy's own win rate), calibrates it, and lets you use it only when it beats the baseline out of sample; signals then show the estimate with its range and the three factors behind it. The honest checklist is in [docs/PROGRESS.md](docs/PROGRESS.md).

From Phase 10 (Backtesting): the Backtest page and `--backtest` replay the strategies on MT5 history with the same code that trades live (entries at the next bar open, the same-candle rule, costs), with walk-forward, Monte-Carlo and a sensitivity heatmap; a test proves the backtest gives the same signals as live on the same data.

From Phase 8 (Execution): approved signals are placed with stop loss and take profit (Paper by default, Semi-auto for real orders after your approval), managed, recovered after a restart and synced back; a kill switch stops everything.

From Phase 5 to 7: the Market page with analysis cards and charts, two strategies with signals, and risk management with limits that cannot be bypassed.

From Phase 4 (Storage): everything the app records goes to a local SQLite database first, with optional cloud sync to your own Supabase project and an import of your MT5 trade history.

From Phase 3 (MT5 connection): the app connects to the MetaTrader 5 terminal on your PC through one gateway thread, runs a Test-connection checklist with real values, saves account profiles (passwords in Windows Credential Manager), switches to Analysis-only with an investor password, and ships Connection Diagnostics and a read-only `--mt5-smoke-test`. It writes structured, masked logs and crash reports.

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

## How the MT5 connection works

The official `MetaTrader5` Python package talks to the **MT5 terminal installed on the same Windows PC**; it does not connect to the broker directly. So:

- MT5 must be installed, running (the app can start it) and logged in, with **Algo Trading** on.
- Run MT5 and the app as the same Windows user and at the same privilege level (both normal, or both "Run as administrator").
- Run **one app instance per account**, never two on the same account. For several accounts at once, use one portable MT5 terminal and one app profile (`--profile NAME`) per account.
- Keep the PC awake while the app runs: Windows Settings, System, Power, set "Sleep" to Never when plugged in, and schedule Windows updates outside trading hours (Windows Update, Advanced options, Active hours).
- For 24/7 running use a Windows VPS close to your broker's server.

Check a build against your account in 30 seconds (read-only, never trades):

```powershell
python -m app --mt5-smoke-test
```

## Logs and crash reports

- Logs: `%APPDATA%\MT5TradingWorkstation\profiles\<profile>\logs`. One folder per category with one `.jsonl` file per day, plus a readable `all.log`. Old files are zipped, deleted after 30 days, and the folder never grows past 500 MB.
- Crash reports: `...\profiles\<profile>\crash_reports\crash_<time>.json`.
- Passwords, keys and tokens are masked before anything is written.
- To prove crash reporting works on your PC, run:

```powershell
python -m app --crash-test
```

It crashes a background thread on purpose and prints `Result: PASS` when a crash report was written and no secret leaked into the report or the logs. The built app supports the same flag: `MT5TradingWorkstation.exe --crash-test --report-file crash-test.txt`.

## Storage and cloud sync

- Everything is written to a local SQLite database first (`profiles/<profile>/data/workstation.db`), so the app works offline.
- Rows waiting for the cloud sit in an outbox and upload in the background. Going offline never loses or duplicates them.
- Cloud sync is optional and uses your own free Supabase project with Row Level Security. Setup steps: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
- The app talks to Supabase with httpx (PostgREST and Supabase Auth), not supabase-py. Why: ADR 30 in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
- Account passwords are kept in Windows Credential Manager, never in files or logs.
- Never paste passwords or tokens into chats, issues or commits.
