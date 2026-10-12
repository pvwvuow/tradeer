# Telegram channels: the smallest lot and the one-bet check (0.50.1)

Part of the Telegram signals work in `docs/TELEGRAM_SIGNALS.md` (phase 22).

12 October 2026: Araz Forex sent `XAUUSD buy 4175 SL 4170 TP 4180/4185/4190`. The card came
up, then it was refused twice:

1. **Budget too small.** The channel has 100 USD and risks 2% = 2 USD a trade. On gold with a
   5 dollar stop, the broker's smallest lot (0.01) risks 5.25 USD (with commission), so the
   lot came out 0.
2. **One bet per currency and direction.** A bot's EURUSD buy stop from 07:00 was also a bet
   against the dollar, so the gold buy was filtered out.

## What changed

- **Floor.** `ChannelPolicy.floor_percent` (8% of the channel's equity by default, 0 turns it
  off) becomes `Budget.floor`, carried on the signal as `budget_floor`. When the normal share
  gives lot 0, the risk manager uses the smallest lot if it risks no more than the floor and
  no more than the account's own risk per trade (both times the leg's share of the signal).
  Every other account limit (daily loss, open risk, exposure, margin) still applies after it.
  The decision trace says: "the smallest lot ... risks ..., more than the channel's normal
  share, but within its floor of ... and the account's own limit".
- **One-bet check.** A channel's signal (strategy `channel:...`) is not held back by it: it
  waits for the owner's own hold anyway. The channel's trades still count against the bots, and
  pasted signals keep the check.

## Tests

`tests/unit/test_channel_floor.py`.

## Not done

- Taking a good channel signal without the hold (an "Auto" mode for a channel) needs its own
  decision: it moves real money without a person, so it is not part of this change.
- A card that is refused after it was shown still says "waits for your hold" until the next
  update; the refusal reason is on the card from the desk's answer.
