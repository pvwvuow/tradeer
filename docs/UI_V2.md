# Workstation UI v2

The owner's own design (ClickUp artifact "MT5 Workstation UI v2", 9 October 2026), a React
mockup, brought to the PySide6 app in phases. The mockup is the reference; this file maps it
to Qt and tracks what is built.

## The look

| Design | Qt |
|---|---|
| Ink page `#0E100F`, cream text `#ECE8DC`; light theme cream paper `#F1EEE6`, ink text | `app/ui/theme.py` `DARK` and `LIGHT` tokens (every pair passes WCAG AA, unit tested) |
| The ink is the accent: current page, primary button, checked segment drawn inverted | `accent == text`, `accent_text == bg`; `QPushButton:checked`, `[nav="true"]:checked`, `[variant="primary"]` |
| Green `#5AD19A`, coral `#F2795F`, amber `#D9B45A` only for results and warnings | `profit`, `loss`, `warning` |
| Flat, hairline borders, radii 8 / 6 / 4 | no `qlineargradient` (a test keeps it out), `RADIUS`, `RADIUS_CONTROL`, `RADIUS_SMALL` |
| Underlined tabs | `QTabBar::tab:selected` has a 2 px bottom border |
| Outlined tags (PAPER, SL ON SERVER, "2 proposals") | chips and badges: tone text and a 1 px edge on the soft fill |
| IBM Plex Mono for numbers and captions | `NUMBER_FONTS`: IBM Plex Mono, then Cascadia Mono and Consolas (Windows ships them) |
| Vazirmatn for Persian | already bundled for the Simple view (`app/ui/i18n.py`) |
| Sidebar pages numbered 01 to 14 under TRADE / ANALYZE / SYSTEM rules | `main_window.nav_text`, section labels with a bottom rule |

## Phases

- **19a, 0.31.0: the look.** Tokens, stylesheet, numbered sidebar without icons, outlined
  logo and chips.
- **19b: AI Lab v2 layout.** Left rail with chat history (search; all / pinned / with
  proposals; date groups; proposal count badge), centre chat with a thinking line
  ("14 s · 4 tools · 6.1k tokens", folds open), right panel tabs Proposals / Context / Memory
  / Auto, input with @ context, / commands, plan mode, model picker and the budget
  ("$0.08 / $1.00"). Needs chat storage (AI_LAB_AGENT.md 18c).
- **19c: visual answers and proposal cards** (AI_LAB_AGENT.md 18d and 18e): KPI strip, trade
  chart with entry, SL and TP, break-depth rows, heatmap; cards with old → new value and
  Apply / Undo / Copy for review; the agent never applies anything by itself.
- **19d: Dashboard layout.** Go-Live checklist strip, five KPI cells in one frame
  (balance, equity with sparkline, today, open risk, 30 days), equity curve with a hatched
  drawdown band, sessions bar (Tokyo / London / New York with a now line), risk limit bars,
  open positions.
- **19e: top bar and ticker.** Live ticker strip of the watched symbols, MT5 · DEMO ·
  CONNECTED chip, status bar in captions. Persian labels for the Advanced pages last.

The rule from AI_DESK.md stays: the AI proposes, the engine and the owner decide.
