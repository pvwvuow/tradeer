# Telegram standby: the AI stays ready for the next messages (phase 22j, 0.50.0)

Part of the Telegram signals work in `docs/TELEGRAM_SIGNALS.md` (phase 22).

The owner: the AI only understood a signal that arrived whole. An admin often talks first
("get ready", "dollar", "I want to post a signal") and gives the trade in the next messages;
the AI should read those, stay ready for the next messages, react fast, and think quickly
while it reads a channel.

Where it lives: `app/channels/standby.py` is pure (the standby book, the glance limits, the
prompts and their parsing); `app/ui/standby_feed.py` is the `ChannelFeed` with it
(`StandbyFeed`, built by `Insights.attach_feed`). The plain `ChannelFeed` is unchanged.

## What happens

- **Glance.** A short message (30 words at most, no link, not a result claim) that the parser
  calls noise goes to the AI in a tiny call: "does this say a signal is coming?" (80 tokens,
  and a low reasoning effort when the model has one set). Yes: the channel goes on standby.
  At most 12 glances an hour per channel and 80 a day. Without an AI connection nothing is
  asked and nothing is told.
- **Standby.** A teaser ("get ready") or a half signal ("EURUSD buy") opens it without any AI
  call; a glance, or an AI reading that says "part" or "teaser", opens it too. It lasts 10
  minutes after the last hint, 30 at most, 12 readings at most. The Telegram signals tab says
  when it starts ("standby (USD): ...") and when it ends ("standby over: no signal came").
- **Quick readings.** While a channel is on standby every new message of it goes to the AI at
  once, with numbers or not, with the channel's last messages, and the AI is told that the
  message may be a piece of the signal and to put the pieces together. The answer is short
  (220 tokens) and the model thinks little. A draft that waits for its rest is read the same
  way.
- **Safety.** The answer is checked like every reading (`canonical`: a known symbol, the right
  sides, only numbers the channel wrote) and ends as a card that needs your hold, or is only
  counted in a Paper trial. A signal that came ends the standby. A message is never an
  instruction to the AI or the app.
- The AI readings of a day went from 100 to 200; glances have their own 80.

## Tests

`tests/unit/test_standby.py` (the book, the limits, the prompts) and
`tests/ui/test_standby_feed.py` (a hint, the standby, the pieces becoming one card; a teaser
opening it; no AI).

## Not done

- A standby indicator in the tab's header (only the start and end lines for now).
- The glance and the quick reading use the AI connection of the AI Lab (one model); a
  cheaper second model for glances would need its own setting.
