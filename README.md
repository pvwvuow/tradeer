# MT5 Trading Workstation

A safety-first Windows workstation for MetaTrader 5 analysis, paper trading, and eventually controlled execution.

> This software does not promise profit. Probability is an uncertain statistical estimate, and Paper mode is the default.

## Phase 1

Phase 1 creates the runnable desktop shell and engineering guardrails. MT5 connection, real market data, risk engine, and execution arrive in later phases.

### Requirements

- Windows 10/11, 64-bit
- Python 3.11 64-bit
- MetaTrader 5 terminal for later phases

### Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m app.main
```

Run checks:

```powershell
ruff check .
ruff format --check .
mypy app tests
pytest
```

The current shell has no trading connection and never creates orders. Do not connect a real account until the execution and Go-Live gates are implemented and tested.
