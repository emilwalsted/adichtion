"""The logo, the app icon and the splash (headless Qt)."""
import os

import pytest

from adichtion.ui import logo

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_svg_for_size_picks_the_icon_below_128_and_the_logo_above():
    icon, full = logo.build_icon_svg(64), logo.build_logo_svg(1024)
    assert '<line ' not in icon and logo.AMBER not in icon         # no grid, no band: the "a" alone
    assert '<line ' in full and logo.AMBER in full
    for px in (16, 32, 48, 64):
        assert logo.svg_for_size(px) == logo.build_icon_svg(px)
    for px in (128, 256, 512, 1024):
        assert logo.svg_for_size(px) == logo.build_logo_svg(px)


def test_design_files_match_the_module():
    """design/logo/*.svg are the module's output (python design/logo/adichtion_logo.py)."""
    with open(os.path.join(ROOT, "design", "logo", "adichtion-logo.svg"), encoding="utf-8") as fh:
        assert fh.read() == logo.build_logo_svg(1024)
    with open(os.path.join(ROOT, "design", "logo", "adichtion-icon.svg"), encoding="utf-8") as fh:
        assert fh.read() == logo.build_icon_svg(64)


def test_svgs_render_in_qt(qapp):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    for svg in (logo.build_icon_svg(64), logo.build_logo_svg(1024)):
        assert QSvgRenderer(QByteArray(svg.encode())).isValid()
    icon = logo.make_pixmap(32)
    assert icon.width() == 32 and icon.devicePixelRatio() == 1.0
    img = icon.toImage()
    assert img.pixelColor(0, 0).alpha() == 0                        # outside the rounded tile
    stem = int((logo.ICON_A["cx"] + logo.ICON_A["r"]) / logo.CANVAS * 32)
    assert img.pixelColor(stem, 16).lightness() < 80                # the stem of the "a" is ink
    assert img.pixelColor(int(logo.ICON_A["cx"] / logo.CANVAS * 32), 16).lightness() > 180   # inside the bowl: paper
    full = logo.logo_pixmap(256)
    assert full.width() == 256
    band_x = int(sum(logo.LOGO_BAND) / 2 / logo.CANVAS * 256)
    assert full.toImage().pixelColor(band_x, 40).hue() in range(25, 50)   # the amber selection band
    retina = logo.make_pixmap(64, dpr=2.0)
    assert retina.width() == 128 and retina.devicePixelRatio() == 2.0
    assert logo.AMBER in logo.svg_for_size(128)                     # 64 logical px on a 2x screen get the logo


def test_app_icon_carries_every_size(qapp):
    icon = logo.app_icon()
    assert icon is not None and not icon.isNull()
    assert [s.width() for s in icon.availableSizes()] == [16, 24, 32, 48, 64, 128, 256, 512]


def test_splash_svg_has_the_brand_and_the_version():
    from adichtion.ui import splash

    svg = splash.build_splash_svg(780, 460, version="9.9.9")
    assert ">adichtion<" in svg and "v9.9.9" in svg and "Emil Ingerslev Walsted" in svg
    assert splash.AMBER in svg and splash.INK in svg
    assert svg.count("<polyline") == 1                              # one ink trace


def test_make_splash_builds_on_the_paper(qapp):
    from PySide6.QtWidgets import QSplashScreen
    from adichtion.ui import splash

    s = splash.make_splash(qapp)
    assert isinstance(s, QSplashScreen)
    pm = s.pixmap()
    assert pm.width() == 780 * pm.devicePixelRatio() and pm.height() == 460 * pm.devicePixelRatio()
    assert "," not in splash._FONT                                  # resolved to one installed family
    img = pm.toImage()
    ratio = pm.devicePixelRatio()
    # the logo tile sits where the layout says (its top-left corner is outside the rounded tile)
    tile = img.pixelColor(int((splash.LOGO_X + splash.LOGO_S / 2) * ratio), int((splash.LOGO_Y + 8) * ratio))
    assert tile.lightness() > 150                                   # paper, not transparent black
    s.deleteLater()


def test_reveal_delay_tops_up_to_the_floor():
    from adichtion.ui.app import MIN_SPLASH_MS, reveal_delay_ms

    assert reveal_delay_ms(0) == MIN_SPLASH_MS
    assert reveal_delay_ms(MIN_SPLASH_MS - 100) == 100
    assert reveal_delay_ms(MIN_SPLASH_MS) == 0 and reveal_delay_ms(10 * MIN_SPLASH_MS) == 0


@pytest.mark.parametrize("name", ["adichtion.png", "adichtion.ico", "adichtion.icns",
                                  *[f"adichtion-{s}.png" for s in (16, 32, 64, 128, 256, 512)]])
def test_installer_icons_exist_and_are_the_drawing(name):
    """scripts/render_icons.py wrote every file briefcase needs; the sized PNGs carry the
    icon drawing up to 64 px (no amber) and the logo from 128 px (amber band present)."""
    PIL = pytest.importorskip("PIL.Image")
    path = os.path.join(ROOT, "installer", name)
    assert os.path.exists(path)
    if name.endswith(".png") and "-" in name:
        px = int(name.split("-")[1].split(".")[0])
        im = PIL.open(path).convert("RGBA")
        assert im.size == (px, px)
        band_x, band_y = int(sum(logo.LOGO_BAND) / 2 / logo.CANVAS * px), int(0.15 * px)
        r, g, b, a = im.getpixel((band_x, band_y))
        amber = r > 200 and 150 < g < 235 and b < 190
        assert amber == (px > logo.ICON_MAX_PX), (name, (r, g, b, a))
