# User guide

## What works today (Phase 12)

Phase 12 adds the Dashboard, the Analytics and Journal pages, daily and weekly reports, Windows notifications and an optional Telegram bot (see "Dashboard", "Analytics page", "Journal page" and "Notifications and Telegram"). Phase 11 added the win-probability model: train it on MT5 history on the Model page, see how it does out of sample next to the baseline, and use it only when it is better (see "Model page"). Phase 10 added backtesting: replay the strategies on MT5 history with the same code that trades live, with walk-forward, Monte-Carlo and a sensitivity heatmap (see "Backtest page" and "Backtest from the command line"). Phase 9 added the Simple view's Home screen: one trade suggestion at a time in plain words with Approve and Skip, your balance, your open trades with Close now, and Stop trading now (see "Home (Simple view)"). Phase 8 added trading: approve a signal and the app places the order with its stop loss and take profit, manages it and records the result (see "Approving a signal", "Positions & Trades" and "Kill switch"). Paper is the default, so nothing reaches your MT5 account until you choose Semi-auto. Phase 7 added risk management: every signal is sized from your account and checked against your limits, and the Risk page shows them (see "Risk page"). Phase 6 added strategies and signals: the Signals page and the Strategies page (see below). Phase 5 added the Market page: analysis cards, a trend matrix, an interactive chart, correlation, currency strength and the economic calendar. See "Market page" below. Phase 4 added the local database, optional cloud sync and trade history import.

- **Simple view** (default): the Home screen in plain language (see "Home (Simple view)"). **Settings** (top bar) opens the settings, **Back to Home** returns.
- **Advanced view**: click **Switch to Advanced** (top right). The sidebar groups the pages into Trade, Analyze and System. Pages that are not built yet say in which phase they arrive and never show sample data.
- **Theme**: click **Switch to light theme** or **Switch to dark theme** (top right). Your choice is remembered.
- **Command palette**: in the Advanced view press **Ctrl+K**, type part of a page or command name (for example `risk` or `debug`) and press Enter.
- **Status bar** (bottom; in the Simple view only after "Show status bar"): connection state (broker, server and login when connected), the operating mode badge (`PAPER`, or `ANALYSIS-ONLY` with an investor password), the bot state, the cloud state, the session clock, the next high-impact news, the app version and the kill switch.

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
- Audit lines (category `audit`) say what changed, for example `Audit: trading mode changed (paper → semi_auto)`; the full before and after values are in the line's JSON entry.
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
- **Cards**: one per symbol, renewed after every closed 5-minute bar, and once when the market closes on Friday or opens on Sunday. The headline reads like "XAUUSD: H4 uptrend, H1 pullback into support 2,318.20 (0.4 ATR), volatility high, London session, USD CPI in 3h 05m -> wait". The verdict is one of: watch, wait (news, wide spread, market closed, extreme volatility), no clear direction, or data problem (the reason is shown and logged).
- **Chart**: pick a symbol and a timeframe. Drag to pan, use the mouse wheel to zoom, and move the mouse for the crosshair with open, high, low and close. Tick boxes turn EMAs, levels, swings and session shading on or off.
- **Trend matrix, Correlation, Currency strength**: hover a cell for its reasons.
- **Calendar**: events of the next 7 days with countdowns. Add one by hand, import a CSV (columns time, currency, impact, title; optional actual, forecast, previous; times in UTC), or use the MT5 exporter:
  1. Click Install MT5 exporter (connect MT5 first).
  2. In MT5 press F4 to open MetaEditor, open Services > CalendarExporter.mq5 and press F7 (Compile).
  3. In MT5, Navigator > Services, right-click CalendarExporter > Add service, then start it.
  4. The app reads the calendar every 3 minutes; click Read MT5 calendar now to read it at once.

## Signals page

Advanced view > Signals. A signal is a trade idea from a strategy.

- **Feed**: every signal, also the filtered-out ones with the reason. Pick "Waiting for approval", "Sent or open", "Closed or failed", "Filtered out" or "Expired or dismissed" to narrow it.
- **Approve**: select a waiting signal, click Approve and confirm. The dialog shows the lot and about how much you could lose at the stop loss. See "Approving a signal".
- **Decision trace**: select a signal to see every step: the closed bar, each strategy rule with its value and limit, the features, the probability, the EV, each filter (✓ passed, ✗ failed, · not applied), risk and the decision.
- **Lot and Risk**: the lot size and the money the trade would lose at its stop loss (commission included), for signals within every risk limit. A signal that breaks a limit says "rejected by risk" and which limit.
- **Probability**: "unknown" until the strategy has 30 resolved signals; the app never shows a made-up number.
- **Dismiss**: rejects a waiting signal. Waiting signals also expire by themselves (trend pullback after 2 bars, London breakout at 11:00 London time).
- **Scanner**: symbols ranked by setup (ready before forming), then by probability x EV, or by rules passed while no probability exists.

## Home (Simple view)

The screen you see first. Everything on it is in plain words; the exact numbers are one click deeper, under **Show details**.

- **First start**: a card says "This is practice money, not real" and asks how much you know about trading. "I'm new to trading: keep it simple" keeps this screen; "I already trade: show the Advanced view" opens the Advanced view. It is asked once.
- **Mode line** (top): "Practice money: trades are simulated, nothing real is bought or sold." in Paper, "Real orders: a trade is placed only after you approve it." in Semi-auto, "Watching only" in Analysis-only. Change the mode in the Advanced view (Positions & Trades).
- **Balance**: your practice balance (Paper) or your balance, today's change in money and percent (▲ green, ▼ red), and the result of the last 7 days with a small chart. "not known yet" means the account was not read yet.
- **Trade suggestion**: one at a time, the one that ends soonest first ("1 of 3 suggestions" when more wait). It says which market (for example "Gold (XAUUSD)"), Buy or Sell, one sentence why, how much you could lose and make in money, a confidence label and when the suggestion ends. "The price has moved since this was found" means it may be cancelled when you approve.
  - **Approve**: asks once, with the money and whether it is practice or a REAL order. Then it goes through the same checks as on the Signals page (see "Approving a signal"): checked again at the live price, sized, placed with its exits.
  - **Skip**: removes the suggestion (the same as Dismiss on the Signals page).
  - **Show details**: the win chance with its range and sample size, the order price, both exit prices, the size in lots, the expected result in R, the chart timeframe, the strategy and its full reason.
  - Confidence is "not known yet" until a strategy has 30 finished suggestions. It is an estimate, never a promise.
- **Your open trades**: each trade the app opened, with Buy or Sell and its result now in money and percent. **Close now** closes it at the current price after a confirmation. "Waiting for its price" is an order that has not filled yet.
- **Status line**: what the app is doing: watching, found a trade, trade running, market closed, paused by a limit, stopped, or not connected.
- **Stop trading now**: the kill switch (see "Kill switch"). It asks first.
- **Show status bar**: shows the Advanced status bar at the bottom.

## Approving a signal

1. Select a signal that is "waiting for approval" and click **Approve**, then **Yes**.
2. Within a few seconds the app checks it again with the live price: is it still valid, did the price move more than 0.25 R from the entry, is the spread too wide, are the stops far enough? If anything fails, the signal becomes "expired" with the reason and nothing is sent.
3. The risk manager sizes it again at the live price. Then the order is placed with its stop loss and take profit:
   - **Paper** (default): simulated on live prices, nothing in MT5.
   - **Semi-auto**: a real order on your MT5 account, checked first with MT5's `order_check`. It shows in MT5 with the strategy's magic number (26070001 trend pullback, 26070002 London breakout) and a `tw-` comment.
4. The signal then shows "filled", "open (managed)" and finally "closed" with the net result and R. A London breakout order that fills cancels its other side.

Approve is off in Analysis-only mode and after the kill switch.

## Positions & Trades

Advanced view > Positions & Trades.

- **Trading mode**: Paper, Semi-auto or Analysis-only, then **Use this mode**. On a REAL account, Semi-auto asks you to type REAL. Auto mode needs the Go-Live checks of a later phase and cannot be chosen yet.
- **Open**: the positions and pending orders this app opened (manual trades are not listed and never touched), with P/L, the best (MFE) and worst (MAE) move in R. **Close position** closes the selected one at the market price after a confirmation.
- **Execution events**: fills, slippage, stop loss moves, closes and failures, newest first.
- **History**: your closed trades with filters; select one to see its story, the signal's reasoning, features and decision trace, the events and your journal notes.
- **Execution settings**: max deviation, retries, entry tolerance, spread limit, paper slippage, commission and start balance; and per strategy the optional management (break-even at X R, ATR trailing, partial close, time exit). All management rules are off (0) by default.
- If the app or the PC restarts, the app finds its trades in MT5 again and keeps managing them. Your stop loss and take profit are on the broker's server, so they protect you while the app is closed.

## Kill switch

Click **Stop trading** in the status bar, the kill switch on Positions & Trades, or press **Ctrl+Shift+K**, then confirm. The app closes every position it opened, cancels its pending orders and stops new entries. Manual trades stay open. To trade again: type ENABLE on the Risk page and click "Allow approvals again" on Positions & Trades.

## Trade test (demo only)

With MT5 open on a **demo** account and Algo Trading ON, run `MT5TradingWorkstation.exe --mt5-trade-test --symbol EURUSD`. It opens the minimum lot with a stop loss and take profit, checks it in MT5, moves the stop loss, closes it, reads the closed deal back and prints every step with ✓ or ✗ and MT5's return codes. It refuses to run on a REAL account. Run it while the market is open (Monday to Friday): at the weekend MT5 refuses the order with 10018 MARKET_CLOSED. Use `--report-file report.txt` to save the report.

## Strategies page

Advanced view > Strategies. Both strategies are **examples with exact rules, not proven to be profitable**.

- Tick **On** to let a strategy make signals, change its settings (limits are checked), then click **Save strategy settings**. Changes apply from the next closed bar and are written to the audit log.
- **Signal filters**: minimum probability and EV, cooldown and pause after losses, spread limits, sessions, rollover, Friday close, Monday open and the news blackout.
- Settings are saved per profile in `strategies.json`.

## Risk page

Advanced view > Risk. The limits are enforced by the app on every signal; this page shows them and lets you change them.

- **Status**: "Trading allowed", or why new entries are stopped. Below it, in plain words, how much one new trade can lose at most.
- **Limit usage**: daily loss, drawdown, open risk, open trades, trades today and margin level, each with its allowed value.
- **Currency exposure**: your net risk per currency over all open positions (manual ones too, unless you turn that off). Long EURUSD and long GBPUSD are both short USD, so a third USD-short trade can be blocked.
- **Risk events**: limits hit, trades blocked, stops and re-enables.
- **Profile**: pick Conservative (0.25% per trade), Normal (0.5%) or Prop-firm (0.5%, 4% daily loss, 8% static drawdown), click **Use this profile**, then **Save risk settings**. You can change any value; the profile then shows as Custom. No value can go above the safety caps (for example 1% per trade).
- **Stop new entries**: no new signal passes until you re-enable. The kill switch (status bar, Ctrl+Shift+K) also closes the bot's positions.
- **Re-enable trading**: needed after the drawdown limit or a stop; type ENABLE to confirm. The drawdown then counts from your current equity. A daily-loss stop ends by itself at the next trading day.
- Lots are calculated with MetaTrader 5's own profit calculator, so gold, JPY pairs and non-USD accounts are sized correctly.

## Backtest page

Advanced view, Analyze > Backtest. Choose the symbol, the dates (UTC; the last day is included), the strategies (they use the settings of the Strategies page) and the costs: start balance, account currency, leverage, spread floor and extra spread, slippage, commission per lot (round turn) and swap per lot and night. Click **Run backtest**; the window stays usable and **Cancel** stops after the current day.

- **How it works**: the app reads the history from MT5 (kept on this PC, so the next run is faster) and replays it bar by bar through the same strategies, filters, risk limits and trade management as live trading. Entries happen at the next bar's open; a stop loss and take profit inside one candle count as a loss; weekend gaps fill at the open price.
- **Summary**: trades, win rate, net result, profit factor, expectancy (R per trade), max drawdown and how long it lasted, Sharpe and Sortino, the longest losing streak. Fewer than 100 trades shows a warning: such results are mostly luck.
- **Equity** shows the account and the drawdown over time; **Trades** lists every trade; **Breakdowns** groups them by month, session, weekday, strategy or symbol.
- **Walk-forward** (tick it, choose one parameter and the values to try): the best value on each past window (in-sample) is traded on the next one (out-of-sample). It passes with at least 100 out-of-sample trades and a positive expectancy.
- **Monte-Carlo**: the same trades in 1,000 other orders; how deep the drawdown can get and the risk of ruin (losing half the account).
- **Sensitivity**: two parameters on a grid. A stable plateau is good; a sharp peak means the best values are probably fitted to noise.
- **Saved runs**: every finished run, to compare.
- MT5 keeps only "Max bars in chart" bars: set it to Unlimited (Tools > Options > Charts) and restart MT5. The page says when the history is too short.

## Backtest from the command line

`MT5TradingWorkstation.exe --backtest --symbol EURUSD --from 2026-01-01 --to 2026-06-30` connects with the saved profile, reads the history, replays it with the profile's settings and prints the summary and every trade (add `--strategies trend_pullback` for one strategy and `--report-file backtest.txt` to save it). It never sends an order and works at the weekend.

## Model page

Advanced view, Analyze > Model. The model estimates each signal's chance to reach its target before its stop. It is an estimate on noisy data, never a promise.

- **Train**: choose the symbols (comma separated), the dates (the longer the better: two years is the default), the strategies, how many signals are needed (300), the walk-forward folds (5) and the timeout in bars (48). Click **Train model**: the app reads the history from MT5, replays the strategies on it like a backtest, labels every signal (did it reach the target first?) and trains in a separate process, so the window stays usable. **Cancel** stops it.
- **Baseline only**: with fewer signals than needed no model is trained and the app keeps showing the strategy's own win rate with its range. With the two example strategies this is the usual result.
- **The report**: the model's scores next to the baseline's on signals it never saw (ROC-AUC: above 0.5 is better than a coin; log-loss and Brier: lower is better), the calibration (predicted vs real win rate), the buckets (signals grouped by their predicted chance, with the real win rate, its range, the expectancy in R and the profit factor), the most important features and the folds.
- **Versions**: every trained model is kept. **Use selected model** only works for a model that beat the baseline (lower log-loss and lower Brier score); otherwise the page says why. **Roll back** goes to the previous usable version, **Use the baseline** stops using a model. Every change is in the audit log.
- **In use**: new signals show the model's chance as "62% ± 8 (n = 140, model v3)" (n is how many past signals in that range stand behind it) and, in the decision trace, the three factors that moved it most ("a strong trend +8%").
- **Drift**: when a model is in use, the page compares its recent trades with what it predicted and checks whether the market looks different from the training data; a warning means: train it again.
- A model made for an older version of the features cannot be used after an update; train it again.

## Dashboard

Advanced view, Trade > Dashboard: balance, equity, today's closed result, open risk and the last 30 days (net and win rate), the equity curve of your closed trades, how much of each risk limit is used (daily loss, drawdown, open risk, open trades), your open positions, the latest signals and the market bias of every watched symbol. It only shows; it never changes anything. The Go-Live checklist comes in Phase 13.

## Analytics page

Analyze > Analytics. Choose the dates, the account, symbol, strategy, mode (live or paper) and bot or manual, then click **Update**.

- **Summary**: trades, win rate, net profit and return, profit factor, expectancy (money and R), average win and loss, payoff ratio, largest win and loss, streaks, Sharpe and Sortino, max drawdown with how long it lasted, recovery factor, time to recover and costs. Under 30 trades the page warns that the numbers are mostly luck.
- **Breakdowns**: the same numbers per symbol, strategy, direction, session, hour, weekday, month, holding time, probability bucket, config, bot or manual, and mode.
- **Equity**, **Monthly returns** (a green and red heatmap), **R and MFE/MAE** (how far trades went for and against you: "winners gave back 0.8 R", "4 losers were 1 R ahead first"), **Costs** (commission, swap and fees as a share of the gross profit; the spread is already inside the prices).
- **Behavior** (your manual trades): overtrading days, revenge trades, losers held longer than winners, trades around high-impact news, changing lot sizes, trading outside your best hours.
- **Risk of ruin**: your real trades reshuffled thousands of times: the chance of a 50% drawdown and a 100-trade projection with a likely range. Past trades do not promise future ones.
- **Compare**: live vs paper (and your latest backtests), strategy vs strategy, config vs config, the last 30 days vs the 30 before.
- **Export CSV** saves the trades and the shown breakdown, **Save chart PNG** the shown tab, in `profiles/<profile>/exports`.

Percentages need MT5 connected (the account balance). The start balance of the period is today's balance minus the shown trades' result, so deposits and withdrawals in the period make it approximate.

## Journal page

Analyze > Journal.

- **Trades**: every closed trade with its story in plain words (what, why, the SL and TP, every change the app made, the result with costs). Add your notes, tags (comma separated), a rating from 1 to 5 and, for your own trades, how you felt; click **Save**. Filter the list by a tag.
- **P/L calendar**: each day of the month with its result and number of trades (UTC days); the arrows change the month.
- **Reports**: a daily report after each trading day and a weekly one after each week: result, trades, win rate, best and worst trade, rejected signals by reason, costs, warnings and errors, health issues and anomalies. **Make yesterday's report** and **Make last week's report** make them now. Reports are also saved as files in `profiles/<profile>/reports` and sent as a notification.

## Notifications and Telegram

Settings > Notifications. Choose for each event (trade opened or closed, approval needed, a limit hit, MT5 disconnected, an error, cloud sync failing, model drift, reports) whether it shows a Windows notification and whether it goes to Telegram. Quiet hours hold the others at night; urgent ones (a limit hit, a disconnect, an error) always go out. **Send a test notification** checks it.

Telegram (optional):
1. In Telegram, talk to @BotFather, send `/newbot`, copy the token.
2. Send `/start` to your new bot, and get your chat id (for example from @userinfobot).
3. Paste the token (it is kept in Windows Credential Manager), add the chat id, choose a PIN (4 to 12 digits), tick **Use the Telegram bot**, **Save**.
4. In Telegram send `/pin <your PIN>` (valid 15 minutes), then `/status`, `/positions`, `/pnl`, `/pause` (stop new entries), `/resume` (only after `/pause`), `/approve <id>` (the first characters of a waiting signal's id) or `/killswitch` (then `/killswitch CONFIRM` within a minute). Other chats are ignored. Every command is in the audit log.

The bot works only while the app runs.

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
- `execution.json`: trading mode, execution and management settings.
- `notifications.json`: notification switches, quiet hours, Telegram chat ids and the PIN hash (the bot token is in Windows Credential Manager).
- `exports\`: CSV and PNG files from the Analytics page; `reports\`: the daily and weekly reports.
- `account.json`: login, server and terminal path of the saved account (no password).
- `instance.lock`: held while the app runs on this profile.
- `logs\`: one folder per category with one `.jsonl` file per day, plus `all.log`.
- `crash_reports\`: `crash_<time>.json` files and `fatal_errors.log` for native crashes.

Start the app with `--profile NAME` to keep a separate profile per account. Run one app instance per profile.
