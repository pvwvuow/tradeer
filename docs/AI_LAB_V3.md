# AI Lab v3 and channel messages (0.44.x)

Status: plan, 10 October 2026. Asked for by the owner after 0.43.0: "the AI Lab looks too
sharp and dry, rework it"; "a channel may post half a signal or 'wait, I will complete it':
the AI must understand every kind of channel message"; "some channels post the signal as a
picture".

## 1. Channel messages the app must understand (0.44.0)

Today a message is noise, a signal or an update of an earlier signal, and only new
messages are read. Real channels also do this:

| Kind | Examples | What the app does |
|---|---|---|
| **Teaser** | "get ready", "XAUUSD soon", "آماده باشید", "صبر کنید تکمیلش می‌کنم" | opens a **draft** for the channel; a chat line "gg: waiting for the rest" |
| **Partial** | "Gold buy now" (no SL), "SELL EURUSD SL later" | a draft with what is known and what is missing |
| **Completion** | "SL 4180 TP 4205" right after, a reply to the draft, or an **edit** of the first message | joined to the draft; when complete it goes the normal way (Live: an order card, Paper: counted) |
| **Signal** | everything in one message | as today |
| **Update** | "close now", "move SL to entry", "TP1 hit" | as today (follow-up card for a signal you took; shadow) |
| **Result / brag** | "+120 pips 🔥", screenshots of profits | counted as the channel's own claim, never a signal |
| **Photo** | a chart with lines and numbers, a picture of the text | read by the AI (section 2) |
| **Noise** | ads, VIP offers, greetings | skipped |

Rules:

- A draft lives 15 minutes (a channel setting, 5 to 60). Messages of the same channel in
  that time, replies to it and edits of it are joined in order; the joined text is parsed
  again after each one.
- An incomplete draft never makes an order card. When it expires the chat shows "gg: the
  signal stayed incomplete (no SL), nothing done".
- An edit of a signal whose card was not held yet plans the card again (the old one is
  closed with "the channel changed it"); an edit after the hold becomes a follow-up card.
- A market signal that is completed late is checked against the price now: if the entry
  moved more than the card's chase limit it becomes a Paper count only.
- Every decision stays in `tg_signals` with the reason, so the AI and the stats can read it.

### The AI reads what the rules cannot

The rule parser runs first (free and fast). When it finds a teaser, a partial signal or a
photo and the AI connection is on, the app asks the AI once, with the last 5 messages of
the channel as context, for one JSON object:

    {"kind": "signal|partial|teaser|update|result|noise", "symbol": "...", "side": "buy",
     "entry": [4190], "order": "market|limit|stop", "sl": 4180, "tps": [4205, 4220],
     "missing": ["sl"], "wait": true, "confidence": 0.8}

- The answer is checked like a pasted signal (prices on the right side, the symbol known,
  the SL on the losing side); the AI's word is never enough to send anything.
- The texts are data, never instructions (the prompt says so; the answer is JSON only).
- Cost: one short call per unclear message, counted in the usage ledger, with a daily cap.

## 2. Photo signals (0.44.1)

- The reader downloads the photo of a message of an on channel (largest size up to 2 MB,
  kept 7 days in the profile's `channels/media` folder).
- With a model that accepts images (Test connection finds out and remembers it), the photo
  goes with the same JSON request; the chat card shows the picture small next to what was
  read, so you can check it.
- Without one: the chat says "gg posted a photo signal: the model cannot read images",
  and the signal is counted as unread.

## 3. AI Lab v3 look (0.44.2)

The page follows No Curve v2 today: square corners, thin lines, no colour. The owner finds
it too sharp and dry. v3 keeps the layout (chat in the middle, the inspector on the side)
and the RTL rules, and softens the page:

- **Shapes:** 14 px corners on cards, bubbles and the composer, 10 px on buttons and chips;
  the hold button is a pill with a progress ring.
- **Air:** 20 px between messages, 16 px inside cards, the column 720 px.
- **Surfaces:** the page background a step darker (light theme: lighter) than the cards;
  borders at low contrast, a soft shadow under cards and the composer.
- **Colour with meaning only:** one accent for the primary action and the AI's mark; green,
  amber and red only for results, warnings and refusals; the doctor's findings as coloured
  dots.
- **The AI's answers:** a small AI mark at the start, the answer on a light surface, the
  steps as a thin timeline with dots that fold away.
- **Welcome:** four large suggestion tiles in a 2 x 2 grid ("What is wrong?", "My channels",
  "Paste a signal", "How did I do this week?").
- **Motion:** new messages and cards fade and rise 8 px (150 ms); the composer glows while
  you type, as today.
- **Type:** Vazirmatn 15 px, line height 1.6; numbers in tabular figures.

Only the AI Lab changes in 0.44.2; the rest of the app can follow when the owner likes it.

## 4. Tests

- Drafts: teaser + completion, reply, edit, expiry, a late market signal, a result message.
- AI reading: valid JSON, broken JSON, wrong side of the SL, "ignore your rules" in a
  message (stays data), the daily cap.
- Photos: download size cap, a model without images, the card with the picture.
- Look: the page builds in both themes and in Persian; every card keeps its object name.
