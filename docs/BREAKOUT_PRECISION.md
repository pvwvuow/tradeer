# Breakout precision (0.31.1)

8 October 2026: the London breakout sold EURUSD (07:00:27 UTC) and GBPUSD (07:01:49 UTC) on
the first touch below the Asia range. Neither closed far beyond it; both were stopped out at
14:11 and 14:25 UTC when the dollar turned. Two trades, one idea (long USD).

## Changes

- **One bet per currency and direction** (filter, on by default, Settings > Filters
  `one_bet_per_currency`). A trade on another symbol that shares a currency leg with the same
  sign blocks the new signal: EURUSD sell + GBPUSD sell (both long USD), EURUSD sell +
  USDJPY buy. Any strategy, waiting, sent or open. Index and crypto symbols are not checked.
- **London breakout 1.1.0, `entry_mode`**:
  - `stop` (default, unchanged): buy stop and sell stop at the open, first touch fills.
  - `close`: no order before an M15 close beyond the range plus `buffer_atr`
    (`confirm_bars` closes in a row, default 1), then market, on the day's first confirmed
    break only, between the open and `cancel_time`. A close more than `max_chase_atr`
    (default 0.5 ATR) past the range edge is skipped. SL at the other side (max 1.5 ATR).

## To check before trusting it

Backtest both entry modes on broker history (Backtest page, london_breakout, EURUSD and
GBPUSD, at least 6 months) and compare false breaks, win rate and max drawdown. Two trades
prove nothing either way.
