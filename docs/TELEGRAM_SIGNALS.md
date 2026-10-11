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

## Next

- **22b, the bot**: neater messages (HTML), a notice for every channel signal; an approval
  card gets Approve and Skip buttons in the bot (inline keyboard, `callback_query`), an info
  only message when no approval is needed.
- **22e, the rest**: the desk note's tokens, and the readings per channel on the channels
  page.
- **22f, quicker detection**: fewer waits for drafts, more formats read without the AI.
- **22g, budgets in the main pages**: the money given to the channels, their open risk and
  their profit on the Dashboard, Risk and Analytics pages.
