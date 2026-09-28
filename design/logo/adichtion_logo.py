"""The adichtion logo, generated as SVG (design source; see README.md in this folder).

Concept "a-trace" (chosen 28-09-2026): a sheet of chart-recorder paper (cream, green
millimetre grid) on which one ink trace writes a lowercase "a" and runs on into a sniff-like
spike, with the spike marked as a selection (amber band), the app's core action. The paper
points back to the strip-chart recorder that "LabChart" is named after; the single ink
trace and the selection are what adichtion does with such a recording.

Two drawings share the tile:

- ``build_logo_svg(size)``: the full artwork (grid, band, trace with the "a" and the spike)
  for 128 px and up: app-store / dmg / splash / README.
- ``build_icon_svg(size)``: the simplified mark for small sizes (64 px and below): the "a"
  alone, in ink on the paper, without grid or band. ``svg_for_size(px)`` picks one.

Pure Python, no Qt: the same string can be handed to ``QSvgRenderer`` in ``ui/logo.py`` (as
RespMech does) or to cairosvg/Inkscape. No filters, masks or clipPaths are used, so Qt's SVG
Tiny renderer draws it exactly like a browser. Geometry is in a 1024×1024 viewBox with the
tile inset like RespMech's (87.5 % of the canvas, 25 % corner radius), so the two apps sit
level in a dock.

Run as a script to (re)write the SVGs and, when cairosvg is installed, the PNG renders::

    python design/logo/adichtion_logo.py [outdir]
"""
from __future__ import annotations

import math
import os
import sys

# ---- canvas and tile ----------------------------------------------------------------
CANVAS = 1024
TILE0, TILE1 = 64, 960            # tile extent on the canvas (RespMech's proportions)
TILE_W = TILE1 - TILE0
RADIUS = 224                      # 25 % of the tile width

# ---- palette ---------------------------------------------------------------------------
PAPER_HI, PAPER_LO = "#F7F0DC", "#EBDFBF"   # chart-recorder paper, top-left to bottom-right
PAPER_EDGE = "#CDBB90"                      # thin edge that keeps the light tile defined
GRID = "#7FA26F"                            # the green millimetre grid
INK = "#26242E"                             # blue-black recorder ink
AMBER = "#E39A2B"                           # the selection band

# ---- geometry of the mark ---------------------------------------------------------------
# The large "a": bowl centre, radius; the stem sits on the bowl's right at x = cx + r.
LOGO_A = dict(cx=420, cy=530, r=118)
LOGO_SPIKE_H = 210                # height of the spike above the baseline
LOGO_LEAD_IN, LOGO_LEAD_OUT = 118, 906
LOGO_STROKE = 46
LOGO_BAND = (660, 776)            # x extent of the selection band (centred on the spike)
# The small "a": bigger and centred, no tail.
ICON_A = dict(cx=486, cy=512, r=150)
ICON_STROKE = 104


def _rounded_span(v: float):
    """Inside extent of the rounded tile along one axis at coordinate ``v`` on the other,
    so grid lines can run edge to edge without a clipPath."""
    if v < TILE0 + RADIUS:
        d = (TILE0 + RADIUS) - v
    elif v > TILE1 - RADIUS:
        d = v - (TILE1 - RADIUS)
    else:
        return TILE0, TILE1
    if d >= RADIUS:
        return None
    h = math.sqrt(RADIUS * RADIUS - d * d)
    return (TILE0 + RADIUS) - h, (TILE1 - RADIUS) + h


def _grid(step=56, minor_opacity=0.30, major_every=5, major_opacity=0.55, inset=8, width=3):
    out = []
    k, v = 0, TILE0 + step
    while v < TILE1:
        k += 1
        major = k % major_every == 0
        op = major_opacity if major else minor_opacity
        w = width + 1 if major else width
        span = _rounded_span(v)
        if span:
            a, b = span[0] + inset, span[1] - inset
            out.append(f'<line x1="{v}" y1="{a:.0f}" x2="{v}" y2="{b:.0f}" stroke="{GRID}" '
                       f'stroke-opacity="{op}" stroke-width="{w}"/>')
            out.append(f'<line x1="{a:.0f}" y1="{v}" x2="{b:.0f}" y2="{v}" stroke="{GRID}" '
                       f'stroke-opacity="{op}" stroke-width="{w}"/>')
        v += step
    return "".join(out)


def _band(x0, x1, opacity=0.32):
    y0, y1 = TILE0 + 40, TILE1 - 40
    return (f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="{AMBER}" '
            f'fill-opacity="{opacity}"/>'
            f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="{AMBER}" stroke-width="5" stroke-opacity="0.9"/>'
            f'<line x1="{x1}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="{AMBER}" stroke-width="5" stroke-opacity="0.9"/>')


def a_trace_path(cx, cy, r, spike_h, lead_in, lead_out):
    """One continuous stroke: baseline in from the left, the bowl of the "a" (a full circle
    entered at its bottom), the stem up and down again, then the spike and the baseline out."""
    top, base, sx = cy - r, cy + r, cx + r
    return (f"M{lead_in},{base} H{cx} A{r},{r} 0 1 1 {sx},{cy} V{top} V{base} "
            f"H{sx + 148} L{sx + 180},{base - spike_h} L{sx + 212},{base} H{lead_out}")


def a_path(cx, cy, r):
    """The bare "a": a full bowl and the stem on its right (two sub-paths, one stroke)."""
    top, base, sx = cy - r, cy + r, cx + r
    return f"M{sx},{cy} A{r},{r} 0 1 1 {sx},{cy - 0.01} M{sx},{top} V{base}"


def _svg(body: str, size: int) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}" '
            f'width="{size}" height="{size}">'
            f'<defs><linearGradient id="paper" x1="0" y1="0" x2="0.4" y2="1">'
            f'<stop offset="0" stop-color="{PAPER_HI}"/><stop offset="1" stop-color="{PAPER_LO}"/>'
            f'</linearGradient></defs>'
            f'<rect x="{TILE0}" y="{TILE0}" width="{TILE_W}" height="{TILE_W}" rx="{RADIUS}" fill="url(#paper)"/>'
            f'{body}'
            f'<rect x="{TILE0 + 3}" y="{TILE0 + 3}" width="{TILE_W - 6}" height="{TILE_W - 6}" rx="{RADIUS - 3}" '
            f'fill="none" stroke="{PAPER_EDGE}" stroke-width="6"/>'
            f'</svg>')


def build_logo_svg(size: int = 1024) -> str:
    """The full logo: chart paper, selection band, the ink trace writing the "a" and the spike."""
    body = _grid()
    body += _band(*LOGO_BAND)
    d = a_trace_path(spike_h=LOGO_SPIKE_H, lead_in=LOGO_LEAD_IN, lead_out=LOGO_LEAD_OUT, **LOGO_A)
    body += (f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="{LOGO_STROKE}" '
             f'stroke-linecap="round" stroke-linejoin="round"/>')
    return _svg(body, size)


def build_icon_svg(size: int = 64) -> str:
    """The small mark: the "a" alone in ink on the paper (for 64 px and below)."""
    body = (f'<path d="{a_path(**ICON_A)}" fill="none" stroke="{INK}" stroke-width="{ICON_STROKE}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')
    return _svg(body, size)


def svg_for_size(px: int) -> str:
    """The drawing to use for a given pixel size: the icon up to 64 px, the logo above."""
    return build_icon_svg(px) if px <= 64 else build_logo_svg(px)


LOGO_SIZES = (1024, 512, 256, 128)
ICON_SIZES = (64, 48, 32, 16)


def main(out: str) -> None:
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "adichtion-logo.svg"), "w", encoding="utf-8") as f:
        f.write(build_logo_svg())
    with open(os.path.join(out, "adichtion-icon.svg"), "w", encoding="utf-8") as f:
        f.write(build_icon_svg())
    try:
        import cairosvg
    except ImportError:
        print("SVGs written; install cairosvg for the PNG renders")
        return
    renders = os.path.join(out, "renders")
    os.makedirs(renders, exist_ok=True)
    for s in LOGO_SIZES + ICON_SIZES:
        cairosvg.svg2png(bytestring=svg_for_size(s).encode(), output_width=s, output_height=s,
                         write_to=os.path.join(renders, f"adichtion-{s}.png"))
    print("SVGs and PNG renders written to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__)))
