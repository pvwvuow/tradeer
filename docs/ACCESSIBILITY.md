# Accessibility and the light theme (Phase 16b)

Spec F1: "profit/loss never by color only (+/- and icons), WCAG AA contrast, full keyboard
navigation, High-DPI, RTL". RTL is Phase 16a (docs/I18N.md).

## Contrast (WCAG 2.1 AA)

`app/ui/theme.py` lists every color pair the app draws (`contrast_pairs`) and
`contrast_failures` reports any pair below its minimum; `tests/unit/test_accessibility_tokens.py`
keeps that list empty for both themes.

- Text: every text color (text, secondary, accent, profit, loss, warning) is at least 4.5:1 on
  the page, panel and card colors; plain text also on the hover and selection fills; every
  status chip on its own fill; button text on the accent color.
- Controls: buttons and inputs have a border of at least 3:1 (`control_border`), and the
  focus ring (accent) is at least 3:1 too.
- The dark palette is the spec's. The light theme's green and amber were darkened
  (`#147236`, `#A84B05`) so they also pass on the hover fill. A danger button keeps its fill on
  hover, so the red text keeps its contrast.

## Never by color only

- Home (Simple view): every result has a sign and an arrow (`▲ +$12.30`, `▼ −$5.00`).
- Dashboard: Today and Last 30 days get an arrow next to the sign; a risk limit bar says
  "(near the limit)" from 75% used and "(limit reached)" at 100%, besides turning amber or red.
- Tables already show signed numbers (`+1.2%`, `-35.00`); colored cells only add emphasis.
- Analytics: wins and losses in the MAE/MFE chart are up and down triangles with a key in the
  chart title; the R chart puts losses left of 0 and gains right of it.
- Health, logs and the kill switch say their state in words (Critical, ERROR, Stop trading).

## Keyboard

- Tab and Shift+Tab reach every button, field and table; the focused control shows a ring.
- Ctrl+, opens Settings (both views), Ctrl+K the command palette (Advanced view) and
  Ctrl+Shift+K the kill switch (it always asks first).
- Buttons that show only an icon or an arrow get a name for screen readers (`name_controls`).

## High-DPI

Qt 6 scales the whole window to the Windows display scale (rounding policy PassThrough, so 125%
and 150% stay exact). Icons are drawn at the screen's pixel ratio (at least 2x), so they stay
sharp up to 300%. Sizes in the stylesheet are in points or logical pixels and scale with it.

## Charts in the light theme

Chart lines take their colors from the current theme (`app.ui.style.chart_tokens`) instead of
the dark theme's fixed colors (Dashboard and Analytics; the Backtest page still uses its own
colors, which read on both themes).

## Check it on the PC (0.21.0)

1. Switch to the light theme: Dashboard and Analytics charts use the light colors.
2. Press Tab a few times on Home: the focus ring moves through the buttons. Ctrl+, opens
   Settings.
3. Windows display scale at 150%: text and icons are sharp, nothing is cut off.
