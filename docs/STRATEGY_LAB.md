# Strategy lab

Asked for on 7 October 2026: on the DEMO account, run many strategies at once and sort the
trades by strategy, to learn each one's weak and strong spots before any of them goes near a
real account.

## The strategies

| Strategy | Idea | Entry | Stop and target | Sessions | Magic |
|---|---|---|---|---|---|
| trend_pullback | H1 trend, M15 pullback to the EMA20 zone, RSI back over 50 | market | swing or 1.5 ATR, 2R | London, New York | 26070001 |
| london_breakout | Asia range, stop orders at the London open (OCO) | stop | other side of the range, 1.5R | London | 26070002 |
| range_reversion | no H1 trend (ADX < 22): a M15 close back inside the 20-bar band after a stretch | market | last 3 bars + 0.2 ATR (1 to 2.5 ATR), the band's middle (at least 1R) | Asia, London | 26070003 |
| channel_breakout | H1 trend, a fresh M15 close beyond the 40-bar high or low (channel 2 to 10 ATR) | market | 2 ATR, 2R | London, New York | 26070004 |
| ema_momentum | M15 EMA9/EMA21 cross on the side of the EMA100, ADX > 25, RSI 50 to 70 | market | 1.5 ATR, 1.5R | London, New York | 26070005 |

None of them is proven to be profitable: that is what the lab is for. Each has its own magic
number, so every trade in MT5 and in the journal belongs to exactly one strategy. The two spec
examples are on by default; the three lab strategies start off (on a real account each one
that is on would need its own Go-Live approval). The Strategies page turns each one on or off
and edits its rules.

## How to run it

1. Strategies page: tick On for range_reversion, channel_breakout and ema_momentum, then
   Save.
2. Use a DEMO account. Risk > profile **Strategy lab (demo)** > Use this profile > Save:
   0.25% per trade, up to 12 open trades (5 per symbol, 3 per strategy), 40 trades a day,
   5% daily loss and 20% drawdown stops.
3. Trading mode Auto (on a demo account it needs no Go-Live approval).
4. Leave it on through the London and New York sessions on as many weekdays as you can.

## Reading the results

- Every daily and weekly report ends with **By strategy**: trades, win rate, net, profit
  factor and expectancy in R, best expectancy first, then its strong (+) and weak (-) spots:
  the best and worst symbol, session and side; how often it ends at the stop loss; losers that
  were 1R or more in profit first (move the stop to break-even sooner); winners that first went
  deep against the trade (early entries).
- Analytics > Compare > By strategy shows the same strategies side by side for any period,
  and Breakdowns > Strategy splits them further.
- A strategy needs 30 trades before its numbers mean much: the reports say "too few to judge"
  until then.
