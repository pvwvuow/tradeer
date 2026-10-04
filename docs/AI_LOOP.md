# AI Lab: the loop with an outside AI (Phase 13a)

Spec C13 and F3.9. The app never sends your data anywhere by itself: you choose the AI,
you give it the files, you paste its answer back. Every suggestion is checked, backtested
against the current settings and turned on only in Paper (or Analysis-only) mode.

## The four steps (Advanced > Analyze > AI Lab)

1. **Export for AI** writes `profiles/<profile>/exports/ai_<UTC stamp>/`:
   - `trades_full.csv` and `trades_full.json`: every field of every closed trade (JSON also
     has the times as UTC text).
   - `report.md`: the analysis prompt at the top, then the data scope, statistics,
     breakdowns (symbol, strategy, direction, session, hour, weekday, month, holding time,
     probability, config, bot or manual, mode), probability calibration (predicted vs
     actual win rate per bucket), MFE/MAE, costs, rejected signals by reason, live and
     paper vs the recent backtests, and for each strategy its current params and its
     JSON Schema (the limits every suggestion must keep).
   - Filters: strategy, mode (live or paper) and the last N days (0 = all time).
   - "Copy prompt" puts only the prompt on the clipboard.
2. **Paste the answer.** The app reads the fenced JSON block (or the outermost object):
   `{"changes": [{"strategy", "params", "reason", "expected_impact"}]}`; a bare list or a
   single change object also works. Refused, each with a plain reason: invalid JSON,
   unknown strategies or parameters, values outside the schema (pydantic validation of
   the merged params), a missing reason, no actual change, a strategy listed twice.
   The difference table shows Strategy, Parameter, Now, Suggested.
3. **Run comparison**: two full backtests on the same symbol and period (default the last
   six months), the current settings vs the suggested ones, only the changed strategies,
   no Monte-Carlo. Both runs are saved in `backtest_runs`. The verdict is "better" only
   with at least 30 trades, a higher expectancy in R and a max drawdown no deeper than
   1.25 x the current one plus 1 point. One period is not proof; the verdict says so.
4. **Activate in Paper**: refused while the operating mode places real orders
   (Semi-auto, Auto). Needs a comparison of exactly this suggestion; a "not better"
   verdict asks for confirmation. It refuses if the saved params changed since the check.
   Then it saves `strategies.json` (on/off unchanged), writes a `strategy_configs` row per
   strategy (`created_by = "ai_suggestion"`, `parent_config_id` = the config it replaces,
   the reason and expected impact in `notes`) and one `audit_log` row (`source =
   "ai_suggestion"`, params before and after, the verdict).

## Not in 13a

- Walk-forward of the suggestion vs the current settings (13a compares two full-period
  backtests). Run a walk-forward on the Backtest page for more confidence.
- Counterfactuals (what rejected signals would have done): the report says they are missing.
- The Go-Live gate (C9) for Auto on a real account and the optional built-in LLM call: 13b.

## Code

- `app/analytics/ai_export.py`: `select_trades`, `trades_json`, `rejected_counts`,
  `calibration`, `build_report`, `write_export`.
- `app/analytics/ai_import.py`: `parse_suggestion`, `diff_rows`, `apply_suggestion`,
  `config_rows`, `audit_row`, `activation_block`, `RunSummary`, `compare_runs`,
  `summary_rows`.
- `app/ui/ai_lab_page.py`: `AiLabPage`, `AiLabContext`, `ai_lab_context(analytics, backtest)`
  (the main window builds it from the Analytics and Backtest contexts).
- Tests: `tests/unit/test_ai_export.py`, `tests/unit/test_ai_import.py`,
  `tests/ui/test_ai_lab_page.py`.
