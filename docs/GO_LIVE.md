# Go-Live gate (Phase 13b, spec C9)

Auto mode sends signals without asking. On a REAL account a strategy may do that only after
the Go-Live gate approved exactly its current config (params hash) on exactly that account.
Demo accounts need no approval (switching to Auto still needs the typed word AUTO).

## The checklist (`app/engine/go_live_gate.py`)

| Check | Passes when | Default |
|---|---|---|
| Walk-forward backtest | the newest saved backtest of these params with a walk-forward has enough out-of-sample trades and a positive out-of-sample expectancy | 100 trades |
| Paper trades | enough paper trades of the strategy with a positive expectancy (R) | 30 trades |
| Paper slippage | their mean absolute slippage is within the assumption (not recorded counts as fine) | 2 points |
| Calibration | the actual win rate is within the band of the mean predicted probability, over enough rated trades | 10 points, 30 trades |
| Errors and health | no CRITICAL log line and no failing health check | 7 days |
| Risk settings | the risk settings were reviewed and confirmed, and have not changed since (hash) | |

The thresholds live in `go_live.json` in the profile folder together with the risk review and
the approvals. A missing or broken file means no approvals (the safe side).

## Approving (Strategies page, Go-Live checklist)

- Pick the strategy, read every check (value and what it needs), press **Check again** after a
  new backtest or trades.
- **Risk settings reviewed** records the hash of the current risk settings; any later change
  of the Risk page turns that check red again.
- **Approve for Auto**: all checks green asks a plain confirmation. A failed check approves only
  with the typed phrase `I ACCEPT THE RISK`; the approval records that it was an override and
  which checks failed.
- **Remove approval** takes it back. Every action is in the audit log (`go-live approved`,
  `go-live override`, `go-live approval removed`, `go-live risk settings reviewed`).
- Any change of the strategy's params makes the approval invalid ("approve the new settings
  again").

## Auto mode

- Positions page: Auto is in the mode list. Switching needs the typed word `AUTO`; on a REAL
  account every strategy that is on must have a valid approval first (`GoLiveDesk.auto_check`).
- Execution engine: in Auto every new pending signal (all filters and risk checks passed) is
  sent once on the next cycle without a click. Before each order the engine asks the gate again
  (`GoLiveDesk.guard`, `auto_block`); a refused signal expires with the reason. Without the gate
  Auto sends nothing. The kill switch stops Auto like any other mode.
- Auto is saved in `execution.json` and survives a restart (the startup log warns about it).
- Dashboard: the Go-Live line shows, per strategy that is on, how many checks pass and whether
  it is approved (refreshed every minute).

## Next

- 13c: the optional OpenAI-compatible LLM in the AI Lab (key in the keyring, off by default).
