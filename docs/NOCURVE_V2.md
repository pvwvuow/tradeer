# No Curve v2: the plan to make the app look exactly like the design (Phase 20)

Source: the owner's design file **"No Curve v2 — MT5 Workstation"** (15 boards: Simple view,
Dashboard, 02 Market … 14 Settings). It replaces the earlier "UI v2" artifact (Phase 19): the
work on the old design stops here (the dashboard of 0.32.0 did not match, and the AI Lab branch
`feat/ui-v2-ailab` was built for the old layout; its `chat_store.py` and the agent wiring are
reused, its 3-column layout is not).

Rules for every phase:

1. **Exactly like the design.** Sizes, gaps, borders, radii, fonts and colours are copied from
   the design's CSS (the per-page specs quote them), not estimated.
2. **Persian, right to left, real data.** Every number comes from a real snapshot or repository.
   The sample values in the design (10,214.60, EUR/USD 1.0854, `ma_cross`, …) are never shown;
   an unknown value shows "—" or a clear empty state, never a fake "OK".
3. **Nothing is lost.** Every feature the app has and the design does not show gets a place in
   the design's own visual language (listed per page below and in the specs).
4. **Design parts without a backend** get the backend piece they need (listed as
   "NOT IN BACKEND" in the specs, with the module and data shape to add).
5. **Safety stays.** The AI only reads; a change is applied only through check, backtest and
   activation in Paper; no real order from the AI Lab; the account number never goes to the AI;
   the kill switch is always one step away.
6. One phase = one PR to `main` (ci + build + tests green), then the release-please PR and a
   GitHub Release with 8 verified assets.

Detailed specs (layout tree with exact numbers, every text, states, data mapping, missing
features, checklist and tests) are kept next to the design file in `nocurve-v2-plan.zip`
(about 180 KB, so not in the repo):

| Spec | Pages |
| --- | --- |
| `00_frame_simple_dashboard_market.md` | tokens, fonts, shared classes, frame, Simple, Dashboard, Market |
| `03_07_trade_analyze.md` | Signals, Positions & trades, Stats, Journal, Backtest |
| `08_14_system.md` | Model, Strategies, Risk, Logs, Health, Settings |
| `09_ai_lab.md` | AI Lab (the most important page) |

## The design system (from the design's CSS)

- **Light is the default** (`.fa`): bg `#edf0f4`, surface `#ffffff`, border `#d5dbe5`, strong
  border `#aeb9ca`, text `#0f1b2d`, secondary `#52607a`, hover `#e3e8ef`, accent (gold)
  `#c8a15a`, profit `#1b7f53`, loss `#b93636`, warning `#9a6310`, soft fills at 12 %.
- **Dark** (`.fa.lt`): bg `#0a0e13`, surface `#121921`, border `#243040`, strong `#37485f`, text
  `#e8edf4`, secondary `#9aa8bb`, hover `#1a2430`, accent `#d9b26a`, profit `#4cc38a`, loss
  `#ff7d7d`, warning `#e3b04b`, soft fills 14–15 %.
- Fonts: **Vazirmatn** for text, **IBM Plex Mono** for numbers, captions (`.cap` 11 px,
  letter spacing 0.8 px, upper case), tags and code. Numbers stay left to right inside RTL.
- Pieces: `.tag` (3 px radius, 11 px mono; inverted, profit, loss, warning), `.btn` (44 px
  high, 6 px radius), `.seg` (32 px, chosen one inverted), `.ib` (36 px icon button), `.nv`
  (sidebar row 38 px, chosen one inverted), `.rw` (table row 52 px, header 34 px), `.sec`
  (section title 14 px/600 with a hairline), `.led`, `.hatch`, cards `.tc` (8 px radius).
- Motion: fade 0.5 s, fill, draw 1.6 s, pop, ticker 48 s loop, ring, toast, blink; all off with
  reduced motion.

## Phases

### 20a. Tokens, fonts and the shared pieces
Replace the ink-and-cream tokens with the two No Curve palettes (light default), pixel font
sizes, the mono caption style, and Qt versions of tag, button, segment, icon button, nav row,
table row, section title, LED and hatch. Tests: every token value, default theme light, both
themes render, contrast.

Done in 0.33.0 (`app/ui/theme.py`, `app/core/ui_prefs.py`): both palettes with the design's
exact values (soft fills are its rgba laid over the surface), the ink as the accent (the gold
is never drawn), light by default and a one-time move to light for preferences saved before
0.33 (`look` 2), design pixel sizes as points (`px`), outlined 3 px tags with only the PAPER
style inverted, and the control heights of the design as constants. Contrast: words 4.5:1;
the design's profit (4.37:1 on the page) and warning (4.41:1) are kept exactly and held to
4.2:1; the hairline control border (#AEB9CA) is not held to 3:1, the ink focus ring is. The
geometry of the shared pieces (44 px buttons, 32 px segments, 38 px rows, 52 px table rows)
is applied with the frame in 20b and the pages after it, so pages not yet rebuilt keep a
layout that fits.

### 20b. The frame
Header 52 px (logo, "MT5 Workstation", ADVANCED VIEW, connection tag, mode tag, Simple /
Advanced, theme button), ticker 30 px (real quotes, 48 s loop, never SAMPLE values), sidebar
232 px with TRADE / ANALYZE / SYSTEM groups and 38 px rows 01–14, page title row (cap
"TRADE / 01", 26 px title, the trading-mode segment PAPER / SEMI-AUTO / AUTO (locked until
Go-Live) / ANALYSIS on the far side). Kept from the app: search (Ctrl K), language, settings,
kill switch (Ctrl Shift K), notifications badge, status footer (optional, outside the design's
canvas).

### 20c. Dashboard
Four KPI cells (EQUITY · PAPER, TODAY, WIN RATE (EST.) with its range and sample, MAX DRAWDOWN
against the cap, 27 px values, sparklines), equity curve 170 px + drawdown band 64 px with
1D / 1W / 1M, open positions (7 columns, 52 px rows, "SL ON SERVER"), decision trace (56 px
rows, 330 px pipeline: trend, risk, spread, news, order), sessions (UTC), risk limits (daily
risk, open trades, daily drawdown, per-trade size), system health, Go-Live checklist (3/5).
Backend to add: per-trade size in the risk snapshot, a structured Go-Live checklist, the last
price per open position.

### 20d. Simple view
The proposal card with its aside (372 px), the risk/probability bar, hold-to-approve
(320 × 52, release cancels, keyboard too, then the usual confirmation and engine re-check),
the two-step emergency cover and the toast. Real signals only.

### 20e. AI Lab (the most important page, built in four parts)
The new design puts the whole Manual loop **inside the chat** as result cards, with an
inspector of six panels on the side. See `09_ai_lab.md`.

- **20e1 Chat core.** Title row (ANALYZE / 09, subtitle, PAPER ONLY, REAL ORDERS: NEVER), the
  live four-step strip (Export, Paste, Run comparison, Activate in Paper; green only when the
  step is really done), transcript and composer 760 px (bubble, AI text, skeleton, live
  cursor, Enter sends, Shift Enter new line, Stop, the composer glow while typing / busy),
  the real read-only agent (`run_turn`, steps folded above the answer, stop between steps),
  new chat with its four suggestions, quick prompts.
- **20e2 The Manual loop as cards.** Export for AI (files, sizes, scope), Ask AI (model,
  endpoint, summary size against the 12,000 cap, tokens, cost, "not sent: passwords, API key,
  trade list, account number"), Paste the answer (VALID / problems, strategy / parameter / now
  / suggested, the AI's reason and expected effect), Run comparison (two full backtests,
  metrics, the three checks, the verdict box), Activate in Paper (the four checks, activate
  with confirmation, ignore, what is saved). Every card calls the existing code
  (`export_for_ai`, `llm_panel`, `parse_suggestion`, `start_test`, `activate`).
- **20e3 Inspector.** Vertical rail (58 px) + panel (372 px): Settings (export scope, optional
  Ask AI endpoint with the key in Windows Credential Manager, prices, comparison symbol and
  period; "send the account number" removed), History (saved chats, search, groups, tags),
  Prompts (saved prompts, save current), Files and exports, Experiments (#n per suggestion with
  its verdict, out-of-sample equity of current vs AI), Usage and cost (tokens in/out per
  month, cost against the cap, requests per day for 7 days, model). Kept from the app: Plan
  mode, memory notes, context switches (trade rows, logs; account number always off),
  scheduled reviews (off by default), the AI settings dialog behind the gear.
- **20e4 Visual answers.** Native chart cards the agent can ask for, drawn only from tool
  data: candles with signals, equity current vs AI, parameter heatmap, Monte Carlo fan,
  R distribution, walk-forward windows, trade table. Backend to add: `app/ai/visuals.py`
  (data shapes), a usage ledger (`ai_usage`), saved prompts, experiments.

### 20f–20q. The other pages, one phase each
20f Market, 20g Signals, 20h Positions & trades, 20i Stats, 20j Journal, 20k Backtest,
20l Model, 20m Strategies, 20n Risk, 20o Logs, 20p Health, 20q Settings. Each follows its spec.
Current pages the design does not have get these places: Connection, Data, Updates and
Notifications as Settings sections (with a header shortcut for notifications), Go-Live as a
Risk / Health card plus the dashboard checklist, Demo test and the soak report in Health,
the scanner and approve / reject in Signals, calendar management behind "Manage" on Market,
saved backtest runs in Backtest.

## Order and status

| Phase | What | Status |
| --- | --- | --- |
| 20a | tokens, fonts, shared pieces | 0.33.0 |
| 20b | frame | next |
| 20c | dashboard | |
| 20d | simple view | |
| 20e1–20e4 | AI Lab | |
| 20f–20q | other pages | |
