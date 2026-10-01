# User guide

## What works today (Phase 1)

- **Simple view** (default): a calm Home screen in plain language. It says you are in practice mode and that the app is not connected to MetaTrader 5 yet. The "Stop trading now" button is always visible; it becomes active once trading exists.
- **Advanced view**: click **Switch to Advanced** (top right). The sidebar groups the pages into Trade, Analyze and System. Each page says in which phase it will be built and never shows sample data.
- **Theme**: click **Switch to light theme** or **Switch to dark theme** (top right). Your choice is remembered.
- **Command palette**: in the Advanced view press **Ctrl+K**, type part of a page name (for example `risk`) and press Enter.
- **Status bar** (bottom, always visible): connection state, the operating mode badge (`PAPER`), the bot state, the app version and the kill switch.

## Where your settings live

`%APPDATA%\MT5TradingWorkstation\profiles\<profile>\ui_prefs.json`. Start the app with `--profile NAME` to keep a separate profile per account.
