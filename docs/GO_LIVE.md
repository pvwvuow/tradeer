# Go-Live gate (Phase 13b, spec C9)

Auto mode sends signals without asking. On a REAL account a strategy may do that only after
the Go-Live gate approved exactly its current config (params hash) on exactly that account.
Demo and contest accounts need no approval (switching to Auto still needs a confirmation).

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

## Approving

- All checks green: the approval is stored with the time, the account and the params hash.
- A check failed: only the typed phrase `I ACCEPT THE RISK` approves; the approval records
  that it was an override and which checks failed. The caller writes it to the audit log.
- Any change of the strategy's params makes the approval invalid ("approve the new settings
  again"); `auto_block(state, strategy, params_hash, account, real=...)` returns the reason.

## Roll-out

- 13b part 1 (this): the gate, its state and tests.
- 13b part 2: Auto mode in the engine (auto-send pending signals that pass `auto_block`),
  Auto in the Positions page mode switch (typed confirmation, REAL needs the gate), the
  checklist on the Strategies page and the readiness on the Dashboard.
- 13c: the optional OpenAI-compatible LLM in the AI Lab (key in the keyring, off by default).
