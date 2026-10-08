# AI Lab agent (Phase 18)

Status: 18a built (settings window). 18b to 18g planned. Written 8 October 2026.

The AI Lab today is a four-step form: export, paste the answer, backtest, activate. Phase 18
turns it into a conversation with an agent that reads the app, the trades and the logs by
itself, shows what it is doing while it works, answers with charts and tables, and proposes
setting changes that **you** apply with one click after reading (or after asking someone).

The old four steps stay, as the "Manual" tab, for any outside AI.

Rule from AI_DESK.md stays: **the AI proposes, the app decides, you approve.** The agent can
never send an order, change the execution mode, touch Go-Live, the kill switch, the
credentials or its own permissions.

## 1. What it looks like

```
+--------------------------------------------------------------------------------+
| AI Lab              [grok-4.6 | today $0.08]   [New chat] [History] [⚙ Settings]|
| [ Chat ] [ Manual ]                                                            |
+--------------------------------------------------------------------------------+
| You: why did channel_breakout lose today?                                      |
|                                                                                |
| ▼ Worked for 14 s · 4 steps                                                    |
|   ✓ Read 3 trades of channel_breakout (today)           0.2 s                  |
|   ✓ Opened the decision trace of the EURUSD sell        0.1 s                  |
|   ✓ Read the H1 analysis card at 10:00 UTC              0.1 s                  |
|   ✓ Thought about it (reasoning summary)                 9 s                   |
|                                                                                |
| AI: It sold 0.1 ATR below the channel low, right on support 1.11818 ...        |
|   [trade card: EURUSD sell, mini chart with entry / SL / TP / support line]    |
|   [table: break size vs result, last 30 breakouts]                             |
|   [proposal card: channel_breakout.min_break_atr 0.0 -> 0.3]                   |
|        [Backtest] [Apply in Paper] [Copy for review] [Discard]                 |
|   4 steps · 6.1k tokens · $0.014                                               |
+--------------------------------------------------------------------------------+
| [ Ask about trades, settings, the app...                       ] [Send] [Stop] |
| Context: last 30 days · all strategies · Paper + Live   (change)               |
+--------------------------------------------------------------------------------+
```

- Messages in Persian are right to left when the app language is Persian; the agent answers
  in the app language unless asked otherwise.
- **Thinking** is a collapsible block: one line per step (tool call) with a time, then the
  provider's reasoning summary when it gives one (Responses API `reasoning.summary`). Raw
  hidden reasoning is never asked for or shown.
- **Stop** cancels between steps; a cancelled turn keeps what it has.
- Every answer ends with its cost line: steps, tokens (cached shown), USD.

## 2. The agent loop

1. The user message plus a short system prompt (section 7) and the chat so far (compacted,
   section 8) go to the model with the tool list (section 3), function calling on.
2. Each tool call runs locally in the `ai-lab-agent` worker thread, read only, with a time
   limit; its result goes back as a tool message. The UI shows the step as it starts and
   ends.
3. Until the model answers without a tool call, or a limit is hit: max steps per message
   (default 8), max tokens per message (default 60k), max USD per message (default 0.10)
   and per day (default 1.00). A limit hit ends the turn with "stopped at the limit" and
   what it found so far.
4. Models without function calling get the same tools as a JSON protocol in the prompt
   (`{"tool": "...", "args": {...}}`), parsed by the same code; Test connection records
   which one works (learned options, as in 17a).

Uses `app.ai.transport` (both API styles, retries, timeouts, redaction) and `app.ai.cost`.

## 3. Tools (read only)

| Tool | Returns |
|---|---|
| `overview()` | mode, account currency, balance, open positions, today's result, enabled strategies, health state |
| `trades(strategy?, symbol?, mode?, days?, result?, limit?)` | compact rows: time, symbol, side, R, net, exit reason, strategy |
| `trade(id)` | one trade with its signal's full decision trace and the analysis card at entry |
| `signals(strategy?, state?, days?, limit?)` | signals including filtered and rejected ones, with the failing filter |
| `strategy_settings(strategy?)` | current params + the schema (type, min, max, description) |
| `filter_settings()` / `risk_settings()` | current values + schema |
| `stats(group_by, strategy?, days?)` | win rate, expectancy R, PF by strategy / symbol / session / hour / weekday |
| `market(symbol)` | the newest analysis card: trend matrix, levels, volatility, session, news |
| `calendar(days)` | upcoming high-impact events |
| `health()` | health checks, soak result, last errors |
| `logs(level?, contains?, since_hours?, limit?)` | log lines, scrubbed by `observability/masking.py` |
| `backtests(limit?)` | saved backtest runs and their metrics |
| `run_backtest(symbol, months, strategy, params?)` | runs in the backtest worker, at most 12 months, one at a time; asks the user first when longer than 3 months |
| `propose_changes(changes, reason, expected_impact)` | creates a proposal card (section 4); changes nothing |

All results are size-capped (default 6k characters per call, the rest summarised with a
"truncated" note). The account number, login, names, chat ids and keys never leave the
app unless "Send the account number" is ticked.

## 4. Proposals and applying them

`propose_changes` is the only way the agent affects settings. A proposal holds a list of
`{area, target, parameter, old, new}` with the reason and the expected impact.

- **Allowed areas** (each can be switched off in Settings): strategy params, strategy on
  and off, signal filters, risk limits. Never: execution mode, Go-Live, credentials, the
  AI's own settings.
- Each change is validated against its schema (min, max, type) and against the current
  value (stale proposals are refused, like the AI Lab check today).
- The card shows the difference, a risk direction badge (**safer** / **riskier** /
  **neutral**, e.g. a smaller risk per trade is safer), and the buttons:
  - **Backtest**: the same comparison as step 3 today, result inside the card.
  - **Apply**: Paper or Analysis-only mode only. Riskier changes and changes to risk
    limits need a backtest first and a typed "apply". In Live mode Apply is disabled; the
    card says why.
  - **Copy for review**: a plain text block (what, why, numbers, backtest result) to paste
    to anyone before deciding.
  - **Discard**.
- Applying writes the settings, a config version (`created_by = "ai_agent"`), an audit row
  with the chat and message id, and adds **Undo** to the card (restores the exact previous
  values while they are still current).

## 5. Visual answers

The answer is Markdown. Fenced blocks with these types are drawn natively; anything else, or
JSON that fails its schema, is shown as a code block:

- ` ```kpi ` up to 6 tiles `{label, value, delta?, good?}`
- ` ```table ` `{columns, rows}` (max 50 rows, sortable)
- ` ```chart ` `{kind: line | bar | histogram | equity, series, x?, y_label?}` with
  pyqtgraph, theme colors, max 2,000 points
- ` ```trade ` `{trade_id}`: a trade card with a mini M15 chart, entry, SL, TP, levels
- ` ```proposal ` is created only by the tool, never from text, so a model cannot fake one

Charts and tables can be built only from data a tool returned in this chat (the renderer
checks the numbers against the tool results' ids where it can; free numbers get a "from
the AI" mark).

## 6. "Check the app" mode

A button above the input, **Review the app**, runs a fixed checklist through the same tools:
health, last errors and warnings, filters that reject most signals, strategies with weak
results, risk settings against the Go-Live profile, data quality, disk and update state.
The answer is a report with KPI tiles and proposals; it repeats on demand only.

## 7. Prompt

System prompt (versioned, `prompts.py`, hash in the audit row): you are the analyst of this
trading app; use the tools, never guess numbers; say when the sample is too small (fewer
than 30 trades = "too few to judge"); propose changes only through `propose_changes`, at
most 3 per answer, each with a measurable expected impact; prefer safer changes; answer in
the user's language; keep answers short with one visual where it helps.

## 8. Chats, memory and cost

- Tables (migration + schema + cloud sync off for these): `ai_chats(id, title, created,
  updated, model)`, `ai_messages(id, chat_id, role, content, tool_name, tool_args,
  tool_result_size, tokens_in, tokens_cached, tokens_out, usd, created)`,
  `ai_proposals(id, chat_id, message_id, changes, state, applied_at, undone_at)`.
  Shared with 17a part 2 (`ai_calls`).
- Long chats are compacted: older turns become one summary message; tool results older
  than the last 3 turns are dropped (their ids stay, the agent can call again).
- "Lessons": the agent may save at most 10 short notes (`ai_notes`) the user can see and
  delete in Settings; they go into every prompt.
- History: list, rename, delete, export to Markdown. Kept 90 days by default.

## 9. Settings window (the gear)

Everything set-able for the AI Lab lives in one window:

- **Connection** (18a, built): preset, endpoint, model, API key, prices, API style,
  reasoning effort, Save, Test connection, Remove key.
- **Agent** (18c): permission (Read only / Can propose, default Can propose), max steps,
  max tokens and USD per message, USD per day, answer language (app / English / Persian),
  reasoning effort for chat.
- **Data sharing** (18c): send the account number, include logs, include trade rows
  (otherwise only statistics), days of history by default.
- **Proposals** (18e): allowed areas, require a backtest before Apply (on), allow riskier
  changes (off).
- **Chats and notes** (18g): keep days, clear history, the saved notes.

## 10. Phases

| Phase | What | Tests |
|---|---|---|
| 18a | Settings window behind the gear; the card keeps one status line | dialog holds every field, save updates the card, reopen drops unsaved edits |
| 18b | Chat tab shell: message list, input, Send/Stop, worker thread, plain answers, cost line, chat tables | fake transport: a turn, a stop, a failure without the key |
| 18c | Tools 1: overview, trades, trade, signals, settings, stats, health, logs; thinking steps; Agent and Data sharing tabs | each tool's output cap and masking; step limit; JSON fallback protocol |
| 18d | Visual blocks: kpi, table, chart, trade card; RTL messages | renderer schema checks, bad JSON falls back |
| 18e | `propose_changes`, proposal card, Backtest, Apply (Paper), Undo, Copy for review, audit | stale proposal refused, riskier needs backtest, Live disabled, undo |
| 18f | `market`, `calendar`, `run_backtest`, Review the app | backtest limits, checklist report |
| 18g | Compaction, history, notes, export | compaction keeps ids, retention |

Order with Phase 17: 17a part 2 (`ai_calls` storage, worker) first, since 18b uses it;
then 18b to 18e; then 17b (AI Desk page) and the rest of 17 and NEWS_WATCH.
