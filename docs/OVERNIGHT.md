# Overnight work, 6 to 7 October 2026

Asked for at 22:15 Tehran time: "every upgrade, improvement and bug fix you can until the
morning; the UI looks old, try harder; write yourself an md file and go phase by phase".
The PC is off overnight, so nothing here can be tried on it before the morning.

| Phase | What | PR | State |
|---|---|---|---|
| 1 | Soak report adds up runs of 4 h or more to 24 h (the PC cannot stay on overnight) | #66 | merged |
| 2 | Fresher look: depth on cards and the top bar, gradient primary buttons, pill tabs, drawn combo and spin arrows, filled check boxes and ring radios, row hover in tables, rounder corners, menus and tooltips | #67 | merged |
| 3 | Window polish (7 Oct): Windows title bar in the theme's colors, pages fade in, hand cursor on everything clickable, candle chart prices on the right with a last-price tag, smooth chart lines | #69 | merged |
| 4 | One release with all of it (0.24.0) | #68 | released |
| 5 | PC log of 7 Oct: the demo test's 26 trades were in the daily report of 6 Oct and counted toward the trades-per-day limit (Auto would refuse signals until midnight); now they count nowhere (0.24.1) | #70 | released |
| 6 | Strategy lab (7 Oct evening): three new strategies (range_reversion, channel_breakout, ema_momentum, off until turned on), the Strategy lab (demo) risk profile and a By strategy review in every report; see STRATEGY_LAB.md (0.25.0) | #72 | released |
| 7 | 8 Oct: the chart's prices looked different from MT5. The tag showed the last closed bar's close and the times were local; now the live bid (tag) and ask lines move every second and the times are broker time, as in MT5 (0.25.1) | #74 | merged |

Rules kept: the spec palette and every WCAG AA pair stay (tests), no image files, no Qt
style property Qt does not know (it would log a warning on every repaint), and a single 24 h
run still gives the same soak report as before.

## Check on the PC

1. Update to 0.24.1 and look through Home, Signals, Positions, Risk and Settings in the dark
   and the light theme: buttons, inputs, check boxes, tabs and tables.
2. The title bar should be dark in the dark theme (Windows 11 also gives it the top bar's
   color). Market > chart: prices on the right and a colored tag at the last price.
3. Send a screenshot of anything that looks clipped or wrong.
4. Each evening keep the app on for 4 hours or more; after three such evenings Health >
   Create soak report should add them up to 24 hours.
5. The daily report of 6 October was saved before the fix and keeps its 26 test trades;
   the reports from 7 October on leave the demo test out.
6. 0.25.0: Strategies page, tick On for the three lab strategies and Save; Risk, profile
   Strategy lab (demo), Use this profile, Save. Then leave Auto on during London and New York.
7. 0.25.1: Market > Chart next to the same MT5 chart: the bid tag should match MT5's bid
   within a second or two, and the time labels should match MT5's.
