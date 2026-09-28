"""Render the installer icons from the app's own logo (``adichtion.ui.logo``).

Writes, into ``installer/`` (docs/RELEASING.md §5):

- ``adichtion.png``: the logo drawing at 1024 px (briefcase's generic icon, the dmg);
- ``adichtion-{16,32,64,128,256,512}.png``: one file per size, as the Linux build wants
  them, each from ``svg_for_size`` so 16 to 64 px get the icon drawing (the bare "a") and
  128 px and up the full logo;
- ``adichtion.ico`` (Windows: 16, 32, 48, 64, 128, 256) and ``adichtion.icns`` (macOS: the
  slots Pillow writes, 16@2x and 32@2x, then 128 to 512 at 1× and 2×; macOS scales the 16 px
  and 32 px 1× slots from those), both assembled from renders at each size rather than by
  scaling the 1024 px logo down, so the small sizes are the icon drawing.

Rendering is Qt's (offscreen), the same renderer the running app uses for its dock and
window icon, so installer and app agree pixel for pixel. Needs the ``[ui]`` extra and
Pillow::

    QT_QPA_PLATFORM=offscreen python scripts/render_icons.py [installer-dir]
"""
from __future__ import annotations

import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

PNG_SIZES = (16, 32, 64, 128, 256, 512)
ICO_SIZES = (16, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def _pil_image(size: int):
    """The drawing for ``size`` px as a Pillow RGBA image, rendered by Qt."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from adichtion.ui.logo import make_pixmap

    pm = make_pixmap(size)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main(out: str) -> None:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841 - Qt needs one to paint
    os.makedirs(out, exist_ok=True)
    images = {s: _pil_image(s) for s in sorted(set(PNG_SIZES + ICO_SIZES + ICNS_SIZES))}

    images[1024].save(os.path.join(out, "adichtion.png"))
    for s in PNG_SIZES:
        images[s].save(os.path.join(out, f"adichtion-{s}.png"))

    # Pillow drops every size larger than the base image, so the base is the biggest one.
    ico = [images[s] for s in sorted(ICO_SIZES, reverse=True)]
    ico[0].save(os.path.join(out, "adichtion.ico"), format="ICO",
                sizes=[(s, s) for s in ICO_SIZES], append_images=ico[1:])
    icns = [images[s] for s in ICNS_SIZES]
    icns[-1].save(os.path.join(out, "adichtion.icns"), format="ICNS", append_images=icns[:-1])
    print("installer icons written to", out)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, "installer"))
