# Architecture

## Phase 1 decision

Use a layered Python package with a pure `domain` package and a Qt UI package. The composition root is `app/main.py`. MT5, storage, network, risk, and execution layers are intentionally absent until their phases.

## Decisions

- **PySide6:** native Windows desktop UI with a maintained Qt binding.
- **Pydantic v2:** validate configuration before it reaches workers or UI.
- **Paper-first:** prevents a polished shell from accidentally becoming a trading system.
- **Python 3.11 64-bit:** matches the product requirement and official MT5 integration target.
