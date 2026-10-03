# Progress

Continue in a new chat with: "Read docs/SPEC.md and docs/PROGRESS.md and continue."

## Completed phases

- Phase 1: Foundation, merged into `main` through pull request #1 on 2026-10-01.
- Phase 2: Observability, pull request #10, CI green, waiting for review.
- Phase 3: MT5 connection, pull request #13, CI green, waiting for review.
- Phase 4: Storage, pull request #14, CI green, waiting for review.
- Phase 5: Market data & analysis, pull request #15, CI green, waiting for review.
- Phase 6: Strategies & signals, pull request #16, CI green, waiting for review.
- Phase 7: Risk, pull request #17, CI green, waiting for review.
- Phase 8: Execution, stacked on Phase 7, in review.

## Phase 1: Foundation (pull request #1, merged)

Built: Python 3.11 project with exact pins, ruff, mypy (strict), pytest, pytest-qt, pre-commit; the desktop shell (Simple view by default, Advanced view with the grouped sidebar and 14 pages that name their phase); design tokens with a generated stylesheet, dark and light theme; status bar with the `PAPER` badge and the kill switch (inactive); command palette (Ctrl+K); `--self-check`, `--report-file`, `--profile`, `--version`; build, release and installer scripts; agent rules; the full specification in `docs/SPEC.md`.

Follow-up: the full pipeline (mypy, frozen self-check and crash test, installer, CodeQL, release-please) was enabled in `.github/workflows/` by the CI maintainer through pull request #11.

## Current phase: 8 Execution (branch `phase/08-execution`, pull request into `phase/07-risk`)

Status: built and tested against the FakeMT5 (a simulated trade server with scripted return codes). First run on your PC on Saturday 3 October (market closed): the app starts, connects and saves its settings cleanly; the demo trade test and the approvals still need an open market. Paper is still the default mode: nothing reaches your account until you switch to Semi-auto and approve a signal.

### First run on your PC (3 October 2026, 07:15 UTC, Saturday, market closed)

- ✓ The Phase 8 build starts and connects to the FIBO demo account automatically (the first connect took 3.7 s, the next ones were instant); three starts without an error, and no warning except that first slow connect.
- ✓ Paper is the mode after every start; the risk settings saved as "Custom" were still in use after a restart; the trading mode was changed 4 times and every change was audited.
- ✗ The trade test, the approvals, the crash recovery and the kill switch need an open market (MT5 answers 10018 MARKET_CLOSED at the weekend): run them on a weekday.
- Fixed: an audit line only named the action (`Audit: trading mode changed`), so the log did not show which mode was chosen. It now says what changed: `Audit: trading mode changed (paper → semi_auto)`, the changed risk settings (`profile normal → custom, settings.risk_per_trade_pct 0.5 → 1.0`); secrets are masked (`app/observability/audit_text.py`).
- Fixed: after Friday's close the cards kept "New York session → watch" until a restart, because the last bar of the week never closes in MT5 (no tick after the close). The cards are now redone once when the market closes or opens ("Market closed (weekend) → wait"); the strategies do not run again on that bar.

### What was built

- **Approval** (Signals page, spec C5): Approve asks for a confirmation with the lot and the money at risk. On the next analysis cycle the signal is re-checked against the live price (still valid, price within 0.25 R of the entry, spread at most 0.2 of the SL distance, stops level, netting) and sized again by the risk manager at the live price. A failed re-check expires the signal with the reason; nothing is sent.
- **Live broker** (`app/brokers/live_broker.py`, spec C7): buy at the ask, sell at the bid; SL and TP always in the request; the filling mode from the symbol (FOK, else IOC, else RETURN: XAUUSD at FIBO gets IOC); deviation; the strategy's magic number; comment `tw-<signal id>`. `order_check` must pass before `order_send`. Pending orders carry their expiration.
- **Retcode policy** (`app/mt5/retcodes.py`): retries only requote, price changed, off quotes, timeout, too many requests and connection, with a fresh price, at most 3 times (setting). After a timeout or a lost connection it first looks for the order by its comment, so a lost reply never opens a second trade. Every request and answer is saved in `mt5_requests` and logged in the `execution` category, with latency and slippage.
- **Paper broker** (`app/brokers/paper_broker.py`, spec C8): the same interface on live quotes, with slippage (1 point) and commission (setting); pending orders fill when crossed and expire; SL/TP on every cycle; positions, orders, deals and the paper balance survive a restart. Paper trades go to the same `trades` table with `mode = paper`. In Paper mode the risk limits use the paper balance and positions.
- **Execution engine** (`app/engine/execution.py`): opens the order, records the trade (requested and fill price, slippage, initial SL/TP, risk money, signal id), tracks MFE/MAE in R, applies the optional per-strategy management (break-even at X R + offset, ATR trailing, partial close at X R, time exit after N bars; all off by default; never inside the freeze level; the SL only tightens), cancels the other London breakout order when one fills (OCO), and syncs a closed position back from the deal history: profit, commission, swap, net, R and the exit reason (stop loss, take profit, stop out, manual, expert). The signal goes `APPROVED -> SENT -> FILLED -> MANAGED -> CLOSED` (or `FAILED`), every step in its history and trace.
- **Crash recovery**: the managed trades are saved after every cycle. After a restart or reconnect the engine resumes managing them, adopts any bot position it did not know (WARNING in the log), and a failed MT5 read never counts as "closed".
- **Kill switch** (status bar, Positions & Trades, Ctrl+Shift+K; always with a confirmation): closes every bot position, cancels every bot pending order, stops new entries (typed ENABLE on the Risk page to resume). Manual trades are never touched.
- **Positions & Trades page**: the trading mode (Paper, Semi-auto, Analysis-only; Auto waits for the Go-Live gate of Phase 13; Semi-auto on a REAL account needs the typed word REAL), the bot's open positions and pending orders with P/L, MFE and MAE, Close position (with a confirmation), the execution events, and the execution and management settings (`execution.json`). Every change is in the audit log.
- **Status bar**: the mode badge follows the saved mode; the bot state shows "running, N open", "stopped (kill switch)" or "disconnected".
- **`--mt5-trade-test --symbol EURUSD`** (spec I4): refuses REAL accounts and investor logins. On DEMO it opens the minimum lot with SL and TP, checks the position and its magic number in MT5, moves the SL, closes, reads the closed deal back (profit, commission, swap) and prints every step with its return codes (logged in `execution` and `mt5`).
- Fixed from Phase 7: the risk manager now uses the broker's symbol name (for example `EURUSD.m`) for `order_calc_profit`, `order_calc_margin` and the per-symbol limit. Bot trades from the history import are now labelled "bot" (their magic numbers are passed to the import).

### Checklist

- ✓ An approved signal opens with SL/TP and the strategy's magic number, and the closed deal (profit, commission, swap, exit reason, R) is synced back: FakeMT5 end-to-end tests (`tests/unit/test_execution_engine.py`, `test_pipeline_execution.py`)
- ✓ Retcode policy: a requote is retried with a fresh price, invalid stops are never retried, a lost reply is found by its comment (unit tests)
- ✓ XAUUSD with IOC-only filling is sent with IOC (unit tests, trade test)
- ✓ Crash recovery: a new engine resumes the saved trade and adopts an unknown bot position without sending anything (`test_crash_recovery_resumes_managing_and_adopts_unknown_bot_positions`); a failed read never closes a trade
- ✓ Kill switch closes bot positions, cancels bot orders, stops new entries and spares manual trades (unit and UI tests)
- ✓ Paper broker: fills, slippage, commission, pending orders, expiry, SL/TP with gaps, saved state, paper risk picture (unit tests)
- ✓ `--mt5-trade-test` passes on the FakeMT5 DEMO account and refuses a REAL one before any request (unit tests)
- ✓ Architecture tests: only the live broker calls `order_send`/`order_check`; the engine never touches the gateway; the paper broker never reaches MT5 trading
- ✗ **Not yet run on your MT5 demo account**: the acceptance check (a tiny test trade opens with SL/TP, shows the right magic number in MT5, is modified, closed, and the deal is synced back) needs your PC; see "Test on your PC"
- ✗ Management rules and paper SL/TP run on every analysis cycle (a few seconds), not on every tick

### Test on your PC

1. Download the build artifact of this pull request, unzip, run `MT5TradingWorkstation.exe --self-check`.
2. With MT5 open on your **demo** account and Algo Trading ON, run `MT5TradingWorkstation.exe --mt5-trade-test --symbol EURUSD`. Expected: steps 1 to 5 all ✓ and `Result: PASS`; in MT5 (History tab) a 0.01-lot trade with magic 26070099 and comment `tw-trade-test`. Then run it again with `--symbol XAUUSD`: the report must say `filling IOC` and pass.
3. Start the app: the status bar shows `PAPER` and "Bot: running". Open Advanced > Positions & Trades: mode Paper.
4. When a signal waits (Signals page), select it and click Approve, then Yes. Within a few seconds its state is "open (managed)" and it appears on Positions & Trades as a paper position. Nothing appears in MT5.
5. Switch the mode to Semi-auto (demo: a confirmation only). Approve the next signal: the trade appears in MT5 with its SL, TP, magic 26070001 or 26070002 and a `tw-` comment.
6. Crash-recovery test: with that trade open, close the app (or end it in Task Manager), start it again: Positions & Trades lists the trade again ("Resumed managing 1 bot trade" in the events) and nothing new is sent.
7. Press Ctrl+Shift+K and confirm: the bot trade is closed, the status bar says "Bot: stopped (kill switch)", the Risk page says new entries are stopped. Your manual trades stay open.
8. After the trade closed, the signal says "closed" with net profit and R, and Settings > Data shows the trade with commission and swap after the next history import.
9. If anything fails, send the report of step 2 and the newest files from `logs/execution/`, `logs/mt5/` and `logs/all.log`.

### Limitations

- SL/TP of paper positions and the management rules are checked on every analysis cycle (every few seconds), not on every tick; the server-side SL protects live positions between cycles.
- On a netting account a second trade on a symbol with an open bot position is blocked (not reduced).
- A paper position is valued with the profit per 1.0 of price read when it opened; for a cross-currency symbol this ignores later changes of the conversion rate.
- The full Positions & Trades history (filters, detail drawer, journal) is Phase 12; Simple-mode cards are Phase 9; Telegram remote kill is Phase 12.

## Phase 7: Risk (branch `phase/07-risk`, pull request #17)

Status: built and tested against the FakeMT5; not yet run on your PC.

### What was built

- **Position sizing** (`app/domain/sizing.py`, spec C6): lot = capital (equity or balance) x risk% / (loss per lot at the SL + expected commission per lot), rounded down to the volume step and clamped to the maximum. If even the minimum lot risks more than allowed, the trade is rejected. The loss per lot comes from MT5's `order_calc_profit` (`app/mt5/risk_reads.py`), so JPY, gold and EUR accounts are converted by MT5 itself; the tick value is never used (ADR 60). Every line of the calculation goes into the decision trace and the `risk` log.
- **Commission**: estimated from your imported deals of the symbol (all commissions / lots opened); without history, the "commission per lot" setting (0 by default).
- **Limits** (`app/risk/limits.py`): trading stopped, daily loss (realized + floating vs start-of-day equity), drawdown (trailing from the equity high, or static), open trades total / per symbol / per strategy, trades today, total open risk, currency exposure, the broker's stops level, and the margin level after the trade (`order_calc_margin`). Manual trades and other EAs count when "count manual trades" is on (default); the bot never touches them.
- **Currency exposure** (`app/risk/exposure.py`): each position is long its base and short its quote currency, weighted by the money it risks (long EURUSD + long GBPUSD = 2x short USD).
- **Persisted limit state** (`app/risk/limits_state.py`): trading day, start-of-day equity, equity high, stop state; saved per account in the local database and loaded after a restart. A daily-loss stop ends with the next trading day; a drawdown stop and a manual stop stay until you type ENABLE. A damaged saved state stops trading instead of resetting it. Deposits and withdrawals move the drawdown basis, so they are not counted as profit or loss.
- **Risk manager** (`app/risk/risk_manager.py`): runs in the analysis thread, reads account, positions and today's deals in one gateway call, refreshes the usage every 30 s, records `risk_events` (daily_limit, dd_limit, exposure_block, margin_block, kill_switch, re_enabled).
- **Pipeline**: the risk step replaced the Phase 6 placeholder. A signal that breaks a limit is `RISK_REJECTED` with the reason; one within every limit waits for approval with its lot and risk money (shown in the Signals feed and saved with the signal).
- **Profiles** (`app/risk/settings.py`, `risk.json`): Conservative (0.25%), Normal (0.5%, the spec defaults), Prop-firm (0.5%, 4% daily, 8% static drawdown). Hard caps: at most 1% per trade, the risk per trade never above the total open risk.
- **Magic numbers** per strategy (`app/strategies/registry.py`): trend pullback 26070001, London breakout 26070002.
- **Risk page**: status, plain-language risk ("each new trade can lose about X"), limit usage, currency exposure, risk events, profile picker, the settings form, Stop new entries, Re-enable trading (typed ENABLE). Every change in the audit log.

### Checklist

- ✓ Sizing tests pass for all symbol types: EURUSD, USDJPY, XAUUSD (with FIBO's wrong tick value), a EUR account, min-lot rejection, commission, rounding down, max lot (`tests/unit/test_sizing.py`)
- ✓ Limits survive a restart: a hit daily limit still blocks after a new risk manager loads the state (`test_a_hit_daily_limit_survives_a_restart`), and a damaged state stops trading
- ✓ Limits, exposure, margin, stops level, daily rollover, drawdown stop and re-enable (unit tests)
- ✓ Pipeline: passing signals are sized and saved with their lot; breaking a limit gives `RISK_REJECTED` with a full trace (unit tests)
- ✓ Profiles and hard caps (unit tests); Risk page (UI test runs in CI)
- ✓ Architecture tests: risk math is pure, risk code never sends or checks orders, sizing never reads the tick value
- ✗ Not yet run on your PC with live FIBO data
- ✗ `order_check`, the full kill switch (close bot positions, cancel orders) and the approval re-check arrived with orders in Phase 8

### Test on your PC

1. Start the app, connect, open Advanced > Risk: the status says "Trading allowed", the usage table shows your daily loss, drawdown, open risk and margin level, and "Each new trade can lose about X" matches 0.5% of your equity.
2. Open a small manual trade with a stop loss in MT5: within 30 s Open trades and Open risk include it, and Currency exposure shows its currencies.
3. Wait for a signal (or a London morning): its trace shows "loss per lot at SL", "lot size" with the full calculation and every limit. For XAUUSD the loss per lot must match MT5's own profit calculator, not the tick value.
4. Click Stop new entries, restart the app: the page still says "New entries stopped". Click Re-enable trading and type ENABLE.
5. If anything looks wrong, send the newest files from `logs/risk/` and `logs/all.log`.

### Limitations

- The start-of-day equity is exact only when the app ran through the broker's midnight; otherwise it is estimated from the balance (marked "estimated"), which can only count more loss, never less.
- A position without a stop loss counts with its current loss as its risk.
- The commission estimate needs imported deals of the symbol; set "commission per lot" for a raw-spread account without history.

## Phase 6: Strategies & signals (branch `phase/06-strategies-signals`, pull request #16)

Status: built and tested on synthetic markets; not yet run on your PC. Signal only: nothing is sent to MT5.

### What was built

- **Strategy interface** (`app/strategies/base.py`, spec C4): name, version, params model, required history, entry timeframe, `evaluate(ctx)` with every rule as a condition (value, threshold, pass or fail) and a setup state (none, forming, ready) for the scanner. Strategies are pure; an architecture test enforces it.
- **Two example strategies** with exact rules, labelled "not proven to be profitable": trend pullback (H1 EMA50/EMA200 trend with ADX, M15 pullback into the EMA20 zone, RSI back over 50, SL beyond the swing or 1.5 ATR, max 3 ATR, TP 2R) and London breakout (Asia range 00:00 to 07:00 London time, 0.5 to 1.5 ATR(D1, scaled to the range hours) wide, buy stop and sell stop as an OCO pair at 08:00, SL at the other side, max 1.5 ATR, TP 1.5R, cancelled at 11:00).
- **Market context** (`app/strategies/context.py`): closed entry bars and only fully closed higher-timeframe bars at the moment the entry bar closed.
- **Signals and the state machine** (`app/domain/signals.py`, spec C5): NEW, FILTERED_OUT, RISK_REJECTED, PENDING_APPROVAL, APPROVED, USER_REJECTED, EXPIRED, SENT, FILLED, FAILED, MANAGED, CLOSED. Any other transition raises an error.
- **Probability baseline** (`app/domain/probability.py`): the strategy's win rate with a Wilson 95% interval, "unknown" below 30 resolved signals; expected value in R with the spread as a cost.
- **Filters** (`app/engine/filters.py`): probability, EV, one pending signal per symbol, strategy and side, open position, cooldown after a loss, pause after losses, spread vs ATR and vs SL, session, rollover, Friday close, Monday open, news blackout, fresh data, market open, symbol trade mode, still valid.
- **Pipeline** (`app/engine/signal_pipeline.py`): on each new closed entry bar, context, strategy, features, probability, EV, filters, risk (placeholder until Phase 7), decision, every step in the decision trace (`app/observability/decision_trace.py`). Runs in the analysis thread; dismiss and expiry are queued there, so all writes happen in one thread.
- **Storage** (`app/storage/signal_store.py`): `strategy_configs`, `signals` and `decision_traces` rows in one transaction with stable ids; saved signals load again after a restart and expire on time.
- **Scanner** (`app/analysis/scanner.py`): symbols ranked by setup state, then probability x EV (rules passed while no probability is known).
- **Signals page**: feed with rejected signals and their reasons, state filter, decision trace, scanner tab, Dismiss; Approve is off until Phase 8.
- **Strategies page**: one card per strategy with on/off, its rules, an auto-generated settings form (from the params model), and the signal filters; saved per profile in `strategies.json`, every change in the audit log.

### Checklist

- ✓ Strategy interface and 2 example strategies with exact rules (unit tests on synthetic markets, long and short)
- ✓ Pipeline, state machine, filters, scanner (unit tests)
- ✓ Every signal has a full decision trace (acceptance test `test_every_signal_has_a_full_decision_trace`: every stage present, saved and read back)
- ✓ One signal per symbol, strategy and bar; restarts never duplicate a signal (unit test)
- ✓ Dismiss and expiry are saved and survive a restart (unit test)
- ✓ Signals and Strategies pages (UI tests run in CI)
- ✗ Not yet run on your PC with live FIBO data
- ✗ Probability stays "unknown" until 30 signals are resolved, which needs trades (Phase 8); the probability and EV filters are recorded as "not applied" until then
- ✗ Order execution (Phase 8) is not built; Approve stays off (risk sizing arrived in Phase 7)

### Test on your PC

1. Start the app, connect, open Advanced > Strategies: both strategies are on. Change a value, click Save, and check the audit entry in Logs.
2. Leave the app running through a London morning (07:00 to 08:00 London time): at the open the London breakout shows a buy stop and a sell stop for each symbol whose Asia range fits, or a filtered-out signal with the reason.
3. Open Advanced > Signals, select a signal and read its trace. Dismiss a waiting signal; it shows "dismissed" within a few seconds.
4. Restart the app: the signals are still there, and waiting ones expire on time.
5. If anything looks wrong, send the newest log files from the log folder.

### Limitations

- The strategies are examples. They are not proven to be profitable and must be backtested (Phase 10) before anyone relies on them.
- Signals are checked every M15 close only for symbols in the watchlist.
- A changed setting applies from the next closed bar; signals already made keep the settings they were made with (config id).

## Phase 5: Market data & analysis (branch `phase/05-market-analysis`, pull request #15)

Status: pull request #15 into `phase/04-storage`, waiting for review. Updated after four runs on your PC (see below).

### Found in your first real run (fixed)

Your log (FIBO Group demo, 2 October 2026) showed the connection, the history import (11 deals, 5 trades), the broker time (UTC+2/+3) and all 3 cards working. It also showed 4 problems:

1. **The window froze for 12 s and 16 s** while MT5 answered slowly (connecting took 11.4 s, the first bar download 15.7 s). Even the watchdog stopped, so the MetaTrader5 package blocks the whole Python process while it waits. Fix: the package runs in its own helper process (ADR 46). A call that hangs for 120 s restarts the helper and the app logs in again.
2. **95% of the log was one Qt warning** (`QFont::setPointSize: Point size <= 0 (-1)`, 956 times). Fix: font sizes in pt instead of px, and a repeated message is logged once a minute with a count (ADR 48).
3. **GBPUSD and XAUUSD always said "wait".** MT5 stores the minimum spread of each bar, and the check compared the live spread with those minimums. Fix: the last closed M5 bar's spread is compared with the typical bar spread of this hour (ADR 47). The log line of each card now also says why.
4. **Trade times were saved in server time**, because no fresh price had arrived when the history import ran ("offset unknown (market closed)" although the market was open). Fix: the import uses the broker clock the analysis measured or saved, and runs again by itself once the clock is measured.

### Second real run (2 October 2026, 10:42 UTC)

Confirmed on your PC: no font warnings at all, GBPUSD now says "watch" with a reason instead of always "wait", and the history import used the saved broker clock (5 trades corrected to UTC). The freeze fix could not be judged: MT5 answered fast this time (connected in 0.07 s), so there was no slow call. Found and fixed:

1. **Diagnostics showed 0 bars everywhere.** The terminal refused one request for 100,000 bars. Fix: the probe asks again with fewer bars and shows the MT5 error if nothing comes back (ADR 50).
2. **The first XAUUSD card judged bars two hours old.** MT5 answered with the bars it had on disk and caught up two seconds later; that first card said "wait, spread 3.2x typical". Fix: while a symbol's bars trail the live price, its card says LOADING and waits (ADR 49).
3. **A card did not follow a new evaluation of the same bar** (for example news added by hand). Fix: cards follow every new analysis.
4. **The log could not show that the MT5 helper runs.** Fix: one log line per start, with the pid and the MetaTrader5 version.

### Third real run (2 October 2026, 11:52 UTC)

Confirmed on your PC: the MT5 helper process starts (pid 29836, MetaTrader5 5.0.6090), the app connected in 0.06 s, the history import found nothing new (the trade times stay in UTC), and all 3 cards appeared 2 s after the start, each with a reason. With debug logging on, every MT5 call took under 20 ms. Not shown yet: the Diagnostics bar counts, the LOADING card (MT5 had every bar ready) and a slow MT5 call. Found and fixed:

1. **MT5 call times showed only 0, 15 or 16 ms.** Before Python 3.13, `time.monotonic()` on Windows ticks in 15.6 ms steps. Fix: the gateway measures with `time.perf_counter()` and logs tenths of a millisecond.

### Fourth real run (2 October 2026, 12:32 UTC, Diagnostics report)

Confirmed on your PC: the History lines show real bar counts (EURUSD M15 50,000+, H1 48,189, H4 15,110, D1 5,921; GBPUSD and XAUUSD alike), so asking again with fewer bars works (ADR 50). Found:

1. **Fixed: "Broker time: offset unknown" while the market was open.** The prices were read before the history probes and compared with the clock after them, and the probes now download deep history, which can take minutes. Fix: the prices are read again right before the offset is measured.
2. **For Phase 7: XAUUSD reports a tick value of 0.1 USD** for a 0.01 tick with contract size 100, while FIBO's published specification says 1 USD per point. Sizing from the tick value would risk 10 times too much on gold. Position sizing must use `order_calc_profit`, as the spec says, never the tick value.
3. **For Phase 8: XAUUSD allows only IOC filling** (EURUSD and GBPUSD allow FOK and IOC). The report now names the filling modes.

### What was built

- Closed bars only: `copy_rates_from_pos(symbol, tf, 1, n)` for M5, M15, H1, H4 and D1, cached per symbol and timeframe, with only the new bars read after the first download (`app/mt5/market_data.py`).
- One cheap MT5 request every 2 s reads each watched symbol's price and newest closed M5 bar. A symbol is analysed again only when a new M5 bar has closed (`app/engine/market_watch.py`, thread `market-analysis`, watched by the watchdog).
- Broker time: the offset to UTC is measured from fresh ticks, with US or EU summer-time rules written out in code (Windows has no tz database). Everything is stored in UTC; the trading day is the broker day (`app/core/clock.py`). The measured clock is saved per server.
- Data checks with logging: missing bars, zero-volume bars, spikes over 10x ATR, stale prices, symbols not trading, late bars and broker time jumps. Weekend gaps are normal. Errors skip the evaluation and the card says "data problem".
- All C3 modules in `app/analysis/` (pure, numpy only): indicators, multi-timeframe trend matrix with a bias of -100 to +100 and reasons, market structure (confirmed swings, HH/HL/LH/LL, BOS and CHoCH, no look-ahead), key levels (swing clusters, previous day and week, session highs and lows, round numbers, distance in ATR), volatility (ATR percentile over 100 days, ADR used today, regime), sessions and the session clock, correlation matrix, currency strength meter, spread monitor, candle patterns (information only) and a plain-language analysis card per symbol.
- Economic calendar: manual entry, CSV import, and `CalendarExporter.mq5` (full source, an MT5 service) that writes the MT5 calendar to `Common/Files/tradeer_calendar.csv` every 5 minutes in UTC. The app reads the file every 3 minutes when it changed and saves the events in `calendar_events`. High-impact news within an hour means "wait" on the card.
- The Market page: watchlist (up to 10 symbols, saved per profile), analysis cards, trend matrix, an interactive pyqtgraph chart (timeframe switch, zoom and pan, crosshair, EMA 20/50/200, levels, swings, session shading), correlation, currency strength and the calendar with countdowns.
- The status bar shows the session clock and the next high-impact news.
- Phase 4 limitation fixed: history import now converts deal times with the broker's summer-time rules, and imports everything again once when those rules change.
- The opportunity scanner (ranking by setup state and probability x EV) needs strategies and the probability model, so it arrives with Phase 6.

### Checklist

- ✓ Analysis cards for 3 symbols update on closed bars: `tests/unit/test_market_watch.py` runs the real loop against FakeMT5 through the gateway; the same bar is not analysed twice, and the next closed M5 bar renews all 3 cards.
- ✓ Indicators match hand-calculated values and a step-by-step Wilder ADX reference.
- ✓ Market structure never looks ahead: the result for the first k bars equals the full result cut at bar k.
- ✓ Summer-time dates for the USA and the EU (2005, 2024, 2026), UTC conversion and the broker day are unit-tested.
- ✓ Data checks, levels, volatility, sessions, correlation, currency strength, spread, patterns, card rules, calendar CSV and store are unit-tested.
- ✓ Architecture tests: `analysis`, `calendar`, `engine` and `core` never import Qt or MetaTrader5, the analysis is pure, and the market-data code never trades.
- ✓ In the sandbox: 314 unit tests passed, 3 skipped (the Market page UI tests run in CI). New tests run the helper process for real: package shapes, a hanging call that restarts it, a crash, a missing package, the gateway beating while it waits, a postponed heartbeat, the repeat filter, the spread rule from your account and the history import running again. Second-run tests: a refused 100,000-bar request, bars that trail the price, the 60 s warning, a closed market, the LOADING card and a card that follows a calendar change. Third-run test: a 3 ms call is no longer reported as 0 ms. Fourth-run test: the broker offset is found after history probes that take minutes.
- ✓ CI on pull request #15: `ruff check`, `ruff format --check`, `mypy` (strict) and pytest on Linux and Windows are green, including the Market page UI tests (pyqtgraph 0.14.0 with PySide6 6.10.3); the build job builds the app with the bundled `CalendarExporter.mq5` and runs the frozen `--self-check` and `--crash-test`. The first run found 2 mypy errors in `levels.py`; they were fixed. The fixes from your first three runs are green on all three jobs too.
- ✗ `CalendarExporter.mq5` was not compiled here (no MetaEditor). Compile it on your PC (see below).
- ✓ First run against a real MT5 demo account (your log): connection, history, broker time and 3 cards worked; the 4 problems above are fixed.
- ✓ Second run on your PC: no font warnings, GBPUSD no longer stuck on "wait", trade times in UTC.
- ✓ Third run on your PC: the helper process starts (pid in the log), every MT5 call took under 20 ms, the history import stayed in UTC, and 3 cards with reasons appeared 2 s after the start.
- ✗ The freeze fix is not proven on your PC yet (no slow MT5 call happened in the second or third run). Step 6 below causes one.
- ✓ Fourth run on your PC: Diagnostics shows real bar counts (50,000+ M15 bars per symbol).
- ✗ Not checked on your PC yet: the LOADING card (step 6) and the broker time in Diagnostics after this fix (step 7).

### Test on your PC

1. Start the app, connect MT5, switch to the Advanced view and open Market. Within a few seconds 3 cards (EURUSD, GBPUSD, XAUUSD) show a headline. Note the "Updated at the HH:MM M5 close" line; after the next 5-minute close it changes.
2. Open the Chart tab: switch M5 to D1, drag to pan, use the wheel to zoom and move the mouse for the crosshair.
3. Open the Calendar tab and click Install MT5 exporter. In MT5 press F4 (MetaEditor), open Services > CalendarExporter.mq5 and press F7 (Compile). Back in MT5, Navigator > Services, right-click CalendarExporter > Add service, then start it. Click Read MT5 calendar now: upcoming events appear with countdowns.
4. Add a USD high-impact event 30 minutes from now by hand: the EURUSD and XAUUSD cards say "wait" after the next bar.
5. Check the status bar: the session clock and the next news.
6. Close MT5 completely (File > Exit), then start the app: it starts MT5 itself, so the first calls are slow. While the cards load for the first time, move the window and switch pages: it must not freeze. Cards may show LOADING for a few seconds. In Task Manager you see a second `MT5TradingWorkstation` process (the MT5 helper). Then send `all.log` again: lines like "MT5 ... was slow" are fine, but there must be no "Worker ... did not respond".
7. Press Run diagnostics on the Connection page and copy the report: the History lines show real bar counts (for example "50,000+ bars"), and while the market is open the Broker time line says "server time is UTC+3". The first run can take longer while MT5 downloads deep history.

### Limitations

- The analysis card is information only and never a trade signal. Signals arrive in Phase 6.
- The session clock uses FX hours (Sunday to Friday, New York 17:00). Symbols with their own hours (indices) show "not trading right now" when their prices stop.
- The broker clock assumes UTC+2/+3 (New York close) until the first fresh price arrives; the card says so.
- The chart draws the bars kept by the analysis (600 per timeframe); deeper history arrives with backtesting.

## Phase 4: Storage (branch `phase/04-storage`, pull request #14)

Status: pull request #14 into `phase/03-mt5-connection`, CI green, waiting for review.

### What was built

- A local SQLite database per profile (`profiles/<profile>/data/workstation.db`) in WAL mode, with frozen migrations, checksums and a backup before migrating.
- All E2 tables locally and in Supabase, plus the local `outbox`, `sync_state`, `mt5_deals` and `mt5_orders` tables.
- A transactional outbox and a background cloud sync: batches of 200, backoff from 2 s to 300 s, paused-project handling, refused rows parked with "Retry refused rows".
- Supabase SQL in `supabase/`: schema, Row Level Security, four views (`security_invoker`) and a cleanup function.
- Email sign-in and sign-up with the anon key only. The refresh token is kept in Credential Manager and the password is never saved.
- Log entries of level WARNING and higher and audit entries are saved in `app_logs` and `audit_log` and synced.
- Trade history import (deals and orders): trades are rebuilt per position and marked manual, bot or external.
- Daily backups (last 7 kept) and a local cleanup that never deletes trades, signals or rows waiting to upload.
- Settings has a new "Data & cloud sync" tab, and the status bar shows the cloud state.
- Supabase is called with httpx instead of supabase-py (ADR 30, needs your approval).

### Checklist

- ✓ Offline writes sync later without duplicates (unit tests with a fake Supabase: offline then online, lost response, refused rows, expired token, paused project).
- ✓ Migrations on a new and an existing database, running twice, changed checksum and a newer database (unit tests).
- ✓ Importing history twice creates no duplicates (unit tests with FakeMT5).
- ✓ SQLite, `schema.py` and `supabase/schema.sql` agree (unit test).
- ✓ In the sandbox: 225 unit tests passed, 3 skipped (the httpx client, UI and integration tests run in CI).
- ✓ CI is green on this pull request: `ruff check`, `ruff format --check`, `mypy` (strict) and pytest on Linux and Windows, including the httpx client, Data page and log storage tests; the build job builds the app and runs the frozen `--self-check` and `--crash-test`. The first run had found 4 mypy errors and a Windows-only file lock on a damaged database; both were fixed.
- ✗ Not tested with a real Supabase project (see "Test on your PC").
- ✗ The SQL files have not been run on a real Postgres database.
- ✓ (Checked in Phase 5) History import ran against your real FIBO demo account: 11 deals, 10 orders, 5 trades.
- ✓ (Fixed in Phase 5) Server time to UTC now follows the broker's summer-time rules.

### Test on your PC

1. Create a free Supabase project and run `supabase/schema.sql`, `rls.sql`, `views.sql` and `cleanup.sql` in its SQL Editor.
2. Start the app, open Settings, tab "Data & cloud sync", paste the project URL and the anon key, then click Create account or Sign in.
3. Connect MT5 and click Import history now. Note the trade count, click it again: the count must not change.
4. Turn the internet off for a few minutes while the app runs. The status bar must show "Cloud: offline" with rows waiting.
5. Turn the internet back on. The status must go to "Cloud: up to date", and in the Supabase Table Editor every row must appear once.

### Limitations

- Sync goes one way, from the PC to the cloud. Restoring from the cloud is not built.
- The daylight-saving limitation was fixed in Phase 5.
- Supabase is reached with httpx, not supabase-py (ADR 30).

## Phase 3: MT5 connection (branch `phase/03-mt5-connection`, stacked on pull request #10)

Built:

- `MT5Gateway`: one thread owns every call to the real `MetaTrader5` package, through a queue with a timeout on every call, a watchdog heartbeat and a log record (masked arguments, duration, error) for every request in the `mt5` category.
- `FakeMT5` in `tests/fakes/` for tests only. An architecture test fails if any module except `app/mt5/gateway.py` imports `MetaTrader5`, or if `app/` imports the fakes.
- The Test-connection checklist (spec C1, I3): terminal found, terminal running, login, account info, broker connection, Algo Trading, account trading permission, symbols, live quotes and history, each with the real value or a plain-language fix. `last_error()` is mapped to friendly messages (wrong password or server, IPC errors, MT4 or 32-bit terminal, not logged in, no connection), with a same-user and same-privilege hint.
- Account details shown: broker, server, login, name, `DEMO` / `REAL` / `CONTEST`, currency, balance, equity, leverage, hedging or netting, stop-out level and mode, terminal build.
- Investor password: detected from `account_info().trade_allowed`, switches to Analysis-only (status bar badge `ANALYSIS-ONLY`).
- Account profiles: `profiles/<name>/account.json` without the password; the password lives in Windows Credential Manager (`keyring`) and is registered with the log masker. One instance per profile (OS file lock); another profile opens in a new window. Auto-connect on start when a profile is saved.
- Terminal discovery: program folders, `%APPDATA%\MetaQuotes\Terminal\*`, registry, running processes; a server dropdown from the terminal's known servers; Browse as fallback.
- Connection service: heartbeat every 5 s, status bar text, reconnect with exponential backoff (2 s to 120 s) after a lost connection, a CRITICAL alert if positions are open while disconnected. A failed first connect is never retried on its own.
- Connection page (Advanced view, System, Settings): profile, terminal and account cards, Connect, Re-check, Disconnect, live bid/ask of 3 symbols every second, Run diagnostics, Copy report.
- Connection Diagnostics (spec I4): every checklist step plus ping, bars per symbol and timeframe, symbol specs, broker time offset and permission flags; the report never contains the password.
- `--mt5-smoke-test` (spec I4): read-only, prints the account, 3 ticks, the last 10 EURUSD M15 bars and the last 10 deals, exit code 0 or 1.
- Command palette: "Connect to MT5", "Run connection diagnostics".
- New dependency: `keyring==25.7.0` (the build adds its Windows backend as a hidden import).

Acceptance checklist (spec G3 phase 3):

- ✓ Gateway, checklist, diagnostics, smoke test, connection service, profiles, credentials, single-instance lock and terminal discovery pass their unit tests against `FakeMT5` in the agent sandbox (Python 3.12, without PySide6, loguru or keyring).
- ✓ The read-only tools never call `order_send` or `order_check` (tests check every call `FakeMT5` received).
- ✗ **The real connection is not verified.** The spec criterion is that the downloaded build connects to your real MT5 demo account and shows real broker, login, balance and live bid/ask. Only you can check this on your PC (see the pull request, "Test on your PC").
- ✓ CI is green on this pull request: `ruff check`, `ruff format --check`, `mypy` (strict) and pytest on Linux and Windows, including the Qt tests of the Connection page and the integration test of the request log; the build job builds the app and runs the frozen `--self-check` and `--crash-test`.
- ✗ Investor mode, reconnect and the open-position alert are verified with `FakeMT5` only.
- ✗ Partly Windows-only: the test that compares the copied constants with the real package runs only in the Windows CI job. Terminal discovery and Credential Manager are tested with temporary folders and an in-memory store, not with a real installed terminal or the real vault.

## Phase 2: Observability (pull request #10, in review)

Built:

- Structured JSON logs through loguru with 17 categories (the 16 of spec E3 plus `update` for spec J4), each with its own files and its own level that can change at runtime.
- Files `logs/<category>/<date>.jsonl` and a readable `logs/all.log`, non-blocking (`enqueue=True`), rotated daily and at 20 MB, zipped, kept 30 days, total size capped at 500 MB.
- Every record has UTC time, level, category, module, function, line, thread, session id and, when set, trace id, signal id, trade id, ticket, symbol and strategy.
- Trace context (`trace()`, `propagate()` for threads).
- Secret masking in every log line, field, stack trace and crash report.
- Debug mode: all categories at DEBUG for 30 minutes, then automatic return.
- Crash handler: `sys.excepthook`, `threading.excepthook`, the Qt message handler and `faulthandler` write `crash_reports/crash_<time>.json` (masked stack, last 200 log entries, state, versions, OS) and open a friendly dialog.
- Watchdog: heartbeats, freeze detection with the stuck thread's stack, CRITICAL log, restart callback. The UI thread is watched from the start.
- Basic Logs page: live tail, filters (category, level, text or regex, one trace), JSON detail, level per category, debug mode, open folder.
- Startup log (versions, OS, profile, package versions, defaults) and audit entries for log level and debug mode changes.
- `--crash-test`: forces a crash and checks the report and the logs for leaked secrets; exit code 0 or 1. The build script runs it on the frozen exe.
- Command palette: "Toggle debug mode", "Open log folder", "Test the crash reporter".

Acceptance checklist (spec G3 phase 2):

- ✓ A forced exception produces a crash report: unit tests, the Qt tests and the end-to-end `--crash-test` integration test pass in CI on Linux (Python 3.11, real loguru and PySide6) and on Windows (`windows-latest`).
- ✓ Secrets are masked: unit tests cover token formats, key/value pairs, URL passwords, registered values, structured fields, stack traces and crash reports; the integration tests check the real log files and crash report; a negative test proves the crash test fails when masking is broken.
- ✓ The full pipeline is enabled on `main` and runs on this pull request after the sync: `ruff check`, `ruff format --check`, `mypy` (strict), pytest with coverage (Windows `ci` job) and the frozen `--self-check` + `--crash-test` build job (see maintainer PR #11).

## Known issues

- Resolved: the staged `ci/workflows/` folder is gone; `.github/workflows/` is maintained by the CI maintainer agent, so Brain does not need the `workflow` permission.
- No lock file yet; direct dependencies are pinned exactly.
- Tick freshness and data checks arrived in Phase 5 (Market page); the connection checklist itself still only checks that history exists and warns when "Max bars in chart" is low.
- "Start MT5 automatically" and "Start the app with Windows" (spec I5) are not built yet.
- The probability is "unknown" until a strategy has 30 resolved signals.
- FIBO's XAUUSD tick value (0.1 USD) disagrees with its contract size and published point value (1 USD): Phase 7 sizes positions with `order_calc_profit` and never reads the tick value (ADR 60).
- Profiles cannot be switched inside a running window; another profile opens in a new window.
- Health checks, performance metrics, the debug bundle and the full Logs page (trace timeline, time filters, export) are Phase 14 items.

## Next steps

1. Review and merge the stacked pull requests in order: Phase 2 (#10), Phase 3 (#13), Phase 4 (#14), then Phase 5 (#15). Each one is retargeted to `main` after the one before it is merged.
2. Run "Test on your PC" from the Phase 3, 4 and 5 pull requests against your MT5 demo account and report the result.
3. Run "Test on your PC" from the Phase 6 pull request during a London morning, and from the Phase 7 pull request (Risk page).
4. Run "Test on your PC" from the Phase 8 pull request on your demo account on a weekday, while the market is open (`--mt5-trade-test`, then a paper and a Semi-auto approval, the restart and the kill switch).
5. Phase 9 (Simple Mode: the Home screen with the Trade Suggestion Card, plain-language status and the Stop button) after Phase 8 is reviewed.
