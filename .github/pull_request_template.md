## What was built

## How to test it

```powershell
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest
```

## Test on your PC

1. Download the artifact `MT5TradingWorkstation-<run>` from this PR: Checks, then Artifacts.
2. Unzip it and run `MT5TradingWorkstation.exe --self-check --report-file self-check.txt`.
3. Open the app and do the steps listed for this phase. Expected result:
4. If something fails, attach the self-check report or crash report as a comment on this PR.

## Phase acceptance checklist

- [ ] 

## Known limitations

## Screenshots
