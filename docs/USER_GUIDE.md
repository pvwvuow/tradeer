# User guide

## What works today (Phase 6)

Phase 6 adds strategies and signals: the Signals page and the Strategies page (see below). Signals are suggestions only; nothing is sent to MT5 yet. Phase 5 added the Market page: analysis cards, a trend matrix, an interactive chart, correlation, currency strength and the economic calendar. See "Market page" below. Phase 4 added the local database, optional cloud sync and trade history import.

- **Simple view** (default): a calm Home screen in plain language. It says you are in practice mode and whether the app is connected to MetaTrader 5. The "Stop trading now" button is always visible; it becomes active once trading exists.
- **Advanced view**: click **Switch to Advanced** (top right). The sidebar groups the pages into Trade, Analyze and System. Pages that are not built yet say in which phase they arrive and never show sample data.
- **Theme**: click **Switch to light theme** or **Switch to dark theme** (top right). Your choice is remembered.
- **Command palette**: in the Advanced view press **Ctrl+K**, type part of a page or command name (for example `risk` or `debug`) and press Enter.
- **Status bar** (bottom, always visible): connection state (broker, server and login when connected), the operating mode badge (`PAPER`, or `ANALYSIS-ONLY` with an investor password), the bot state, the cloud state, the session clock, the next high-impact news, the app version and the kill switch.

## Before you connect

The app does not talk to your broker directly. It talks to the **MetaTrader 5 terminal on the same PC**, and the terminal talks to the broker.

1. Start MT5 (64-bit `terminal64.exe`, not MT4) and log in to your account. Use a **demo** account first.
2. In MT5 press the **Algo Trading** button in the toolbar so it turns green.
3. Run MT5 and this app as the **same Windows user** and the same way: both normally, or both with "Run as administrator". A mismatch is the most common reason a connection fails.

## Connect to MT5

Advanced view, sidebar **System**, then **Settings** (or Ctrl+K, "Connect to MT5").

1. **Terminal**: pick your MT5 from the list. Every MT5 found on the PC is listed with its broker. If yours is missing, click **Browse...** and choose `terminal64.exe`.
2. **Account**: enter login, password and server. The server list is filled from the servers your terminal already knows; you can also type one.
3. Click **Connect**. Each step of the checklist turns ✓ or ✗ with the real value (terminal build, broker, account, balance, leverage, hedging or netting) or a fix in plain words. Below it, the bid and ask of 3 symbols update every second: this is real data from your terminal.
4. If **Algo Trading** shows ✗, press the button in MT5 and click **Re-check**.
5. The profile is saved. The password goes to **Windows Credential Manager**, never into a file. Next time the app connects by itself.

An **investor (read-only) password** works too: the app switches to Analysis-only and shows `ANALYSIS-ONLY` in the status bar. It can analyze but never trade on that account.

If the first connect fails, the app does **not** retry on its own (a wrong password repeated many times can lock your account). Fix the item marked ✗ and click **Connect** again. If an established connection drops, the status bar says so at once and the app reconnects by itself, waiting a little longer after each try (up to 2 minutes). If you have open positions at that moment, the app logs an urgent alert; your server-side stop losses stay at the broker.

## Connection Diagnostics

On the same page click **Run diagnostics**. It repeats every check and adds ping to the broker, bars available per symbol and timeframe, the symbol specs, the broker time offset and the permission flags. **Copy report** copies a text report without your password; paste it into the pull request when something fails.

## Smoke test (30 seconds, read-only)

```powershell
MT5TradingWorkstation.exe --mt5-smoke-test --report-file smoke.txt
```

It connects with the saved profile (or the account logged in to MT5 right now), prints the account, 3 prices, the last 10 EURUSD M15 bars and your last 10 deals, and ends with `Result: PASS` or `Result: FAIL`. It never places, changes or closes an order.

## Several accounts

Each account gets its own profile: start the app with `--profile NAME`, or click **Open profile in a new window** on the Settings page. Run one app instance per profile; the app refuses to open the same profile twice. To run several accounts at the same time, use one portable MT5 terminal per account.

## Logs page

Advanced view, sidebar **System**, then **Logs**.

- New lines appear live. Passwords, keys and tokens are already masked.
- Filter by category, by minimum level, or by text (tick **Regex** for a regular expression).
- Click a line to see its full JSON entry below the table. If the line belongs to a trace, **Show only this trace** shows every line of that trace; click **Show all traces** to go back.
- Pick a category in the first filter to change **its** level (for example `mt5` to `DEBUG`). The change is recorded in the audit log.
- **Debug mode for 30 min** switches every category to DEBUG and turns itself off after 30 minutes, so it cannot fill your disk by accident.
- **Open log folder** opens the folder in Explorer.

## When something goes wrong

The app saves a crash report and shows a "Something went wrong" window with the report's location. The report contains no passwords or keys. Restart the app; if it happens again, attach the newest file from the crash reports folder to a comment on the GitHub pull request.

To check that crash reporting works: open the command palette and run **Test the crash reporter**, or run `--crash-test` from PowerShell (see README).

## Cloud sync with Supabase (optional)

Everything is saved on your PC first. Cloud sync copies it to your own free Supabase project. The app works fine without it.

1. Create a free project at https://supabase.com.
2. In the project open SQL Editor and run these files from the `supabase` folder, in this order: `schema.sql`, `rls.sql`, `views.sql`, `cleanup.sql`.
3. Open Authentication, Sign In / Providers, and check that Email is enabled. If "Confirm email" is on, click the link in the email before you sign in.
4. Copy the Project URL and the anon (publishable) key from Project Settings (API Keys and Data API). Never use the service_role or secret key: the app refuses it.
5. In the app open Settings, tab "Data & cloud sync". Paste the URL and the key, type your email and a password, then click Create account the first time or Sign in later.

The app never saves your password. It keeps a sign-in token in Windows Credential Manager, so you stay signed in after a restart.

### What the cloud status means

| Status bar | Meaning |
| --- | --- |
| Cloud: off | Cloud sync is not set up or is turned off. |
| Cloud: signed out | Sign in again in "Data & cloud sync". |
| Cloud: up to date | Everything is uploaded. |
| Cloud: uploading | Rows are being uploaded now. |
| Cloud: offline | No internet, or Supabase cannot be reached. The app retries by itself. |
| Cloud: project paused | Free projects pause after about a week without use. Restore it in the Supabase dashboard; nothing is lost. |
| Cloud: setup needed | A table or column is missing. Run the SQL files again. |
| Cloud: error | Something unexpected happened. Look at the Logs page. |
| · N waiting | Rows saved on this PC that are not uploaded yet. |
| · N refused | Rows Supabase refused. Fix the cause, then click Retry refused rows. |

## Market page

Advanced view > Market. Everything here is information only and never a trade signal.

- **Watchlist**: type up to 10 symbols separated by commas (for example `EURUSD, GBPUSD, XAUUSD`) and click Save watchlist. Broker suffixes such as `EURUSD.m` are found automatically; a symbol your broker does not offer is named in the page status.
- **Cards**: one per symbol, renewed after every closed 5-minute bar. The headline reads like "XAUUSD: H4 uptrend, H1 pullback into support 2,318.20 (0.4 ATR), volatility high, London session, USD CPI in 3h 05m -> wait". The verdict is one of: watch, wait (news, wide spread, market closed, extreme volatility), no clear direction, or data problem (the reason is shown and logged).
- **Chart**: pick a symbol and a timeframe. Drag to pan, use the mouse wheel to zoom, and move the mouse for the crosshair with open, high, low and close. Tick boxes turn EMAs, levels, swings and session shading on or off.
- **Trend matrix, Correlation, Currency strength**: hover a cell for its reasons.
- **Calendar**: events of the next 7 days with countdowns. Add one by hand, import a CSV (columns time, currency, impact, title; optional actual, forecast, previous; times in UTC), or use the MT5 exporter:
  1. Click Install MT5 exporter (connect MT5 first).
  2. In MT5 press F4 to open MetaEditor, open Services > CalendarExporter.mq5 and press F7 (Compile).
  3. In MT5, Navigator > Services, right-click CalendarExporter > Add service, then start it.
  4. The app reads the calendar every 3 minutes; click Read MT5 calendar now to read it at once.

## Signals page

Advanced view > Signals. A signal is a trade idea from a strategy. Nothing is sent to MT5: risk sizing arrives in Phase 7 and orders in Phase 8, so **Approve** is off.

- **Feed**: every signal, also the filtered-out ones with the reason. Pick "Waiting for approval", "Filtered out" or "Expired or dismissed" to narrow it.
- **Decision trace**: select a signal to see every step: the closed bar, each strategy rule with its value and limit, the features, the probability, the EV, each filter (✓ passed, ✗ failed, · not applied), risk and the decision.
- **Probability**: "unknown" until the strategy has 30 resolved signals; the app never shows a made-up number.
- **Dismiss**: rejects a waiting signal. Waiting signals also expire by themselves (trend pullback after 2 bars, London breakout at 11:00 London time).
- **Scanner**: symbols ranked by setup (ready before forming), then by probability x EV, or by rules passed while no probability exists.

## Strategies page

Advanced view > Strategies. Both strategies are **examples with exact rules, not proven to be profitable**.

- Tick **On** to let a strategy make signals, change its settings (limits are checked), then click **Save strategy settings**. Changes apply from the next closed bar and are written to the audit log.
- **Signal filters**: minimum probability and EV, cooldown and pause after losses, spread limits, sessions, rollover, Friday close, Monday open and the news blackout.
- Settings are saved per profile in `strategies.json`.

## Trade history

Open Settings, tab "Data & cloud sync", and click Import history now, or use the command "Import trade history". The first import reads your whole history; later imports read only the last few days. Importing again never creates duplicates. Each trade is marked manual, bot or external (another EA).

Times are converted to UTC with your broker's clock, including its summer-time rules (most brokers are UTC+2 in winter and UTC+3 in summer). When the app learns new rules for your broker, it imports the history again once to correct older trades.

## Your data on this PC

- Database: `profiles/<profile>/data/workstation.db`.
- Backups: one a day, the last 7 kept, plus one before every database update.
- Cleanup: logs and MT5 request records older than 90 days and health and performance rows older than 30 days are removed. Trades and signals are never deleted, and nothing is deleted before it is uploaded.

## Where your files live

`%APPDATA%\MT5TradingWorkstation\profiles\<profile>\`:

- `ui_prefs.json`: theme and view.
- `account.json`: login, server and terminal path of the saved account (no password).
- `instance.lock`: held while the app runs on this profile.
- `logs\`: one folder per category with one `.jsonl` file per day, plus `all.log`.
- `crash_reports\`: `crash_<time>.json` files and `fatal_errors.log` for native crashes.

Start the app with `--profile NAME` to keep a separate profile per account. Run one app instance per profile.
