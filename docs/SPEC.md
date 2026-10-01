# MT5 Trading Workstation Specification

This file is the source of truth for implementation. The complete product specification is maintained from the attached v9 prompt. Phase 1 deliberately implements only the Foundation phase: tooling, documentation, a safe desktop shell, theme tokens, and CI/build scaffolding.

## Safety invariants

- Paper mode is the first-launch default.
- No code in Phase 1 connects to an account or places orders.
- The product never promises profit.
- Real and Auto modes require later Go-Live gates and typed confirmation.
- Secrets never enter source control, logs, exports, or prompts.

## Phase plan

1. Foundation
2. Observability
3. Real MT5 connection
4. Storage and synchronization
5. Market data and analysis
6. Strategies and signals
7. Risk management
8. Execution
9. Simple Mode
10. Backtesting
11. ML
12. Analytics and journal
13. AI loop and Go-Live
14. Reliability
15. In-app updates
16. Release polish

See the attached v9 specification for the detailed acceptance criteria for each phase. Every phase must stop after its acceptance checklist.
