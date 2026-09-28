# adichtion logo

The app icon and logo, as design output. The drawing itself (palette, geometry, the SVG
builders) lives in the app, `src/adichtion/ui/logo.py`, so the running app and the installers
carry the same source; `adichtion_logo.py` here imports it and writes the SVGs and PNG
renders next to it (`python design/logo/adichtion_logo.py`). A test
(`tests/ui/test_logo.py`) fails when the checked-in SVGs no longer match the module.

## Concept: "a-trace" (chosen 28-09-2026)

A sheet of chart-recorder paper (cream, green millimetre grid) on which a single ink trace
writes a lowercase "a" and runs on into a sniff-like spike; the spike is marked as a
selection with an amber band, which is the app's core action (pick a section, export it).
The paper points back to the strip-chart recorder that "LabChart" is named after; the one
ink trace and the selection are what adichtion does with such a recording.

It is deliberately unlike the two marks it will sit next to: ADInstruments' logo is a dark
blue (#005581) wordmark with a hand-drawn zigzag spike, and the LabChart icon is a blue
gradient circle with a white ring, one white squiggle and a starburst. No circle, no blue
tile, no white squiggle, no star.

## Two drawings, one tile

| Drawing | Function | Used at | Content |
|---|---|---|---|
| logo | `build_logo_svg(size)` | 128 px and up (app store, dmg, splash, README) | grid, amber band, ink trace with the "a" and the spike |
| icon | `build_icon_svg(size)` | 64 px and below (dock at small sizes, title bars, file lists) | the "a" alone in ink on the paper |

`svg_for_size(px)` picks the drawing. Both use a 1024×1024 viewBox with the tile inset like
RespMech's (87.5 % of the canvas, 25 % corner radius), so the two apps sit level in a dock.
No filters, masks or clipPaths: Qt's SVG renderer draws it the same as a browser.

## Palette

| Role | Hex |
|---|---|
| paper (gradient top-left → bottom-right) | #F7F0DC → #EBDFBF |
| paper edge | #CDBB90 |
| grid | #7FA26F (opacity 0.30 minor, 0.55 every fifth line) |
| ink | #26242E |
| selection band | #E39A2B (fill opacity 0.32, edges 0.9) |

## Files

- `src/adichtion/ui/logo.py` — the geometry and palette (the only thing to edit).
- `adichtion_logo.py` — writes the files below from that module (cairosvg for the PNGs).
- `adichtion-logo.svg`, `adichtion-icon.svg` — the two drawings at 1024 / 64 px.
- `renders/adichtion-{1024,512,256,128}.png` — the logo; `renders/adichtion-{64,48,32,16}.png` — the icon.

## Where it is used

1. `src/adichtion/ui/logo.py` renders the drawing with `QSvgRenderer`: `make_pixmap(size,
   dpr)` picks the drawing by device size, `logo_pixmap(size, dpr)` is always the logo, and
   `app_icon()` is the multi-resolution `QIcon` (16, 24, 32, 48, 64, 128, 256, 512 px) the
   window and the dock show.
2. `installer/adichtion.png` (1024, logo), `installer/adichtion-{16..512}.png` (each from
   `svg_for_size`, so 16 to 64 px are the icon drawing) and `adichtion.ico` / `.icns`,
   assembled from the sized renders, are written by `scripts/render_icons.py`
   (`docs/RELEASING.md` §5).
3. The logo drawing is shown wherever the app shows itself large: the splash
   (`src/adichtion/ui/splash.py`, a sheet of the same chart paper with the tile, the
   wordmark and one breathing trace whose sniff is selected), the About dialog, and the top
   of `README.md` (`renders/adichtion-256.png`).
4. `tests/ui/test_logo.py` checks that both SVGs load in `QSvgRenderer`, that
   `svg_for_size` picks the icon up to 64 px and the logo from 128 px, that the checked-in
   SVGs and installer PNGs are the module's drawings, and that the splash builds.
