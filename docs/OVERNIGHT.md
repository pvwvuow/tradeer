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
| 5 | PC log of 7 Oct: the demo test's 26 trades were in the daily report of 6 Oct and counted toward the trades-per-day limit (Auto would refuse signals until midnight); now they count nowhere (0.24.1) | #70 | merged |

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
