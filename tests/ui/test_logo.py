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


def _is_logo(img):
    """The full logo carries the amber selection band; the bare "a" icon drawing has none."""
    band_x = int(sum(logo.LOGO_BAND) / 2 / logo.CANVAS * img.width())
    c = img.pixelColor(band_x, int(0.15 * img.height()))
    return c.alpha() > 200 and c.red() > 200 and 150 < c.green() < 235 and c.blue() < 190


def test_dock_icon_is_the_full_logo_at_every_size(qapp):
    """macOS from source: whatever size Qt asks for (the dock tile times the pixel ratio,
    or the first representation on Qt 6.9.1-6.10.0), the Dock gets the logo, never the
    bare "a" blown up (docs/beslutninger.md 2026-09-28 (5))."""
    from PySide6.QtCore import QSize

    icon = logo.dock_icon()
    assert icon is not None and not icon.isNull()
    sizes = [s.width() for s in icon.availableSizes()]
    assert sizes == list(logo.DOCK_ICON_SIZES) and min(sizes) > logo.ICON_MAX_PX
    for s in icon.availableSizes():                          # every representation Qt 6.9 builds
        assert _is_logo(icon.pixmap(s).toImage()), s
    for n in (16, 32, 48, 64, 128):                         # and every size Qt 6.10+ may ask for
        for dpr in (1.0, 2.0):
            assert _is_logo(icon.pixmap(QSize(n, n), dpr).toImage()), (n, dpr)


def test_the_packaged_mac_app_sets_no_application_icon():
    """The bundle's icns is the Dock and app-switcher icon; a run-time one replaced it with
    a blurry bare "a" (Qt 6.9.3). From source on macOS the logo-only dock icon, elsewhere
    the full multi-size icon."""
    from adichtion.ui.app import application_icon_choice

    assert application_icon_choice("darwin", packaged=True) is None
    assert application_icon_choice("darwin", packaged=False) == "dock"
    for platform in ("win32", "linux"):
        assert application_icon_choice(platform, packaged=False) == "app"
        assert application_icon_choice(platform, packaged=True) == "app"


@pytest.mark.parametrize("platform, packaged, sizes", [
    ("darwin", True, None),                                  # the bundle's icns stays
    ("darwin", False, list(logo.DOCK_ICON_SIZES)),          # from source: the logo only
    ("linux", False, [16, 24, 32, 48, 64, 128, 256, 512]),  # as before
    ("win32", True, [16, 24, 32, 48, 64, 128, 256, 512]),
])
def test_main_sets_the_chosen_application_icon(qapp, monkeypatch, platform, packaged, sizes):
    """What main() hands QApplication.setWindowIcon (via _set_application_icon)."""
    from adichtion.ui import app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", platform)
    monkeypatch.setattr(app_mod, "_running_as_packaged_mac_app", lambda app: packaged)
    calls = []

    class FakeApp:
        def setWindowIcon(self, icon):
            calls.append(icon)

    app_mod._set_application_icon(FakeApp())
    if sizes is None:
        assert calls == []
    else:
        assert len(calls) == 1 and [s.width() for s in calls[0].availableSizes()] == sizes


def test_packaged_mac_detection_asks_macbundle_only_on_macos(monkeypatch):
    """_running_as_packaged_mac_app asks macbundle only on macOS, passes the bundle AppKit
    uses first, and never lets a failure there stop start-up."""
    from adichtion.ui import app as app_mod
    import adichtion.ui.macbundle as mb

    class FakeApp:
        def applicationFilePath(self):
            return "/Applications/adichtion.app/Contents/MacOS/adichtion"

    seen = []
    monkeypatch.setattr(mb, "is_packaged_app", lambda exes: seen.append(tuple(exes)) or True)
    monkeypatch.setattr(app_mod.sys, "platform", "linux")
    assert app_mod._running_as_packaged_mac_app(FakeApp()) is False and seen == []
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    assert app_mod._running_as_packaged_mac_app(FakeApp()) is True
    assert seen[0][0] == FakeApp().applicationFilePath()

    def boom(exes):
        raise RuntimeError("no plist")
    monkeypatch.setattr(mb, "is_packaged_app", boom)
    assert app_mod._running_as_packaged_mac_app(FakeApp()) is False


def _icns_chunks(path):
    """{type: body bytes} for every chunk of an .icns file."""
    import struct

    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    chunks, i = {}, 8
    while i < len(data):
        kind, n = data[i:i + 4].decode("latin-1"), struct.unpack(">I", data[i + 4:i + 8])[0]
        chunks[kind] = data[i + 8:i + n]
        i += n
    return chunks


def test_installer_icns_holds_the_logo_from_128_px(qapp):
    """Every image in installer/adichtion.icns is the drawing its pixel size calls for:
    the bare "a" at 32 and 64 px (16 and 32 pt @2x), the logo from 128 px. The icns is
    what the Dock and the app switcher show for the packaged app."""
    from PySide6.QtGui import QImage

    expected = {"ic11": 32, "ic12": 64, "ic07": 128, "ic08": 256, "ic13": 256,
                "ic09": 512, "ic14": 512, "ic10": 1024}
    chunks = _icns_chunks(os.path.join(ROOT, "installer", "adichtion.icns"))
    assert set(chunks) - {"TOC "} == set(expected)          # no other image chunk, PNG or legacy
    for kind, px in expected.items():
        assert chunks[kind][:8] == b"\x89PNG\r\n\x1a\n", kind
        img = QImage.fromData(chunks[kind])
        assert (img.width(), img.height()) == (px, px), kind
        assert _is_logo(img) == (px > logo.ICON_MAX_PX), (kind, px)


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
