"""Writes the adichtion logo as SVG and PNG (design output; see README.md in this folder).

The drawing itself (concept "a-trace", chosen 28-09-2026: chart-recorder paper, one ink
trace writing an "a" that runs on into a spike marked as a selection) lives in the app,
``src/adichtion/ui/logo.py``, which is the only place to edit palette or geometry. This
script renders that module's output: ``adichtion-logo.svg`` (1024 px), ``adichtion-icon.svg``
(64 px) and, when cairosvg is installed, the PNG renders in ``renders/`` (the logo drawing at
1024, 512, 256 and 128 px, the icon drawing at 64, 48, 32 and 16 px). A test in
``tests/ui/test_logo.py`` checks that the checked-in SVGs match the module, so rerun this
after any change there::

    python design/logo/adichtion_logo.py [outdir]

The installer icons are rendered separately by ``scripts/render_icons.py``.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from adichtion.ui.logo import ICON_SIZES, LOGO_SIZES, build_icon_svg, build_logo_svg, svg_for_size
except ImportError:                                   # not installed: run from the checkout
    sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))
    from adichtion.ui.logo import ICON_SIZES, LOGO_SIZES, build_icon_svg, build_logo_svg, svg_for_size


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
    main(sys.argv[1] if len(sys.argv) > 1 else _HERE)
