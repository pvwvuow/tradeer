# User guide

## What works today (Phase 2)

- **Simple view** (default): a calm Home screen in plain language. It says you are in practice mode and that the app is not connected to MetaTrader 5 yet. The "Stop trading now" button is always visible; it becomes active once trading exists.
- **Advanced view**: click **Switch to Advanced** (top right). The sidebar groups the pages into Trade, Analyze and System. Pages that are not built yet say in which phase they arrive and never show sample data.
- **Theme**: click **Switch to light theme** or **Switch to dark theme** (top right). Your choice is remembered.
- **Command palette**: in the Advanced view press **Ctrl+K**, type part of a page or command name (for example `risk` or `debug`) and press Enter.
- **Status bar** (bottom, always visible): connection state, the operating mode badge (`PAPER`), the bot state, the app version and the kill switch.

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
- `logs\`: one folder per category with one `.jsonl` file per day, plus `all.log`.
- `crash_reports\`: `crash_<time>.json` files and `fatal_errors.log` for native crashes.

Start the app with `--profile NAME` to keep a separate profile per account. Run one app instance per profile.
