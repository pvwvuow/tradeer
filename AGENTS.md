# Agent rules

1. Read `docs/SPEC.md` and `docs/PROGRESS.md` before changing code.
2. Follow the architecture rules: domain purity, non-blocking UI, one MT5 owner thread, closed-bar decisions, and shared live/paper/backtest logic.
3. Never promise profit. Never bypass risk controls. Never commit secrets.
4. Use Python 3.11 64-bit on Windows. Run `ruff check .`, `ruff format --check .`, `mypy app tests`, and `pytest` before pushing.
5. Use Conventional Commits. Work on `phase/<nn>-<name>` branches and open a PR into `main`; never push directly to `main`.
6. Stop after each phase acceptance checklist.
