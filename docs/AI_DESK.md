# AI Desk (Phase 17): spec

Asked for on 8 October 2026: "an AI that analyses the open orders and the whole market,
suggests a trade when it sees one, reviews every finished trade to learn and get better;
opening, analysing, everything with AI. A complete new section, with a complete and
beautiful design."

Agreed approach: **the AI proposes, the engine decides.** Every AI trade idea becomes a
normal signal of its own strategy (`ai_analyst`) and goes through the same filters, the same
risk manager, the same Go-Live gate and the same execution engine as every other strategy.
The AI never sends an order itself, never widens a stop loss, and on a real account only
suggests until its strategy is approved on the Go-Live desk.

## 1. Principles

1. **Same pipeline.** AI ideas get no shortcut: decision trace, filters, sizing, limits,
   kill switch, audit log, all as today.
2. **Demo first.** Auto with AI ideas runs on demo and paper accounts. On a real account the
   strategy needs the Go-Live approval like any other (30+ paper trades, positive
   expectancy, calibration), and position changes are only suggested.
3. **Honest learning.** A language model does not learn from our trades. What gets better
   is (a) the playbook of lessons written after every trade and sent with every later
   request, (b) the probability model (Phase 11), retrained on the real results with the
   AI's confidence as a feature, and (c) our knowledge: the AI is measured like every other
   strategy (By strategy, calibration, expectancy).
4. **Measured, not trusted.** Every AI claim is checked: prices against the live quote, the
   stop against ATR, confidence against the results (calibration).
5. **Cheap and calm.** A daily cost cap, a call cap and a fixed cadence. Without a network or
   with the cap reached, the desk pauses and says so; trading of the other strategies is
   not affected.
6. **Private.** Prompts carry no login, holder name, chat id or key (the existing masking of
   `app.analytics.llm_client`). The key stays in the credential store.
7. **Any provider.** Base URL, API key and model are plain settings; nothing is tied to one
   company (section 8).

## 2. Modes (one control on the page)

| Mode | Market brief | Trade ideas | Open trades |
|---|---|---|---|
| Off | none | none | none |
| Advisor (default when switched on) | yes | shown as cards, never sent | notes only |
| Co-pilot | yes | become `ai_analyst` signals: Paper and Semi-auto ask for approval, Auto sends them on demo or paper only (and on real only after Go-Live approval) | notes; on demo optionally applied (tighten SL, partial, close), on real Apply button only |

Shadow mode (always on while the desk runs): every idea, also refused or skipped ones, is
followed on the price path until its SL, TP or expiry, so the AI gets a virtual R without
money. This fills the statistics much faster than real trades.

## 3. What is sent and what comes back

### 3.1 Context (built in `app/ai/context.py`, pure, tested)

Per watched symbol, compact JSON: the last closed bars (M15 60, H1 48, H4 30, D1 20; OHLC
rounded to the symbol's digits), ATR(14), EMA 20/50/200, ADX, RSI, the analysis card (trend,
bias, structure, levels, volatility), the session and the next high-impact news within 24
hours, the spread and the market hours. Then the account view in percent (open risk, daily
loss, drawdown, trades today), the open bot positions (symbol, side, entry, SL, TP, R now,
MFE and MAE in R, age, strategy), the pending orders, and up to 12 playbook lessons
(section 6). Size cap 12 000 characters; the oldest bars are cut first.

### 3.2 Answer (strict JSON, validated with pydantic in `app/ai/schema.py`)

```json
{
  "market": [{"symbol": "EURUSD", "bias": "bearish", "confidence": 62,
              "levels": [1.1188, 1.1212], "thesis": "two sentences at most"}],
  "ideas": [{"symbol": "EURUSD", "side": "sell", "order": "limit", "entry": 1.1205,
             "sl": 1.1229, "tp": 1.1160, "expires_minutes": 120, "confidence": 58,
             "invalidation": "H1 close above 1.1230", "reason": "at most 400 characters"}],
  "positions": [{"ticket": 123, "action": "hold", "new_sl": null,
                 "reason": "at most 200 characters"}]
}
```

`action` is one of `hold`, `tighten_sl`, `partial`, `close`. Text outside the JSON block is
kept as the free commentary. The answer language (English or Persian) is a setting; the
JSON keys stay English.

### 3.3 Validator (`app/ai/validate.py`)

An idea is refused (and logged with the reason, shadow-tracked anyway) when: the symbol is
not watched; the market is closed; the SL is not on the losing side or the TP not on the
winning side; R:R < 1.2; the stop is under 0.5 or over 4 ATR (M15); a market idea's entry is
more than 0.3 ATR from the live price; a limit or stop entry is more than 2 ATR away; it
expires in under 15 or over 480 minutes; more than 2 ideas came in one answer; the same
symbol and side already has an AI signal waiting or an open AI position. A position action
is refused when the ticket is not an open bot position, when `tighten_sl` would widen the
stop or put it inside the stops level, or when `partial` is under the minimum lot. Any
malformed answer is refused as a whole; nothing in it is ever executed as code.

## 4. The `ai_analyst` strategy

- `app/strategies/ai_analyst.py`, magic 26_070_006, off by default, in
  `registry.OFF_BY_DEFAULT` like the lab strategies.
- `evaluate` turns the latest validated ideas (not older than the bar) into `Signal`s; the
  trace gets an `ai` stage: model, prompt hash, tokens, cost, latency, confidence, reason,
  each validator check.
- Sessions default London and New York (a setting), news blackout and all filters as usual.
- Probability: the baseline of its own results; once 30 resolved, the Phase 11 model with
  the extra feature `ai_confidence`.
- Hard caps on top of the risk profile: 2 open AI trades, 1 per symbol, a daily AI loss of
  1% stops new AI ideas until midnight UTC.

## 5. Open-trade advisor

Runs at every desk cycle and on events: a position reaches +1R or -0.5R, high-impact news
within 30 minutes, the session ends. Notes are shown on the page and in the trade's events.
On demo with "Let the AI manage demo trades" on, `tighten_sl`, `partial` and `close` go
through `app.domain.management` like the strategy rules (never a wider stop, the same
retries and logs). On a real account each note has Apply and Ignore buttons.

## 6. Learning: reviews, playbook, evaluation

- **Review** after every closed `ai_analyst` trade and every resolved shadow idea (and,
  if switched on, every trade of the other strategies): one short call with the trade, its
  trace, the original reasoning and the price path (MFE, MAE, bars to exit). Answer:
  `verdict` (right, lucky, wrong, unlucky), `lesson` (at most 200 characters), `tags`.
- **Lessons** in SQLite `ai_lessons` (id, created, symbol, session, tags, lesson, trade id,
  result R, uses, wins, sum R, enabled). A lesson's evidence grows each time an idea that
  cited it is resolved.
- **Playbook**: up to 12 lessons per request, picked by match (symbol, session, tags) and
  evidence; lessons with 10+ uses and a negative average R are retired by themselves; the
  user can switch any lesson off. A weekly condense call merges near duplicates.
- **Evaluation**: `ai_analyst` in the By strategy section of every report; AI confidence
  versus real and shadow win rate (calibration); AI against the rule strategies on the same
  days; cost per trade and per R.

## 7. The page: AI Desk

New sidebar group **AI** with the page **AI Desk** (page id `ai_desk`), in the 0.26 look.

```
+----------------------------------------------------------------------------------+
| AI Desk   [Off | Advisor | Co-pilot]   (o) Thinking...   xAI grok-4.6           |
| Today $0.18 of $1.00 [=====-----]   Next run 12:45   [ Run now ]                 |
+-------------------------+----------------------------+---------------------------+
| MARKET BRIEF            | IDEAS                      | OPEN TRADES               |
| EURUSD  (62) bearish    | SELL limit EURUSD          | EURUSD sell  +0.4R        |
|  thesis, two lines      |  1.1205  SL 1.1229 TP 1.116|  AI: hold - below H1 EMA  |
|  [1.1188] [1.1212]      |  R:R 1.9  conf 58  1h 52m  |  [Apply] [Ignore]         |
| GBPUSD  (48) neutral    |  state: in pipeline > sent |                           |
| XAUUSD  (71) bullish    |  [Approve] [Skip] [Why?]   |                           |
+-------------------------+----------------------------+---------------------------+
| Journal | Playbook | Performance | Activity                                      |
| 10:41 EURUSD sell closed +1.5R  verdict: right  lesson: "London range breaks..." |
+----------------------------------------------------------------------------------+
```

- **Header**: the mode as a segmented control, a status chip (off, thinking with a soft
  pulse, ready, offline, budget reached), the provider and model, today's cost against the
  cap as a slim bar, the next run time and Run now.
- **Market brief**: one card per symbol with a drawn confidence ring, the bias pill (word
  and arrow, never color only), the thesis and the levels as chips; a click opens the full
  reasoning and the mini chart with the AI levels drawn on it.
- **Ideas**: newest first, each with side badge, prices, R:R, confidence, the expiry
  countdown and its live state through the pipeline (validated, filtered with the reason,
  waiting, sent, filled, closed with R); Approve and Skip where the mode asks; Why? shows
  the reasoning and every validator and filter check.
- **Open trades**: every bot position with R now and the AI's stance and reason, Apply and
  Ignore on real accounts.
- **Tabs**: Journal (reviews as a timeline with verdict pills), Playbook (lessons with uses,
  win rate, average R and an on/off switch), Performance (AI against the strategies,
  calibration chart, cost per trade), Activity (every call: time, tokens, cost, latency, the
  masked prompt and answer).
- Empty, offline and budget states say in one sentence what is going on and what fixes it.
- Every text through `i18n_fa` so the page also works in the Persian view.

## 8. Provider and settings (Settings > AI)

Any OpenAI-compatible service works: the user types the base URL, the API key and the model
name. A preset only fills the fields; every field stays editable.

| Preset | Base URL | Example model | Notes |
|---|---|---|---|
| xAI Grok (the user's key, 8 Oct 2026) | `https://api.x.ai/v1` | `grok-4.6` | 2 / 6 USD per 1M tokens in / out; `grok-4.7` may answer only on the Responses API |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | |
| OpenRouter | `https://openrouter.ai/api/v1` | any listed model | one key for many companies |
| Local (Ollama, LM Studio) | `http://localhost:11434/v1` | any pulled model | no key, no cost, no internet |
| Custom | any https address (or localhost) | any | |

- **API style**: Auto (default), Chat Completions or Responses. Auto sends
  `/chat/completions` and moves to `/responses` when the service says the model needs it,
  then remembers that for the model.
- **Test connection**: one tiny request, and `/models` where the service has it to offer a
  model list; shows the latency, the model that answered and the cost of one desk cycle.
- **Reasoning effort** (low, medium, high) for models that have it; default low, to keep the
  calls fast and cheap.
- **Prices** per 1M tokens (input, output) for the cost cap, filled by the preset.
- One provider for the AI Lab and the AI Desk; the key in the credential store with the
  same masking.
- Desk settings: mode, cadence (default 30 minutes, minimum 15, plus the session opens and
  the events), daily cost cap (default 1.00 USD) and call cap (default 60), symbols,
  sessions, answer language, "Let the AI manage demo trades", "Review the other strategies'
  trades too".

Cost estimate: one cycle is about 5 000 input and 800 output tokens, about 0.015 USD on
`grok-4.6`, so 60 cycles a day stay under the 1.00 USD cap.

## 9. Storage

SQLite tables `ai_calls`, `ai_briefs`, `ai_ideas`, `ai_position_notes`, `ai_lessons`, each
with the account id, in the outbox for the optional cloud sync like the other tables.

## 10. Phases

| Phase | Content | Release |
|---|---|---|
| 17a | provider presets, API style Auto (Chat Completions or Responses), Test connection, context builder, answer schema, validator, scheduler with cadence, caps and backoff, storage, shadow tracking; all with fake transports | 0.27.0 |
| 17b | `ai_analyst` strategy, the `ai` trace stage, hard caps, Go-Live entry, By strategy | 0.28.0 |
| 17c | open-trade advisor through the management layer, Apply and Ignore | 0.29.0 |
| 17d | reviews, lessons, playbook, calibration, weekly condense | 0.30.0 |
| 17e | the AI Desk page and Settings > AI | 0.31.0 |
| 17f | polish, Persian texts, USER_GUIDE section, PC test checklist | 0.31.x |

## 11. Tests (each phase)

Golden answers through a fake transport, in both API styles; every validator rule with a
passing and a failing case; a stop is never widened; real accounts never get an automatic
change; Auto sends AI ideas only on demo or paper without an approval; caps and backoff;
offline and budget states; prompt injection strings in news titles and in the model's
answer stay data; no login, name, chat id or key in any prompt; the page builds empty,
offline and full.

## 12. Honest limits

An AI idea is not proven to be profitable, and a language model cannot be backtested
without look-ahead, so its value is only known from demo and shadow results over weeks.
Answers vary between calls and models. The playbook helps the prompts; it is not training.
The network to the AI provider may need a VPN, like Telegram.
