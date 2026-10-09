# UI v2, drawn exactly like the design

On 9 October 2026 the owner asked for the app to look exactly like his mockup
("MT5 Workstation UI v2", a React artifact), not only to borrow its colors. This file is the
plan and the record of that work. The colors and fonts are in `docs/UI_V2.md`.

## How the design maps to Qt

| Design (React + CSS)                | Qt                                                         |
| ----------------------------------- | ---------------------------------------------------------- |
| `header` 52 px                      | `MainWindow.top_bar`, `shell.LogoMark`, tags, `KbdButton` |
| LIVE ticker 30 px, 48 s loop        | `shell.TickerStrip` fed by `shell.DayOpens` every second   |
| `nav` 228 px, `cap` group + rule    | `shell.GroupRule`, `shell.NavButton` (number, title, badge) |
| `seg` ساده / پیشرفته                 | `shell.Segmented` (`MainWindow.view_segment`)              |
| `footer` 32 px, Stop + Ctrl Shift K | `QStatusBar` with `shell.Led`, `KbdButton("danger")`      |
| `PageHead` crumb "TRADE / 01"       | `PageHeader`, crumb from `main_window.page_crumb`          |
| Vazirmatn + IBM Plex Mono           | bundled by `scripts/ci/build.ps1`, `shell.load_frame_fonts` |
| `dir="rtl"`                         | Persian: the frame and the page headers run right to left  |

Painted widgets draw themselves from the theme tokens, so they match the design on every
PC; `shell.frame_qss` adds the frame's rules after `theme.build_qss`.

## Phases

- 19c (0.32.0): the frame: top bar, ticker, sidebar, status bar, page headers, fonts.
- 19d: Dashboard as in `Dashboard.jsx`: KPI strip, equity with the hatched drawdown,
  positions, the signal decision trace with pipeline circles, sessions, risk bars,
  market direction, system health.
- 19e: AI Lab as in `AiLab.jsx`: history rail, thinking block, visual answer blocks,
  proposals / context / memory / auto panel, the input bar (@, /, plan, model, budget).
- 19f onwards: Market, Signals, Positions, Stats, Journal, Backtest, Model, Strategies,
  Risk, Logs, Health and Settings, one page at a time, each body in Persian and right to
  left like its mockup page.

Real data only: where the design shows sample numbers the app shows its own (or an honest
empty state). The sidebar badge on Signals and the light on Health come from
`MainWindow.set_nav_badge` and `set_nav_dot`.
