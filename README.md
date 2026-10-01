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

## Turn on CI

`main` already has a basic `.github/workflows/ci.yml` (ruff, Linux tests, a Windows smoke test and an exe artifact). The full pipeline is staged in `ci/workflows/`: `ci.yml` adds mypy and a `build` job (PyInstaller app, frozen `--self-check` and `--crash-test`), plus `codeql.yml` and `release.yml`. The assistant's GitHub connection cannot write `.github/workflows/`, so whoever maintains the workflows merges them by hand:

1. Open the repository on GitHub, switch to the branch, and press the `.` key. GitHub opens a web editor.
2. Copy the staged files from `ci/workflows/` into `.github/workflows/` (merge `ci.yml` with the existing one rather than keeping two CI files).
3. Open the Source Control panel on the left, type `ci: enable the full pipeline`, and click **Commit & Push**.

The build can then be downloaded from the pull request under Checks, then Artifacts.

## Protect the main branch

1. GitHub repository, then **Settings**, then **Branches**, then **Add branch ruleset** (or "Add rule").
2. Target branch: `main`.
3. Enable: **Require a pull request before merging**, **Require status checks to pass** (choose `ci`), **Block force pushes**, **Restrict deletions**.
4. Save. Every phase arrives as a pull request that you review and merge yourself.

## Releases

Releases are automated by release-please once CI is on:

1. Settings, then Actions, then General, then Workflow permissions: enable **Allow GitHub Actions to create and approve pull requests**.
2. Optional but recommended: add a repository secret `RELEASE_PLEASE_TOKEN` (a fine-grained token with Contents and Pull requests read/write on this repository). Without it, release pull requests do not trigger CI and need an admin merge.
3. Merging a phase into `main` updates a "Release PR". Merging that Release PR creates the tag `vX.Y.Z`, builds the installer, and attaches the installer, a portable zip, `checksums.txt` and `latest.json` to the GitHub Release.

## Safety notes

- Paper mode is the default. Real accounts and Auto mode will require typed confirmation and the Go-Live gate.
- Every live order will carry a server-side stop loss. No martingale, grid or averaging down, ever.
- Never paste passwords or tokens into chats, issues or commits.
