# Telegram signals: their own place, the bot, the logs (phase 22, 0.47.0 on)

The owner's request of 11 Oct: a channel signal came as a card in whichever AI Lab chat was
open, and a new chat took it away. Telegram signals need their own place that cannot be
missed, the bot should tell about them (and take the approval when one is needed), the AI's
work on channel messages should be logged and its tokens shown, detection should be quicker,
and the main pages should know the money given to the channels.

## 22a. The Telegram signals tab (0.47.0)

- The AI Lab has two tabs over its chat column: **Chat** and **Telegram signals**
  (`app/ui/signal_inbox.py`, `LabTabs`, mounted by `Insights.attach_inbox`).
- Every channel order card, every follow-up card and every line of the feed (a half signal
  waits, a Paper trial read a signal, the AI read a message or a picture) goes to the
  Telegram signals tab, newest first. A new chat does not take them away; the 200 newest
  stay for the session (the oldest finished ones go first).
- The tab and the sidebar's **AI Lab** show how many cards wait for your hold.
- Filters: All, Waiting for you, Notes; Clear finished removes the notes and the cards that
  are over (sent, skipped, expired, refused).
- **Review in chat** under a card asks the AI about that signal in the chat, only when you
  press it. Edit on a card puts its text in the chat's box and opens the chat tab.
- A signal you paste in the chat, and a channel setting you ask for in the chat, stay in the
  chat. Nothing is sent without the card's own hold, as before.

## 22c. Arrows and pop-ups (0.47.0)

- The arrows of drop-downs and spin boxes are chevron pictures in the theme's colours
  (`app/ui/arrows.py`, drawn in pure Python, written once to the temporary folder). The CSS
  triangles stay only as a fallback: some Windows set-ups paint them as black boxes.
- Pop-up lists and menus (drop-downs, table column menus) are square: a rounded border on a
  pop-up window leaves black corners on Windows. List rows get 26 px and a hover colour.

## The chat as chat apps draw it (0.47.0)

The owner: many standards of a chat page were missing or not like the others (Copy was a
word, not an icon). Now (`app/ui/chat_controls.py`):

- Under an answer, a row of icon buttons with tooltips: Copy (a check mark for a moment),
  Regenerate, Good answer, Bad answer; then the quiet cost line. Under your question, a copy
  icon on hover that keeps its room, so nothing jumps.
- The send button is round with an arrow; while the AI works it is a stop square, and Esc
  stops too. No separate Stop link.
- The jump to the latest message is a round arrow; the quick prompts slide with chevrons
  that mirror right to left. Code and tables keep Copy with its icon.
- No Edit button (the owner found it pointless); the up arrow in an empty box still brings
  back the last question. No file clip (attaching never worked) and no fixed PAPER ONLY tag
  in the message box (it was wrong in Semi-auto and Auto; the header shows the real mode);
  a short hint says how to send and how to start a new line. Persian words everywhere.

## 22d and 22e. Logs and tokens (0.47.0)

- Every chat turn is one log line (category llm): the model, the time, the tools, the
  tokens, the cost, the question and the start of the answer; a failed turn and every thumb
  (good or bad) are logged too, so a bad answer can be found later.
- Every AI reading of a channel message is logged (category notify): the message, a
  picture or not, the time, the model, the tokens, the cost and the AI's answer as it came;
  then, as before, what the checks made of it.
- Each reading's tokens and cost go to the AI Lab's usage ledger (kind `channel`, so the
  Usage panel counts them) and to today's line on the Telegram signals tab ("AI: 3
  reading(s) · 2.1k tokens · $0.0040").

## 22b. The Telegram bot (0.47.0)

- Every message is HTML now: a bold headline (BUY LIMIT XAUUSD), the channel, one fixed-width
  line per price (Entry, SL, TP1 with its R), the lots and the money at risk, and one line
  for what happens next. Text from outside is escaped. Notices (trade opened, limit hit...)
  have an icon and a bold title too.
- Each channel signal is its own message. A card that waits for you ("Waiting for your
  approval, expires 11:40") carries **Approve** and **Skip** buttons. Everything else only
  tells: an order that went out by itself (Auto), a signal the checks refused (with the
  reason), a Paper trial, a duplicate. Cards are told once, when the desk first answers.
- A button works like a command. The chat must be on the list and unlocked with `/pin`;
  a locked chat gets a pop-up and nothing happens. The press is answered on the app's UI
  thread (8 s wait), does what the hold and Skip do (the engine still checks the price, the
  spread and the limits again) and is audit-logged. On a real account Approve asks once more
  ("Yes, send the REAL order" or Cancel). A card that is no longer waiting says so.
- These messages ignore quiet hours and the per-kind settings of Notifications: they are
  tied to a decision. The bot's text is English.
- Code: `app/notify/bot_text.py` (pure), `app/notify/telegram.py`, `app/ui/bot_cards.py`.

## 22g. The channels' money on the main pages (0.48.0)

- **Dashboard**: a side block "Telegram channels" under the risk limits. One line per channel
  that is on and has a budget: what it is worth now (its budget plus what its closed trades
  made) over its budget, and what it made today; "daily stop" or "drawdown stop" when one of
  its limits was reached; a total first when there are several. The tooltip has the mode,
  the closed result and the risk of one trade. With no budget given the block is hidden. It
  links to the AI Lab.
- **Risk page**: a table "Telegram channel budgets" under the limit usage with the channel,
  its mode (Live or Paper trial), budget, worth now, today, risk per trade and stop. The
  account limits above still apply on top of every channel.
- Read every 30 seconds from the channel store and the closed trades of each channel's
  magic number (`app/channels/overview.py`, pure; `app/ui/channel_budgets.py`). The blocks are
  added by `Insights.attach_budgets`, so the two pages themselves are untouched.

## 22h. The AI reads what the parser does not (0.48.1)

The owner wrote only "EURUSD" in a test message and gave the trade in the next messages:
nothing understood. Now:

- A short message that only names the pair (four words at most, not an update, not a result
  claim) opens a draft, so the side and the prices that follow join it like any other piece.
- Two prices where one has its label ("SL 4180 TP 4205"), or a name and two prices, go to the
  AI with the channel's last five messages, even when the parser cannot make a signal of
  them. A draft of three messages that is still incomplete goes to the AI too.
- The AI is told to put the earlier messages and this one together. Its answer still has to
  pass every check (known symbol, right sides, only numbers the channel wrote) and still ends
  as a card that needs your hold.
- A message that should go to the AI but cannot (no AI connection, the daily limit used) is
  told in the chat and the log with the reason, once in ten minutes. The limit is 100 AI
  readings a day (it was 40). A draft that only holds a symbol is not read when it expires.

## Next

- **22e, the rest**: the desk note's tokens, and the readings per channel on the channels
  page.
- **22f, quicker detection**: a message is handled the moment the reader gets it (no
  polling); a half signal waits only for the rest of its own pieces. Every card's
  `latency` (message to card) is saved in `tg_signals`; the next step needs a real log with
  slow signals to find what to cut.
