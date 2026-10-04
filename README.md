# MT5 Trading Workstation

A Windows desktop app that connects to **your own** MetaTrader 5 terminal, analyzes the market, suggests trades with a full explanation, and places them only within risk limits that cannot be bypassed.

> **Honest expectations.** This app is infrastructure, not an edge. It does not find or guarantee a profitable strategy. Any win probability it shows is an uncertain estimate with a sample size and a range. Paper (practice) mode is the default, and the two built-in strategies are examples that are not proven to be profitable.

## Install

1. Download `MT5TradingWorkstation-win-Setup.exe` from the [latest release](https://github.com/pvwvuow/tradeer/releases/latest) and run it. It installs for your Windows user only (no admin rights) and starts the app.
2. The installer is not code-signed yet, so Windows may say "Windows protected your PC": click **More info**, then **Run anyway**.
3. After that the app updates itself: Settings > Updates, or the banner at the top. Only the changed parts are downloaded, and it always asks before it restarts.

Every build is installed with this Setup.exe on a clean Windows machine in CI and must pass its self-check there before it can be merged. Install, update, uninstall and moving to a new PC: [docs/USER_GUIDE.md](docs/USER_GUIDE.md), "Install, update and uninstall".

## What it does

- **Simple view** (default): one trade suggestion at a time in plain words, with what you could lose and make in money, Approve and Skip, your open trades and a Stop button. In English or Persian (right to left).
- **Advanced view**: 14 pages in three groups.
  - Trade: Dashboard, Market (analysis cards, chart, calendar), Signals (every signal with its full decision trace), Positions & Trades.
  - Analyze: Analytics, Journal, Backtest (walk-forward, Monte-Carlo, sensitivity), Model (a LightGBM win probability that is used only when it beats the baseline), AI Lab (export your results for an AI, check its suggestion with a backtest).
  - System: Strategies (with the Go-Live checklist), Risk, Logs, Health, Settings.
- **Trading modes**: Paper (default), Semi-auto (real orders after your approval), Auto (only after the Go-Live checklist and a typed confirmation) and Analysis-only. Every order carries a server-side stop loss. A kill switch (Ctrl+Shift+K) closes the bot's trades.
- **Data**: a local SQLite database, optional one-way sync to your own Supabase project, import of your MT5 trade history, daily and weekly reports, Windows notifications and an optional Telegram bot with a PIN.
- **Health**: health checks every minute, performance budgets, a debug bundle and a 24-hour soak report.
- **Accessibility**: dark and light themes with WCAG AA contrast, gains and losses never shown by color alone, full keyboard use and sharp High-DPI scaling.

## Documentation

- Daily use, every page and every command-line option: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- Status, what is checked and what still needs your PC: [docs/PROGRESS.md](docs/PROGRESS.md)
- Full specification: [docs/SPEC.md](docs/SPEC.md)
- Architecture and decisions (ADRs): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Topics: [updates](docs/UPDATES.md), [Go-Live and Auto](docs/GO_LIVE.md), [AI Lab](docs/AI_LOOP.md), [Ask AI](docs/LLM.md), [health](docs/HEALTH.md), [Persian](docs/I18N.md), [accessibility](docs/ACCESSIBILITY.md), [design system](docs/UI.md)

## How the MT5 connection works

The official `MetaTrader5` Python package talks to the **MT5 terminal installed on the same Windows PC**; it does not connect to the broker directly. So:

- MT5 (64-bit) must be installed, running (the app can start it) and logged in, with **Algo Trading** on.
- Run MT5 and the app as the same Windows user and at the same privilege level (both normal, or both "Run as administrator").
- Run **one app instance per account**, never two on the same account. For several accounts at once, use one portable MT5 terminal and one app profile (`--profile NAME`) per account.
- Keep the PC awake while the app runs: Windows Settings, System, Power, set "Sleep" to Never when plugged in, and schedule Windows updates outside trading hours.
- For 24/7 running use a Windows VPS close to your broker's server.

Check a build against your account in 30 seconds (read-only, never trades):

```powershell
MT5TradingWorkstation.exe --mt5-smoke-test --report-file smoke.txt
```

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

`--self-check` prints `Result: PASS` when Python, MetaTrader5, Qt, the MT5 helper process and the ML library load correctly. From source the Persian font is not bundled (the build downloads it), so Persian falls back to Segoe UI.

## Logs and crash reports

- Logs: `%APPDATA%\MT5TradingWorkstation\profiles\<profile>\logs`. One folder per category with one `.jsonl` file per day, plus a readable `all.log`. Old files are zipped, deleted after 30 days, and the folder never grows past 500 MB.
- Crash reports: `...\profiles\<profile>\crash_reports\crash_<time>.json`.
- Passwords, keys and tokens are masked before anything is written. The debug bundle (Health page) also hides your login, name, Windows user name and Telegram chat ids.
- To prove crash reporting works on your PC: `MT5TradingWorkstation.exe --crash-test --report-file crash-test.txt` prints `Result: PASS` when a crash report was written and no secret leaked.

## Storage and cloud sync

- Everything is written to a local SQLite database first (`profiles/<profile>/data/workstation.db`), so the app works offline.
- Rows waiting for the cloud sit in an outbox and upload in the background. Going offline never loses or duplicates them.
- Cloud sync is optional and uses your own free Supabase project with Row Level Security. Setup steps: [docs/USER_GUIDE.md](docs/USER_GUIDE.md), "Cloud sync with Supabase".
- The app talks to Supabase with httpx (PostgREST and Supabase Auth), not supabase-py. Why: ADR 30 in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run the checks

```powershell
ruff check .
ruff format --check .
mypy
pytest
```

## CI

`.github/workflows/` contains three workflows; the logic lives in `scripts/ci/*.ps1`:

- **CI** (`ci.yml`), on every push to `main` and every pull request:
  - `Tests (Linux, Qt offscreen)`: pytest with `QT_QPA_PLATFORM=offscreen`.
  - `ci`: Windows, `scripts/ci/check.ps1`: `ruff check`, `ruff format --check`, `mypy` (strict) and `pytest` with coverage.
  - `build`: Windows, `scripts/ci/build.ps1`: the one-folder PyInstaller app with the bundled Persian font (checked by its SHA-256), the frozen `--self-check` and `--crash-test`, the portable zip, then `scripts/ci/install-test.ps1`: packs it with Velopack, installs it with `Setup.exe --silent` on the clean runner and runs the installed app's `--self-check`.
- **codeql** (`codeql.yml`): Python security analysis on `main`, pull requests and a weekly schedule.
- **release** (`release.yml`): release-please keeps a release pull request up to date; merging it tags `vX.Y.Z`, and the `publish` job runs `scripts/ci/release.ps1`, which packs the build with Velopack and attaches `MT5TradingWorkstation-win-Setup.exe`, the full and delta packages, `releases.win.json`, `RELEASES`, a portable zip, `checksums.txt` and `latest.json` to the GitHub release.

## Protect the main branch

A branch ruleset on `main` requires a pull request, the status checks `ci`, `build` and `Tests (Linux, Qt offscreen)`, and blocks force pushes and deletions. Every build arrives as a pull request into `main`. Since 3 October 2026 the agent merges it once CI is green and publishes the release (owner's decision).

## Releases

Releases are automated by release-please: merging into `main` updates the release pull request, and merging that creates the tag and the GitHub release, where the installed apps find the update. Release pull requests are opened with the workflow token, so CI starts on them only after one more commit on their branch (or with a `RELEASE_PLEASE_TOKEN` repository secret).

## Safety notes

- Paper mode is the default. Real orders need Semi-auto (typed REAL on a real account) or Auto (the Go-Live checklist, typed AUTO).
- Every live order carries a server-side stop loss. No martingale, grid or averaging down, ever.
- Account passwords, the Telegram token and the AI key are kept in Windows Credential Manager, never in files or logs.
- Never paste passwords or tokens into chats, issues or commits.
