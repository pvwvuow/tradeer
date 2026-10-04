# User guide

The app helps you watch the market, explains every trade idea and places trades only within your risk limits. It does not find a profitable strategy for you: the two built-in strategies are examples that are not proven to be profitable, every win chance is an estimate with a range, and Paper (practice money) is the default.

## Install, update and uninstall

**Install**

1. Download `MT5TradingWorkstation-win-Setup.exe` from the [latest release](https://github.com/pvwvuow/tradeer/releases/latest).
2. Run it. It installs for your Windows user only (no admin rights) into `%LocalAppData%\MT5TradingWorkstation`, adds a Start menu shortcut and starts the app.
3. The installer is not code-signed yet, so Windows SmartScreen may say "Windows protected your PC": click **More info**, then **Run anyway**.
4. The first start asks how much you know about trading (see "Home (Simple view)").

To check the installed app, run `MT5TradingWorkstation.exe --self-check --report-file self-check.txt` in its folder (`%LocalAppData%\MT5TradingWorkstation\current`); every line must say OK and the last one `Result: PASS`. The portable zip from the release runs without installing: unzip it and start `MT5TradingWorkstation.exe`.

**Updates**

The app looks for a new version 60 seconds after it starts and then every 6 hours. When one is found, a banner at the top says "Version X is available" (or "Version X is ready" once downloaded) with **Restart to update**, **Details** and **Later**. Settings > Updates has **Check now**, **Download**, **Restart to update** and **Release notes**, and the switches "Check for new versions automatically", "Check every" (1 to 48 hours), "Download new versions in the background (never a major one)" and "Pause updates (for example during a challenge or a live session)".

- Only the changed parts are downloaded (a delta of about 1 MB), checked with SHA-256 and installed after you click **Restart to update**. The app never restarts by itself.
- With open trades it tells you first: their stop loss and take profit stay at the broker, but nothing is managed during the minute of the restart.
- If the new version does not start healthy twice, or its self-check fails, the app offers to go back: **Roll back** ("Restart and go back to X").
- Your profiles, settings and database are in `%APPDATA%\MT5TradingWorkstation` and are never touched by an update.

**Uninstall**

Windows Settings > Apps > Installed apps > MT5 Trading Workstation > Uninstall. Your profiles and database stay in `%APPDATA%\MT5TradingWorkstation`; delete that folder to remove them too. Saved passwords and tokens stay in Windows Credential Manager (Control Panel > Credential Manager > Windows Credentials) until you remove them.

**Moving to a new PC**

1. Install the app on the new PC as above and start it once, then close it.
2. Copy the folder `%APPDATA%\MT5TradingWorkstation\profiles` from the old PC over the new one (with the app closed on both).
3. Install MT5, log in, start the app and connect: the account password, the Supabase sign-in, the Telegram token and the AI key are kept per PC in Windows Credential Manager, so type them again once.

Cloud sync only copies from the PC to the cloud; it cannot restore a new PC.

## The window

- **Simple view** (default): the Home screen in plain language (see "Home (Simple view)"). **Settings** in the top bar opens the settings, **Back to Home** returns.
- **Advanced view**: click **Switch to Advanced** in the top bar. The sidebar groups the pages into TRADE (Dashboard, Market, Signals, Positions & Trades), ANALYZE (Analytics, Journal, Backtest, Model, AI Lab) and SYSTEM (Strategies, Risk, Logs, Health, Settings). **Switch to Simple** goes back.
- **Top bar**: where you are (for example `TRADE / DASHBOARD`), the MT5 chip (`MT5 not connected`, `Demo · 5012345`, `REAL · 5012345` in red), the mode chip (`PAPER`, `SEMI-AUTO`, `AUTO`, `ANALYSIS-ONLY`), **Search Ctrl+K** (Advanced view), the view switch, the theme switch and the language button.
- **Theme**: the theme button in the top bar switches between the dark and the light theme; your choice is remembered.
- **Language**: click **فارسی** in the top bar (or Ctrl+K, "Change the language") and restart the app. Home and the top bar are then in Persian, right to left, in the Vazirmatn font; the Advanced pages, Settings and the status bar stay English. **English** switches back (again after a restart). Numbers and prices keep Latin digits.
- **Command palette**: in the Advanced view press **Ctrl+K**, type part of a page or command name (for example `risk` or `debug`) and press Enter.
- **Status bar** (bottom; in the Simple view only after "Show status bar"): connection state, the operating mode badge, the bot state, the cloud state, the session clock, the next high-impact news, the app version and the kill switch.

## Keyboard and accessibility

- **Tab** and **Shift+Tab** reach every button, field and table; the focused control has a colored ring.
- **Ctrl+,** opens Settings (both views), **Ctrl+K** the command palette (Advanced view), **Ctrl+Shift+K** the kill switch (it always asks first).
- Gains and losses never depend on color alone: results carry a sign and an arrow (`▲ +$12.30`, `▼ −$5.00`), a risk limit bar says "(near the limit)" or "(limit reached)", and in the Analytics excursion chart wins are ▲ and losses ▼.
- Both themes meet WCAG AA contrast. Buttons with only an icon or an arrow have a name for screen readers.
- At 125%, 150% or more Windows display scaling the window scales exactly and the icons stay sharp.

## Before you connect

The app does not talk to your broker directly. It talks to the **MetaTrader 5 terminal on the same PC**, and the terminal talks to the broker.

1. Start MT5 (64-bit `terminal64.exe`, not MT4) and log in to your account. Use a **demo** account first.
2. In MT5 press the **Algo Trading** button in the toolbar so it turns green.
3. Run MT5 and this app as the **same Windows user** and the same way: both normally, or both with "Run as administrator". A mismatch is the most common reason a connection fails.

## Connect to MT5

Settings (Ctrl+,), tab "Account & connection" (or Ctrl+K, "Connect to MT5").

1. **Terminal**: pick your MT5 from the list. Every MT5 found on the PC is listed with its broker. If yours is missing, click **Browse...** and choose `terminal64.exe`.
2. **Account**: enter login, password and server. The server list is filled from the servers your terminal already knows; you can also type one.
3. Click **Connect**. Each step of the checklist turns ✓ or ✗ with the real value (terminal build, broker, account, balance, leverage, hedging or netting) or a fix in plain words. Below it, the bid and ask of 3 symbols update every second: this is real data from your terminal.
4. If **Algo Trading** shows ✗, press the button in MT5 and click **Re-check**.
5. The profile is saved. The password goes to **Windows Credential Manager**, never into a file. Next time the app connects by itself.

An **investor (read-only) password** works too: the app switches to Analysis-only and shows `ANALYSIS-ONLY`. It can analyze but never trade on that account.

If the first connect fails, the app does **not** retry on its own (a wrong password repeated many times can lock your account). Fix the item marked ✗ and click **Connect** again. If an established connection drops, the status bar says so at once and the app reconnects by itself, waiting a little longer after each try (up to 2 minutes). If you have open positions at that moment, the app logs an urgent alert; your server-side stop losses stay at the broker.

## Connection Diagnostics

On the same page click **Run diagnostics**. It repeats every check and adds ping to the broker, bars available per symbol and timeframe, the symbol specs, the broker time offset, the filling modes and the permission flags. **Copy report** copies a text report without your password.

## Smoke test (30 seconds, read-only)

```powershell
MT5TradingWorkstation.exe --mt5-smoke-test --report-file smoke.txt
```

It connects with the saved profile (or the account logged in to MT5 right now), prints the account, 3 prices, the last 10 EURUSD M15 bars and your last 10 deals, and ends with `Result: PASS` or `Result: FAIL`. It never places, changes or closes an order.

## Several accounts

Each account gets its own profile: start the app with `--profile NAME`, or click **Open profile in a new window** on the Settings page. Run one app instance per profile; the app refuses to open the same profile twice. To run several accounts at the same time, use one portable MT5 terminal per account.

## Settings

Settings (Ctrl+,, or **Settings** in the Simple view's top bar) has four tabs:

- **Account & connection**: see "Connect to MT5".
- **Data & cloud sync**: cloud sync and the trade history import (see "Cloud sync with Supabase" and "Trade history").
- **Notifications**: see "Notifications and Telegram".
- **Updates**: see "Install, update and uninstall".

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

Approve is off in Analysis-only mode and after the kill switch. In Auto mode approved strategies send their signals without asking (see "Go-Live checklist and Auto mode").

## Positions & Trades

Advanced view > Positions & Trades.

- **Trading mode**: Paper, Semi-auto, Auto or Analysis-only, then **Use this mode**. On a REAL account, Semi-auto asks you to type REAL. Auto asks you to type AUTO and, on a REAL account, needs the Go-Live approval of every strategy that is on.
- **Open**: the positions and pending orders this app opened (manual trades are not listed and never touched), with P/L, the best (MFE) and worst (MAE) move in R. **Close position** closes the selected one at the market price after a confirmation.
- **Execution events**: fills, slippage, stop loss moves, closes and failures, newest first.
- **History**: your closed trades with filters; select one to see its story, the signal's reasoning, features and decision trace, the events and your journal notes.
- **Execution settings**: max deviation, retries, entry tolerance, spread limit, paper slippage, commission and start balance; and per strategy the optional management (break-even at X R, ATR trailing, partial close, time exit). All management rules are off (0) by default.
- If the app or the PC restarts, the app finds its trades in MT5 again and keeps managing them. Your stop loss and take profit are on the broker's server, so they protect you while the app is closed.

## Kill switch

Click **Stop trading** in the status bar, the kill switch on Positions & Trades, or press **Ctrl+Shift+K**, then confirm. The app closes every position it opened, cancels its pending orders and stops new entries. Manual trades stay open. To trade again: type ENABLE on the Risk page and click "Allow approvals again" on Positions & Trades.

## Trade test (demo only)

With MT5 open on a **demo** account and Algo Trading ON, run `MT5TradingWorkstation.exe --mt5-trade-test --symbol EURUSD`. It opens the minimum lot with a stop loss and take profit, checks it in MT5, moves the stop loss, closes it, reads the closed deal back and prints every step with ✓ or ✗ and MT5's return codes. It refuses to run on a REAL account. Run it while the market is open (Monday to Friday): at the weekend MT5 refuses the order with 10018 MARKET_CLOSED. Use `--report-file report.txt` to save the report.

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

## Strategies page

Advanced view > Strategies. Both strategies are **examples with exact rules, not proven to be profitable**.

- Tick **On** to let a strategy make signals, change its settings (limits are checked), then click **Save strategy settings**. Changes apply from the next closed bar and are written to the audit log.
- **Signal filters**: minimum probability and EV, cooldown and pause after losses, spread limits, sessions, rollover, Friday close, Monday open and the news blackout.
- **Go-Live checklist**: each strategy card shows it (see the next section).
- Settings are saved per profile in `strategies.json`.

## Go-Live checklist and Auto mode

Auto sends the signals of approved strategies without asking you. Semi-auto stays the recommended mode. The checklist on each strategy card (**Check again** runs it now) has seven items, with these defaults (saved in `go_live.json`):

| Item | Passes when |
| --- | --- |
| Walk-forward backtest | the newest saved walk-forward of exactly these settings has at least 100 out-of-sample trades and a positive expectancy |
| Paper trades | at least 30 paper trades of this strategy with a positive expectancy (R) |
| Paper slippage | the average slippage is at most 2 points |
| Calibration | the real win rate is within 10 percentage points of the predicted chance, over at least 30 rated trades |
| Errors and health | no CRITICAL log line and no unresolved critical health check in the last 7 days |
| Health checks now | every health check is green now |
| Risk settings | you confirmed the risk settings with **Risk settings reviewed** and they did not change since |

1. Click **Risk settings reviewed** and confirm that you checked the risk per trade, the daily loss, the drawdown, the open risk and the open trades.
2. Click **Approve for Auto**. With every item passed a normal confirmation follows; with any item failed you must type `I ACCEPT THE RISK`, and the approval records which items failed. **Remove approval** takes it back. Changing the strategy's settings removes it too.
3. On Positions & Trades choose **Auto**, click **Use this mode** and type `AUTO`. On a demo or contest account no approval is needed (only the typed word); on a REAL account every strategy that is on needs its approval for this account and exactly these settings.

Every signal is checked against the gate again before it is sent; a blocked one expires with the reason. Auto is kept after a restart (with a warning at the start), and the risk limits, the daily-loss and drawdown stops and the kill switch work exactly as in the other modes. Every approval, removal and review is in the audit log.

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
- **Walk-forward** (tick it, choose one parameter and the values to try): the best value on each past window (in-sample) is traded on the next one (out-of-sample). It passes with at least 100 out-of-sample trades and a positive expectancy. The Go-Live checklist uses the newest saved walk-forward.
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

## AI Lab

Advanced view, Analyze > AI Lab. It lets an AI of your choice (ChatGPT, Claude or any other) study your results and suggest new strategy settings, and then checks the suggestion with a backtest before anything changes. An AI's suggestion is an idea, not proof.

1. **Export for the AI**: choose the strategy, the mode (live or paper) and the last N days (180 by default, 0 for all), then click **Export for AI**. It writes `profiles/<profile>/exports/ai_<time>/` with `trades_full.csv`, `trades_full.json` (every closed trade with all its fields, times in UTC) and `report.md` (the prompt, your statistics and breakdowns, the calibration, MFE/MAE, costs, why signals were rejected, live and paper against your latest backtests, and every strategy's settings with their allowed limits). **Copy prompt** copies only the prompt. Give the AI the files.
2. **Paste the AI's answer**: paste the reply (the app finds the JSON block in it) and click **Check suggestion**. It must have the form `{"changes": [{"strategy": ..., "params": {...}, "reason": ..., "expected_impact": ...}]}`. Unknown strategies or settings, values outside the limits, a missing reason or no real change are refused with the reason. A table shows each setting now and suggested.
3. **Backtest it**: choose the symbol and the period (the last six months by default) and click **Run comparison**: two full backtests on the same data, your current settings against the suggested ones. The suggestion counts as better with at least 30 trades, a higher expectancy in R and a max drawdown at most 1.25 times the current one plus 1 point. One period is not proof.
4. **Turn it on in Paper**: **Activate in Paper** saves exactly the tested settings, only in Paper or Analysis-only mode (it is blocked in Semi-auto and Auto), with an extra confirmation when the suggestion was not better. The change is in the audit log; a strategy approved for Auto loses that approval when its settings change.

**Ask your own AI from here (optional)**: the card "Or ask your own AI from here (optional)" sends a summary of your results to an OpenAI-compatible service and puts the answer into step 2. Tick **Use my AI endpoint**, set the **Endpoint** (default `https://api.openai.com/v1`; OpenRouter, a company gateway or a local Ollama or LM Studio work too) and the **Model** (default `gpt-4o-mini`), paste the **API key**, optionally the prices per 1M tokens, and click **Save**. Then type a **Question** and click **Ask AI**.

- It is off by default. The endpoint must be HTTPS (plain HTTP only for `localhost` or `127.0.0.1`).
- The key is kept in Windows Credential Manager (**Remove key** deletes it); the other settings are in `llm.json`.
- It sends a masked summary of at most 12,000 characters (statistics, breakdowns, calibration, MFE/MAE, costs, rejected-signal reasons, recent backtests, strategy settings and limits, your question). It never sends the trade list, a password or a key, and your account number only if you tick **Send the account number**.
- Each request is logged in the `llm` category with the model, the size, the tokens and the estimated cost.

## Dashboard

Advanced view, Trade > Dashboard: balance, equity, today's closed result, open risk and the last 30 days (net and win rate) with an up or down arrow, the equity curve of your closed trades, how much of each risk limit is used (daily loss, drawdown, open risk, open trades; amber and "(near the limit)" from 75%, red and "(limit reached)" at 100%), your open positions, the latest signals, the market bias of every watched symbol and the Go-Live readiness of the strategies that are on. It only shows; it never changes anything.

## Analytics page

Analyze > Analytics. Choose the dates, the account, symbol, strategy, mode (live or paper) and bot or manual, then click **Update**.

- **Summary**: trades, win rate, net profit and return, profit factor, expectancy (money and R), average win and loss, payoff ratio, largest win and loss, streaks, Sharpe and Sortino, max drawdown with how long it lasted, recovery factor, time to recover and costs. Under 30 trades the page warns that the numbers are mostly luck.
- **Breakdowns**: the same numbers per symbol, strategy, direction, session, hour, weekday, month, holding time, probability bucket, config, bot or manual, and mode.
- **Equity**, **Monthly returns** (a green and red heatmap with signed percentages), **R and MFE/MAE** (how far trades went for and against you, wins ▲ and losses ▼: "winners gave back 0.8 R", "4 losers were 1 R ahead first"), **Costs** (commission, swap and fees as a share of the gross profit; the spread is already inside the prices).
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

The bot works only while the app runs, and the app must reach `api.telegram.org`: where Telegram is blocked, it needs a VPN, or a proxy set in Windows for the whole system.

## Health page

Advanced view, System > Health. It shows whether the app and its connections are fine, and it is where you make a debug bundle and the soak report.

**Checks (every minute)** (**Check now** runs them at once):

| Check | Warning | Critical |
| --- | --- | --- |
| MT5 connected | disconnected or failed | reconnecting |
| Algo Trading on | off in MT5 or for the account | |
| Quotes fresh | older than 5 minutes | older than 15 minutes |
| Broker offset stable | the broker clock jumped in the last 24 hours | |
| Supabase reachable | the sync fails (n/a with cloud sync off) | |
| Sync queue | more than 1,000 rows waiting, or refused rows | more than 10,000 rows |
| Disk space | under 5 GB free | under 1 GB free |
| Log size | the logs use more than 1 GB | |
| Latency to the broker | over 250 ms | over 1,000 ms |
| PC clock | more than 30 s behind the broker | more than 120 s |
| Background workers | | a worker is frozen |

A closed market at the weekend is not a stale quote. Changes are saved at once and every check every 15 minutes; the Go-Live checklist reads them.

- **Background workers**: each worker with its state, last heartbeat, timeout and restarts. A frozen worker is logged with its stack and restarted (up to 3 times).
- **Performance**: CPU (under 3% on average while idle), memory (under 500 MB), the time to analyse a closed bar (under 1 s per 10 symbols), MT5 call times (under 1,000 ms), MT5 requests waiting (at most 20), rows waiting to sync (at most 1,000) and the start time (under 5 s). A value over its budget is a warning in the `perf` log, never critical.
- **Create debug bundle**: makes `profiles/<profile>/debug/debug-<time>.zip` with `README_DEBUG.md`, the versions, health and performance, the last 50 decision traces, the profile settings, the logs of the last 2 days and the newest 20 crash reports. Secrets, your login and name, your Windows user name, Telegram chat ids and the PIN hash are replaced with `***`; look inside before you share it anyway. **Open folder** opens it.
- **Create soak report**: after the app ran on a demo account for at least 24 hours on market days, this checks that run: 24 hours or longer, memory under 500 MB at every sample and growing less than 50 MB a day, CPU under 3% on average, MT5 calls under 1,000 ms, no closed bar over its budget, and no critical log line, crash report or frozen worker. A gap of more than 35 minutes in the saved metrics ends a run (a restart for an update does not). The report is `profiles/<profile>/reports/soak-<time>.md`.

## Logs page

Advanced view, System > Logs.

- **All** and one tab per category (`app`, `mt5`, `market_data`, `analysis`, `strategy`, `ml`, `risk`, `execution`, `position`, `sync`, `backtest`, `ui`, `notify`, `llm`, `audit`, `perf`, `update`). New lines appear live. Passwords, keys and tokens are already masked.
- **Time**: This session, Last 15 minutes, Last hour, Last 24 hours, Last 7 days or Last 30 days. Older lines are read from the saved (also zipped) files in the background; the newest 2,000 are shown together with the live ones.
- Filter by minimum level, by text ("Search messages, symbols, trace ids"; tick **Regex** for a regular expression), by **Symbol** and by **Strategy**.
- Click a line to see its full JSON entry. If it belongs to a trace, the detail is a timeline of the whole trace with the time since its first line; **Show only this trace** filters the table to it, **Show all traces** goes back.
- **Level for this category** changes the level of the open tab's category (for example `mt5` to `DEBUG`). The change is recorded in the audit log, which says what changed, for example `Audit: trading mode changed (paper → semi_auto)`.
- **Debug mode for 30 min** switches every category to DEBUG and turns itself off after 30 minutes (**Turn off debug mode** ends it sooner), so it cannot fill your disk by accident.
- **Export** saves the shown lines as `profiles/<profile>/exports/logs-<time>.jsonl` and `.csv` (masked). **Open log folder** opens the folder in Explorer.

## When something goes wrong

The app saves a crash report and shows a "Something went wrong" window with the report's location. The report contains no passwords or keys. Restart the app; if it happens again, make a debug bundle on the Health page and send it.

To check that crash reporting works: open the command palette and run **Test the crash reporter**, or run `--crash-test` (see "Command-line options").

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

## Trade history

Open Settings, tab "Data & cloud sync", and click Import history now, or use the command "Import trade history". The first import reads your whole history; later imports read only the last few days. Importing again never creates duplicates. Each trade is marked manual, bot or external (another EA).

Times are converted to UTC with your broker's clock, including its summer-time rules (most brokers are UTC+2 in winter and UTC+3 in summer). When the app learns new rules for your broker, it imports the history again once to correct older trades.

## Your data on this PC

- Database: `profiles/<profile>/data/workstation.db`.
- Backups: one a day, the last 7 kept, plus one before every database update.
- Cleanup: logs and MT5 request records older than 90 days and health and performance rows older than 30 days are removed. Trades and signals are never deleted, and nothing is deleted before it is uploaded.

## Where your files live

The app itself is in `%LocalAppData%\MT5TradingWorkstation` (replaced by every update). Your data is in `%APPDATA%\MT5TradingWorkstation`: `update_state.json` (the installed and previous version) and `profiles\<profile>\` with:

- `ui_prefs.json`: theme, view and language.
- `account.json`: login, server and terminal path of the saved account (no password).
- `risk.json`, `strategies.json`, `execution.json`: risk limits, strategy settings, trading mode and execution settings.
- `go_live.json`: the Go-Live thresholds and approvals.
- `notifications.json`: notification switches, quiet hours, Telegram chat ids and the PIN hash (the bot token is in Windows Credential Manager).
- `llm.json`: the Ask AI endpoint and model (the key is in Windows Credential Manager).
- `updates.json`: the update switches.
- `data\`: the database and its backups; `models\`: the trained models; `backtest_cache\`: MT5 history kept for backtests.
- `exports\`: CSV, PNG, AI Lab and log exports; `reports\`: daily, weekly and soak reports; `debug\`: debug bundles.
- `logs\`: one folder per category with one `.jsonl` file per day, plus `all.log`.
- `crash_reports\`: `crash_<time>.json` files and `fatal_errors.log` for native crashes.
- `instance.lock`: held while the app runs on this profile.

## Command-line options

Run them in PowerShell in the app's folder (`%LocalAppData%\MT5TradingWorkstation\current`), for example `.\MT5TradingWorkstation.exe --self-check --report-file self-check.txt`. The window has no console, so add `--report-file` to read the result.

| Option | What it does |
| --- | --- |
| `--version` | prints the version |
| `--self-check` | checks that Python, MetaTrader5, Qt, the bundled files, the MT5 helper process and the ML library load; ends with `Result: PASS` or `Result: FAIL` |
| `--crash-test` | crashes a background thread on purpose and checks that a masked crash report was written |
| `--mt5-smoke-test` | read-only connection test with the saved profile (see "Smoke test") |
| `--mt5-trade-test` | demo only: opens, changes and closes one minimum-lot trade (see "Trade test") |
| `--backtest` | replays the strategies on MT5 history and prints the report, never trades (see "Backtest from the command line") |
| `--symbol NAME` | the symbol for `--mt5-trade-test` and `--backtest` (default EURUSD; broker suffixes are found) |
| `--from YYYY-MM-DD` | the first day of `--backtest` |
| `--to YYYY-MM-DD` | the last day of `--backtest` |
| `--strategies a,b` | the strategies for `--backtest` (default: all) |
| `--report-file PATH` | also writes the report of a test command to this file |
| `--profile NAME` | the account profile (default `default`); one app instance per profile |
