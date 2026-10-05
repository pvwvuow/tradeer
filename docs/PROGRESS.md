# Progress

Continue in a new chat with: "Read docs/SPEC.md and docs/PROGRESS.md and continue."

## Status

All 16 phases of the specification (G3) are built. Every phase arrived as a pull request into `main`, was merged with CI green and published as a GitHub release that the installed app updates to by itself. What is still open is testing on your PC (see "Test on your PC"): CI cannot run MT5, a real market or 24 hours of your account.

The detailed notes of phases 1 to 12 (what was built, the checklists and the fixes from your PC logs) are kept in the [Phase 1 to 12 progress log](https://github.com/pvwvuow/tradeer/blob/5dec0593b80adf2b60d871c73a7e2b96d04c3bb8/docs/PROGRESS.md). Each later phase has its own document, linked below.

## Phases and releases

| Phase | What | Release | Details |
| --- | --- | --- | --- |
| 1 | Foundation: project, shell, themes, command palette, `--self-check` | (main) | [log](https://github.com/pvwvuow/tradeer/blob/5dec0593b80adf2b60d871c73a7e2b96d04c3bb8/docs/PROGRESS.md) |
| 2 | Observability: structured masked logs, crash reports, watchdog | 0.12.0 | same log |
| 3 | MT5 connection: gateway, checklist, diagnostics, profiles | 0.12.0 | same log |
| 4 | Storage: SQLite, outbox, Supabase sync, history import | 0.12.0 | same log |
| 5 | Market data and analysis: cards, chart, calendar | 0.12.0 | same log |
| 6 | Strategies and signals with decision traces | 0.12.0 | same log |
| 7 | Risk: sizing with `order_calc_profit`, limits that survive a restart | 0.12.0 | same log |
| 8 | Execution: paper and live brokers, recovery, kill switch | 0.12.0 | same log |
| 9 | Simple mode: the plain-language Home screen | 0.12.0 | same log |
| 10 | Backtesting: same code as live, walk-forward, Monte-Carlo | 0.12.0 | same log |
| 11 | ML win probability with the baseline rule | 0.12.0 | same log |
| 12 | Analytics, journal, reports, notifications, Telegram | 0.12.0 | same log |
| 15 | In-app delta updates (Velopack), built early | 0.12.0 | [UPDATES.md](UPDATES.md) |
| UI | Design system rework | 0.13.0 | [UI.md](UI.md) |
| 13a | AI Lab: export, check, compare, activate in Paper | 0.14.0 | [AI_LOOP.md](AI_LOOP.md) |
| 13b | Go-Live checklist and Auto mode | 0.15.0 | [GO_LIVE.md](GO_LIVE.md) |
| 13c | Optional Ask AI (OpenAI-compatible) | 0.16.0 | [LLM.md](LLM.md) |
| 14a | Health checks and the Health page | 0.17.0 | [HEALTH.md](HEALTH.md) |
| 14b | Performance metrics and the debug bundle | 0.18.0, 0.18.1 | [HEALTH.md](HEALTH.md) |
| 14c | Soak report and the full Logs page | 0.19.0 | [HEALTH.md](HEALTH.md) |
| 16a | Persian and right to left (Simple view) | 0.20.0 | [I18N.md](I18N.md) |
| 16b | Accessibility and light theme polish | 0.21.0 | [ACCESSIBILITY.md](ACCESSIBILITY.md) |
| 16c | Clean-install test, README and user guide | 0.22.0 | this page |
| fix | Your PC log of 5 October: weekly open, reconnect notice, Ask AI | 0.22.1 | this page |
| fix | Your PC log of 5 October, morning: breakout orders, quiet backtests, CPU budget | 0.22.2 | this page |
| fix | Your PC log of 5 October, London open: unanswered order checks, failed-order alerts, Auto asks no approvals | 0.22.3 | this page |

## Phase 16c: clean install, README and user guide

- **Clean-install test** (`scripts/ci/install-test.ps1`, run by `scripts/ci/build.ps1` on every pull request and release): packs the build with Velopack, installs it with `MT5TradingWorkstation-win-Setup.exe --silent` on GitHub's Windows runner (a new virtual machine for every job, nothing installed before, no admin rights used) and runs the installed app's `--self-check`. It also checks that the installed copy has `Update.exe` and the Persian font, and reports the Start menu shortcut and the "Installed apps" entry.
- `scripts/ci/vpk.ps1`: installs the Velopack CLI once per machine, so the build and the release can both use it in one job.
- **README** rewritten for the finished app: install, what it does, documentation map, MT5 notes, CI.
- **User guide** covers every page and feature up to 0.21: install, update, uninstall and moving to a new PC, the window, keyboard and accessibility, Settings, the Go-Live checklist and Auto, AI Lab and Ask AI, the Health page (checks, workers, performance, debug bundle, soak report), the Logs page, where every file lives and every command-line option.
- `tests/unit/test_docs.py` keeps the documents true: every command-line option of `app/cli.py` and every page of the sidebar is in the user guide, and every relative link in the README, the user guide and this page points to a file that exists.

### Checklist

- ✓ The installer installs and the installed app passes its self-check on a clean Windows machine (CI `build` job, on this pull request and every later one)
- ✓ Full test suite on Linux and Windows (`Tests (Linux, Qt offscreen)`, `ci`), `ruff`, `ruff format`, `mypy` strict
- ✓ The documents name every option and page, and their links work (`tests/unit/test_docs.py`)
- ✗ **A clean install on your own fresh PC** (see "Test on your PC", step 1)
- ✗ The installer is not code-signed, so SmartScreen warns on the first install
- ✗ The uninstall is not tested in CI (Windows Settings > Apps does it; your data in `%APPDATA%` stays)

## Your PC, 4 to 5 October 2026 (0.22.1)

From your log of Sunday 20:32 to Monday 04:39 UTC (0.21.0, then 0.22.0); the fixes below shipped in 0.22.1 on 5 October:

- ✓ **In-app update**: 0.22.0 was found, 629 KB downloaded (changes only), verified and installed after **Restart to update**; the app started again in 2.8 s (spec J).
- ✓ **Monday clock**: `Broker time: UTC+2/+3 (US summer time)` at 21:25 UTC, 10 minutes after MT5's last reconnect, then the history was imported again; no "Broker time jumped" line.
- ✓ Eight hours across the weekly open with no frozen worker; MT5 reconnected twice by itself (after 8 s and 2 s).
- Fixed: at the Sunday open the newest price was still Friday's, so "Quotes fresh" said critical ("173,123 s old") and sent an error notification. The wait now counts from the weekly open plus 10 minutes.
- Fixed: the broker's restart at the open dropped MT5 for 11 s and sent an urgent "MT5 disconnected" notification. Without open positions that notice now waits a minute and is dropped when MT5 is back; with open positions it still goes out at once.
- Fixed: the weekly report listed "unknown" health checks as issues.
- Fixed: Ask AI to an endpoint behind Cloudflare failed with HTTP 403 (error 1010, Python's default client signature). The app now names itself in its requests, and HTTP errors carry a plain hint.
- Still open on your PC: Algo Trading was off in MT5 while the mode was Auto (nothing can be sent until it is on; turned on the next morning, see below), and Telegram is still not reachable (timeouts, then "Bad Gateway").

## Your PC, 5 October 2026 morning (0.22.2)

From your log of Monday 04:40 to 06:05 UTC (0.22.0, then 0.22.1), before the London open; the fixes below shipped in 0.22.2 on 5 October:

- ✓ **In-app update** 0.22.0 to 0.22.1: 685 KB (changes only), verified, restarted in 3.2 s.
- ✓ **Algo Trading** is on in MT5 (04:44 UTC), so Auto can send orders on the demo account.
- The Go-Live checklist of `london_breakout` was overridden on demo with 6 of 7 checks failing (no walk-forward, no paper trades, risk settings not reviewed). Your six-month EURUSD backtest of it lost: 34 trades, 29% won, profit factor 0.65, -4.1%. Fine for testing Auto on demo, not for real money.
- Model v1 was trained on two years, but MT5 had M5 bars only from 29 May 2025 (MT5's "Max bars in chart"), and it is not better than the baseline out of sample, so the baseline stays in use.
- Fixed: when one side of a breakout filled, the other side was cancelled and then adopted again from that cycle's older order list ("Adopted bot pending order", then "expired or cancelled" one cycle later). Seen 30 times in the backtest; live orders had the same path.
- Fixed: a breakout order that filled between the engine's two MT5 reads looked like an expiry, and its other side stayed open. Before calling an order expired the engine now looks for it once more among the positions.
- Fixed: "filtered out because: no open position of this strategy on the symbol" read as the opposite of what happened. The check is now "one position per strategy and symbol" and shows how many are open.
- Fixed: a backtest wrote every replayed signal, approval and fill to the app log as INFO (over 1,000 lines for six months of EURUSD, "approved by the user" among them). The replay now logs at DEBUG; the Backtest page shows it all.
- Fixed: the idle CPU budget warned while that backtest ran ("3.3 % is over the budget"). A backtest in the CPU's 5-minute window now makes the CPU "busy", not over budget.

## Your PC, 5 October 2026 London open (0.22.3)

From your log of Monday 06:05 to 07:41 UTC (0.22.1, then 0.22.2); the fixes below shipped in 0.22.3 on 5 October:

- ✓ **In-app update** 0.22.1 to 0.22.2: 703 KB (changes only), verified, restarted in 2.5 s. The first download was slow and stopped when the app was closed; the second took 44 s.
- ✓ Three short broker disconnects (06:20, 06:26 and 06:42 UTC, the longest 37 s) were reconnected without a notice, as intended.
- At the London open (07:00 UTC) Auto sent the `london_breakout` buy stop and sell stop on XAUUSD (0.01 lot, 43.70 USD risk each). Both failed: MT5's `order_check` returned nothing at all, so the app never sent them. MT5's own reason was saved with the request but not written to the log.
- Fixed: a check without any answer no longer stops an order. The order is sent and the trade server decides; a check that MT5 refuses still stops it. When MT5 answers nothing, the log now shows MT5's last error in plain words and the switches that block trading (Algo Trading, trading from Python, the account, the symbol's order, expiry and filling modes), with what to change.
- Fixed: a failed order was only a WARNING line in the log. Each one is now an urgent notice (toast and Telegram): "Order failed: ...".
- Fixed: Auto mode showed "Approve or skip it before it expires" for signals it sends by itself. Auto now asks for no approvals.
- The soak report said FAIL because the run lasted 17.5 hours; it needs 24.

## Test on your PC

1. **Clean install**: on a PC (or a new Windows user, or Windows Sandbox) where the app was never installed, download `MT5TradingWorkstation-win-Setup.exe` from the latest release and run it ("More info", "Run anyway" if SmartScreen warns). The app starts by itself with the "This is practice money" card. In `%LocalAppData%\MT5TradingWorkstation\current` run `.\MT5TradingWorkstation.exe --self-check --report-file self-check.txt`: every line OK. Then install MT5, log in to a demo account and connect.
2. **Weekday, market open**: `--mt5-trade-test --symbol EURUSD` and `--symbol XAUUSD` on demo (Phase 8); a Paper approval and a Semi-auto approval, the restart with an open trade and the kill switch; the Simple view's suggestion, Approve, Skip, Close now and Stop (Phase 9); the London morning signals (Phase 6) and the Risk page (Phase 7).
3. ✓ Done on 5 October: **Monday after the open**, `all.log` says `Broker time: UTC+2/+3 (US summer time)` with no "Broker time jumped" line.
4. **24 hours on demo** on market days, then Health > **Create soak report** (Phase 14c), and look at the Logs page filters and the trace timeline.
5. **Persian**: click **فارسی**, restart: Home and the top bar are right to left in Vazirmatn (Phase 16a).
6. **Light theme and keyboard**: the light theme's charts, Tab through Home, Ctrl+, opens Settings, 150% display scaling stays sharp (Phase 16b).
7. Your PC to-dos from the earlier logs: free disk space (the Health page warns under 5 GB), for Telegram a VPN or a Windows proxy, and in MT5 Tools > Options > Charts set "Max bars in chart" to Unlimited (then restart MT5) so backtests and training get two years of M5 bars. If an order fails with "trading from Python blocked", untick "Disable automatic trading through the external Python API" in MT5 Tools > Options > Expert Advisors.
8. Send a debug bundle (Health > **Create debug bundle**) when something looks wrong.

## Known limitations

- The strategies are examples, not proven to be profitable; the probability stays "unknown" until a strategy has 30 resolved signals, and the model is usually "Baseline only" with two strategies.
- Cloud sync goes one way, from the PC to the cloud; there is no restore.
- Persian covers the Simple view and the top bar; the Advanced pages stay English.
- One symbol per backtest; SL and TP inside one candle always count as a loss.
- "Start MT5 automatically" and "Start the app with Windows" (spec I5) are not built.
- The installer is not code-signed.
