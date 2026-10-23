# Trendline break (NAS100 and US30, M5)

The owner's own method, built as strategy 7 (`trendline_break`, magic 26 070 011). Version
1.1.0 (app 0.54.0). Off by default. Not backtested yet: run the backtest on both symbols
with the real commission before it trades real money.

## The rules

- Symbols: US30 and NAS100 (`US30.p`, `NAS100.p`, `USTEC`, `DJ30`, `WS30`, `US100` too). Any
  other symbol is skipped. Change the list in the strategy's `symbols` setting.
- Timeframe M5. The trendline runs through the last two swing highs (to buy) or lows (to
  sell). A closed M5 bar that cuts the line for the first time is an instant market entry.
- First entry: the line breaks against the last trend (the trend turns). Pullback entry: the
  line is a correction inside the trend and breaks with it. Every later correction is another
  entry. There is no limit of its own on open trades (the strategy is `stackable`).
- Stop: behind the extreme of the move the line contained (lowest low to buy, highest high to
  sell) plus 0.1 ATR; it must be 0.5 to 4 ATR away.
- Half is closed at 2R and the stop of the rest goes to the entry (risk free). The rest is
  closed when the trend turns: a close beyond the last higher low (lower high) that formed
  after the entry, or a fresh break of the opposite trendline. The far take profit is 5R.
- No entry in a range, within 30 minutes before or 15 after high impact USD news, or when
  the spread is above 15% of the stop. Exits still run during news and ranges.
- The strategy is judged by R, not by a win probability: the win probability and expected
  value gates do not apply to it (the other strategies keep them).

## Setting it up

1. Risk page: choose the profile **Trendline (indices)**: 0.5% per trade, up to 20 open
   trades (10 per symbol), 50 a day, 5% total open risk, 3% daily loss, 10% drawdown. Twenty
   open trades can lose 10% together: the total open risk (5%) is the real ceiling.
2. Strategies page: turn on `trendline_break`, and put NAS100 and US30 on the Market page.
3. Paper mode first. Instant entries need Auto mode, which needs the Go-Live gate and the
   typed word AUTO. In Semi-auto every signal waits for your approval and expires after 2
   bars, so it is too slow for this method.
4. Closing on a trend change goes through the execution engine: the whole remaining position
   is closed on its next cycle.

## What is not done

- A backtest on real M5 data with the commission.
- A cap on the sum of the stacked trades in one direction (the total open risk limit does it).
- The method was described on M1 charts; the app has M5 only.
