# User guide

## What works today (Phase 3)

- **Simple view** (default): a calm Home screen in plain language. It says you are in practice mode and whether the app is connected to MetaTrader 5. The "Stop trading now" button is always visible; it becomes active once trading exists.
- **Advanced view**: click **Switch to Advanced** (top right). The sidebar groups the pages into Trade, Analyze and System. Pages that are not built yet say in which phase they arrive and never show sample data.
- **Theme**: click **Switch to light theme** or **Switch to dark theme** (top right). Your choice is remembered.
- **Command palette**: in the Advanced view press **Ctrl+K**, type part of a page or command name (for example `risk` or `debug`) and press Enter.
- **Status bar** (bottom, always visible): connection state (broker, server and login when connected), the operating mode badge (`PAPER`, or `ANALYSIS-ONLY` with an investor password), the bot state, the app version and the kill switch.

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

## Where your files live

`%APPDATA%\MT5TradingWorkstation\profiles\<profile>\`:

- `ui_prefs.json`: theme and view.
- `account.json`: login, server and terminal path of the saved account (no password).
- `instance.lock`: held while the app runs on this profile.
- `logs\`: one folder per category with one `.jsonl` file per day, plus `all.log`.
- `crash_reports\`: `crash_<time>.json` files and `fatal_errors.log` for native crashes.

Start the app with `--profile NAME` to keep a separate profile per account. Run one app instance per profile.
