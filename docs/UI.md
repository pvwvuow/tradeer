# UI design system (0.13.0)

One place for the look of the app. Colors live in `app/ui/theme.py` as tokens; the Qt
stylesheet is generated from them, so a theme switch restyles every widget at once.

## Tokens

- Base: `bg`, `surface`, `card`, `border`, `text`, `text_secondary`, `accent`, `accent_text`,
  `profit`, `loss`, `warning` (the dark values are pinned by the spec).
- Added in 0.13: `hover`, `accent_soft`, `border_strong`, `profit_soft`, `loss_soft`,
  `warning_soft`. Every chip tone's text meets WCAG AA on its soft fill (tested).

## Building blocks

- `app/ui/pages.py`: `styled_label(text, role)`, `card_frame()`, `PageHeader` (group crumb,
  title, summary, optional actions on the right), `empty_state(title, text)`.
  `decorate_page` swaps a page's plain title label for the full header, so pages only build
  `styled_label("Title", "title")` and the main window does the rest.
- `app/ui/style.py`: icons drawn from the Windows icon font (Segoe Fluent Icons or Segoe MDL2
  Assets, no image files; skipped where the font is missing), `chip`/`set_chip` with tones
  neutral, accent, profit, loss and warning, `style_plots` (pyqtgraph charts follow the theme),
  `style_tables` (zebra rows, no grid, 30 px rows, left-aligned headers), `repolish`.
- `app/ui/tables.py`: `make_table` uses the same table look.

## Label roles

`title`, `subtitle`, `crumb` (small caps group or section), `heading`, `section`, `muted`,
`status`, `kpi`, `kpi_profit`, `kpi_loss`, `profit`, `loss`, `warning`, `badge`, `brand`,
`logo`, `empty_icon`.

## Button variants

Default, `primary`, `danger`, `ghost` (top bar), and `nav="true"` for the sidebar.

## Top bar

Logo, the current location (for example TRADE / DASHBOARD), the MT5 chip (demo green, REAL
red, connecting amber), the mode chip (Paper blue, Analysis-only amber, Semi-auto and Auto red),
Search (Ctrl+K, Advanced view only), Settings (Simple view), the view switch and the theme
switch.

## Rules

- No hard-coded colors in pages; use tokens or roles. Charts get their colors from
  `style_plots`.
- A dynamic property change (role, chip, tone) needs `repolish(widget)`.
- Never name a widget attribute after a QWidget method (`actions`, `title` is fine,
  `scroll` is not).
- The stylesheet must parse without a Qt warning (tests/ui/test_design_system.py).
