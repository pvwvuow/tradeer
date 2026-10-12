# AI Autopilot: prompt review, bug list and plan (phase 23)

Written 12 Oct 2026 after 0.50.1. The owner wants the AI smarter and more automatic: he
only wants to see trades. This file holds three things side by side: what is wrong with the
AI prompts today, every bug found in the AI Lab and the signal pipeline (receive, read,
size, send, manage), and the build order. Nothing here is built yet.

Safety line that does not move: the AI never sends an order by itself. Auto-take (23f) is
off by default, per channel, and the owner turns it on himself.

## 1. Prompt review (app/channels/reading.py, app/channels/standby.py)

Good today: the messages are data, never instructions; one JSON object with a schema; no
invented numbers (`canonical` checks every number against the texts); sides checked.

| # | Problem | Effect | Fix |
|---|---------|--------|-----|
| P1 | Earlier messages have no time | a 3 hour old "gold" is stitched into a new trade | write the age next to each one ("3 min ago"), drop pieces older than the standby window |
| P2 | No live price in the prompt | shorthand prices ("buy 75-73" = 4175-4173) are refused; market vs limit is a guess | send bid/ask of the named symbol; allow a shorthand expansion when its last digits are written and it is within 2% of the price |
| P3 | No Persian vocabulary or examples | weaker reading of Persian channels | list حد ضرر، تارگت، پله، سل، بای، ورود، کنسل، ریسک فری and give 3 or 4 short examples |
| P4 | Only gold is named | "dollar", "euro", "pound", "yen", "oil", "bitcoin" are unknown | send the channel's aliases and a default instrument table |
| P5 | Earlier messages are not marked | a signal already taken can be read again as new | mark them "[already a signal]" |
| P6 | `update` has no structure | the AI says update and nothing happens with it | kinds tp_hit, sl_hit, close, close_part, sl_to_entry, new_sl, new_tp, cancel, with the price |
| P7 | No confidence and no "missing" list | nothing to gate a quality check or Auto-take on | add `confidence` 0..1 and `missing` (sl, tp, entry, side) |
| P8 | One trade per message | a message with gold and EURUSD loses one | `trades` array, up to 3 |

## 2. Bugs found (code read on main d0fba82)

Severity: C critical, H high, M medium, L low. "Verified" = re-read by hand after the audit.

### 2.1 Receive and read (app/channels, app/ui/channel_feed.py)

- **B1 (H, verified) Shadow follow re-parses only the first message.** `shadow_follow._follow_one` parses `repository.message(...).text`; signals the AI read, joined from pieces, read from a picture or fixed by an edit are not readable there and end as "expired". The Paper trial and the channel ranking undercount exactly the signals the AI helped with. Fix: save the canonical signal text in tg_signals and follow that.
- **B2 (H, verified) AI numbers may come from unrelated earlier messages.** `canonical` accepts any number found in any of the last 5 texts; with no time limit a stale price passes the "written" check. Fix: P1 plus check numbers only against the messages inside the window.
- **B3 (H, verified) AI "market" with an entry loses the market order.** `canonical` writes `limit`/`stop`, and `now` only when there is no entry; `order="market"` with an entry is re-parsed as a plain one-price signal and may become a pending order. Fix: write `now` for market.
- **B4 (M) Follow-ups ignore the channel's settings.** `handle()` runs `follow_up()` before `route()`; `follow_updates`, `break_even` and mode Off are not checked, so an Off channel or "never" can still make follow-up cards (they still need a hold). Fix: check policy first.
- **B5 (M, verified) "new TP" updates do nothing.** `read_update` makes `NEW_TP`, but no card branch uses it; the card says there is nothing to do. Fix: modify the TP of the matching leg.
- **B6 (M) Stop can leave the old reader running.** `ChannelReader.stop()` drops the thread handle before it has ended; a quick Start makes a second reader and messages are handled twice. Fix: join, and refuse Start while the old one lives.
- **B7 (M) Channel stats undercount skipped and refused.** `skipped = cards - taken` ignores Paper and skip actions. Fix: count by action.
- **B8 (M) Shutdown races the database.** Shadow workers are daemon threads; `Database.close()` can close under them. Fix: stop event + join before close.
- **B9 (M) No catch-up after a restart or a VPN drop.** Only live NewMessage events; a signal posted while the app was closed or Telegram was cut is never seen. Fix: on connect read the last N messages since the last seen id (marked late, market cards refused if older than market_minutes).
- **B10 (L) Albums are separate messages.** A picture album makes several AI readings. Fix: group by grouped_id.

### 2.2 AI Lab (app/ui, app/ai)

- **B11 (H, verified by the audit, known) A refused card still waits for the hold.** `OrderCard.show_result` keeps the old legs when the new result has none, so the state stays "waiting" and Hold stays on. Fix: a result is authoritative; show the refusal reason and a fix button.
- **B12 (H) The daily AI cost cap is shown but not enforced.** Chat, Ask AI and channel readings never ask `Budget`. Fix: one shared gate before every provider call.
- **B13 (H) The per-question cost cap can be passed in a tool loop.** `run_turn` checks the cap only before the next call. Fix: check after each call too.
- **B14 (M) An expired market card can still be held.** `LabDesk.send` does not check `expires_at`; the pipeline approves before it expires. The engine re-checks before sending, so no stale order goes out, but the card lies. Fix: expire first, check in `send`.
- **B15 (H) A card with one failed leg and one open leg shows "failed".** `card_state` returns failed before filled, and Clear finished can remove a card whose trade is still open. Fix: a "partly" state; any open leg = not finished.
- **B16 (M) Two identical signals in the same millisecond share a request id.** `stable_id(..., f"{now:.3f}")`; the second card overwrites the first in `LabDesk.cards`. Fix: add a nonce.
- **B17 (M) Saved history keeps the first text only.** Rows are saved before prices and reasons exist; only the state is updated later. Fix: update the row text when the result settles.
- **B18 (M) No retry on network, 429 or 5xx.** Transport retries only parameter fallbacks. With the user's VPN this fails readings often. Fix: 2 retries with backoff, honour Retry-After, count the cost.

### 2.3 Size, send and manage (app/engine, app/risk, app/brokers)

- **B19 (H) Pending orders are not in the account risk picture.** `read_picture` reads positions only; with max open 1, two confirmed pendings both pass and both can fill. Same for total open risk and currency exposure. Fix: count the bot's pending orders (worst-case risk).
- **B20 (H) An uncertain market send can open twice.** After a timeout `send_open` looks once for the position, then sends again. Fix: poll positions and orders for a few seconds (by magic + comment) before any retry.
- **B21 (H) An uncertain partial close can close twice.** `send_simple` retries close/modify/cancel without re-reading the position. Fix: re-read the volume and retry only what is left.
- **B22 (M) Channel daily stop uses UTC midnight, not the broker day.** `channel_feed.day_start` vs `BrokerClock` for the account. Fix: pass the broker day start.
- **B23 (M) Risk is sized before prices are rounded to the tick.** A long SL rounded up can make the real risk larger than the sized one. Fix: round the SL away from the entry, then size.
- **B24 (M) The fresh market price at send is not re-sized.** `send_open` moves the price after the risk check. Fix: re-check loss per lot with the final price, reduce the volume if needed.
- **B25 (M, unconfirmed) First-start daily baseline ignores floating profit at rollover.** Fix: save the rollover equity.
- **B26 (M) Channel signals skip the one-bet filter (0.50.1) with no other cap.** Gold buy + EURUSD buy + GBPUSD buy from three channels is three bets against USD. Fix: a USD exposure cap across channels (e.g. 2 legs or 1.5x the per-trade risk).

Checked and not a bug: `management._allowed_sl` tests the freeze level on the current SL. That is how MT5 freeze level works (the existing level may not be inside it); the new SL is checked against the stops level.

## 3. Plan (build order)

| Phase | What | Needs |
|-------|------|-------|
| 23a | Safety fixes: B19, B20, B21, B15, B11 (refusal visible with a fix button), B14, B16 | nothing |
| 23b | Prompt v2: P1 to P8, B2, B3, shorthand prices with live price | nothing |
| 23c | Reading fixes: B1 (save canonical text), B4, B5, B9 catch-up, B12, B13, B18 | nothing |
| 23d | Channel grade A to D (shadow R with sample size, drawdown, deletes and edits after the result, latency, slippage) and a reading scorecard (card edits and misreads) | nothing |
| 23e | Auto-tune: the owner sets a ceiling per channel; the AI sets budget, risk, floor and max open by the grade each week; every change logged with Undo; USD exposure cap (B26) | nothing |
| 23f | Auto-take (opt-in per channel, off by default): grade A or B, 20+ resolved shadow signals, average R > 0, AI confidence >= 0.8 and numbers checked, risk under the cap, no daily stop, no high-impact news within 15 min, normal spread; 10 s countdown with Cancel in the app and in the bot; 3 a day at most; back to hold after 3 losses in a row | owner's OK |
| 23g | Quality check before a card: price already past the entry, R:R after slippage, spread, news; suggest a limit at a better price | nothing |
| 23h | Channel memory: corrected readings saved as examples for that channel and sent with its readings | nothing |
| 23i | Post-trade review, daily report and pre-London brief in the bot; log doctor (VPN, MT5 API setting, disk) | nothing |
| rest | B6 to B8, B10, B17, B22 to B25 | nothing |
