"""packaging/: the catalogue manifests and the script that rewrites them for a release.

The script's network side (GitHub, PyPI, msitools) is not run here; its pure parts are, with
made-up release data, and the committed manifests are checked to agree with each other and with
pyproject.toml.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"


@pytest.fixture(scope="module")
def upd():
    spec = importlib.util.spec_from_file_location("adichtion_packaging_update", PKG / "update.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SHA_A = "a" * 64
SHA_B = "b" * 64


def test_cask_carries_version_and_checksum(upd):
    text = upd.render_cask("2.3.4", SHA_A)
    assert 'version "2.3.4"' in text
    assert f'sha256 "{SHA_A}"' in text
    assert "adichtion-#{version}.dmg" in text and "/download/v#{version}/" in text
    assert "depends_on arch: :arm64" in text           # the dmg is built for Apple silicon only
    assert 'app "adichtion.app"' in text
    assert "strategy :github_latest" in text


def test_winget_files_agree_with_each_other(upd):
    files = upd.render_winget("2.3.4", "2027-01-02", "https://example.invalid/a.msi", SHA_B,
                              "{11111111-2222-3333-4444-555555555555}", "{66666666-7777-8888-9999-000000000000}")
    assert set(files) == {"EmilWalsted.adichtion.yaml", "EmilWalsted.adichtion.installer.yaml",
                          "EmilWalsted.adichtion.locale.en-US.yaml"}
    for text in files.values():
        assert "PackageIdentifier: EmilWalsted.adichtion\n" in text
        assert "PackageVersion: 2.3.4\n" in text
        assert f"ManifestVersion: {upd.WINGET_SCHEMA}\n" in text
    inst = files["EmilWalsted.adichtion.installer.yaml"]
    assert f"InstallerSha256: {SHA_B.upper()}" in inst          # winget writes the hash in upper case
    assert inst.count("ProductCode: '{11111111-2222-3333-4444-555555555555}'") == 2
    assert "ReleaseDate: 2027-01-02" in inst
    assert "License: GPL-3.0-or-later" in files["EmilWalsted.adichtion.locale.en-US.yaml"]
    assert "/releases/tag/v2.3.4" in files["EmilWalsted.adichtion.locale.en-US.yaml"]


def test_winget_yaml_parses(upd):
    yaml = pytest.importorskip("yaml")
    files = upd.render_winget("2.3.4", "2027-01-02", "https://example.invalid/a.msi", SHA_B, "{X}", "{Y}")
    inst = yaml.safe_load(files["EmilWalsted.adichtion.installer.yaml"])
    assert inst["Installers"][0]["Architecture"] == "x64"
    assert inst["AppsAndFeaturesEntries"][0]["UpgradeCode"] == "{Y}"
    loc = yaml.safe_load(files["EmilWalsted.adichtion.locale.en-US.yaml"])
    assert loc["Description"].startswith("adichtion opens LabChart .adicht files")
    assert all(t == t.lower() and " " not in t for t in loc["Tags"])


@pytest.mark.parametrize("name, arch, fits", [
    ("pyqtgraph-0.14.0-py3-none-any.whl", None, True),
    ("openpyxl-3.1.5-py2.py3-none-any.whl", None, True),
    ("pyqtgraph-0.14.0-py3-none-any.whl", "x86_64", False),
    ("numpy-2.2.6-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "x86_64", True),
    ("numpy-2.2.6-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "aarch64", False),
    ("numpy-2.2.6-cp313-cp313t-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "x86_64", False),
    ("numpy-2.2.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "x86_64", False),
    ("numpy-2.2.6-cp313-cp313-musllinux_1_2_x86_64.whl", "x86_64", False),
    ("opencv_python_headless-4.12.0.88-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", "aarch64", True),
    ("opencv_python_headless-4.12.0.88-cp37-abi3-win_amd64.whl", "x86_64", False),
    ("imageio_ffmpeg-0.8.1-py3-none-manylinux_2_28_x86_64.whl", "x86_64", True),
    ("imageio_ffmpeg-0.8.1-py3-none-macosx_11_0_arm64.whl", "aarch64", False),
])
def test_wheel_selection(upd, name, arch, fits):
    assert upd.wheel_fits(name, arch) is fits


def _pypi(files):
    return json.dumps({"urls": [{"filename": f, "packagetype": "bdist_wheel", "url": f"https://files.invalid/{f}",
                                 "digests": {"sha256": str(i) * 64}} for i, f in enumerate(files)]}).encode()


def test_flatpak_module_one_wheel_per_arch(upd):
    index = {
        "adichtion": ["adichtion-2.3.4-py3-none-any.whl"],
        "numpy": ["numpy-2.2.6-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                  "numpy-2.2.6-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
                  "numpy-2.2.6-cp313-cp313-win_amd64.whl"],
    }

    def fetch(url):
        name = re.search(r"/pypi/([^/]+)/", url).group(1)
        return _pypi(index[name])

    mod = upd.flatpak_modules("2.3.4", [("numpy", "2.2.6")], fetch)
    srcs = mod["sources"]
    assert [s["url"].rsplit("/", 1)[1] for s in srcs] == index["adichtion"] + index["numpy"][:2]
    assert "only-arches" not in srcs[0]
    assert [s["only-arches"] for s in srcs[1:]] == [["x86_64"], ["aarch64"]]
    assert '"adichtion==2.3.4" "numpy==2.2.6"' in mod["build-commands"][0]
    assert "--no-index" in mod["build-commands"][0] and "--no-deps" in mod["build-commands"][0]


def test_flatpak_module_refuses_a_missing_architecture(upd):
    fetch = lambda url: _pypi(["numpy-2.2.6-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"])
    with pytest.raises(SystemExit, match="aarch64"):
        upd.pypi_wheels("numpy", "2.2.6", fetch)


def test_read_pins_rejects_ranges(upd, tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("# c\n\nnumpy==2.2.6  # why\n", encoding="utf-8")
    assert upd.read_pins(p) == [("numpy", "2.2.6")]
    p.write_text("numpy>=2\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        upd.read_pins(p)


def test_release_info_takes_githubs_digest(upd):
    rel = {"published_at": "2027-01-02T03:04:05Z", "assets": [
        {"name": "adichtion-2.3.4.dmg", "browser_download_url": "u1", "digest": f"sha256:{SHA_A}"},
        {"name": "adichtion-2.3.4.msi", "browser_download_url": "u2", "digest": f"sha256:{SHA_B}"}]}
    info = upd.release_info("2.3.4", lambda url: json.dumps(rel).encode())
    assert info["date"] == "2027-01-02"
    assert info["assets"]["adichtion-2.3.4.msi"] == {"url": "u2", "sha256": SHA_B}
    rel["assets"].pop()
    with pytest.raises(SystemExit, match="msi"):
        upd.release_info("2.3.4", lambda url: json.dumps(rel).encode())


def test_msi_properties(upd):
    text = ("Property\tValue\ns72\tl0\nProperty\tProperty\nALLUSERS\t2\n"
            "ProductCode\t{A}\nUpgradeCode\t{B}\nProductVersion\t2.3.4\nManufacturer\tX Y\n")
    assert upd.parse_msi_properties(text) == {"ProductCode": "{A}", "UpgradeCode": "{B}",
                                              "ProductVersion": "2.3.4", "Manufacturer": "X Y"}


def test_flatpak_tag_and_metainfo_release(upd):
    manifest = (PKG / "flatpak" / f"{upd.APP_ID}.yml").read_text(encoding="utf-8")
    out = upd.set_flatpak_tag(manifest, "2.3.4", "c" * 40)
    assert "tag: v2.3.4\n" in out and f"commit: {'c' * 40}\n" in out
    meta = (PKG / "flatpak" / f"{upd.APP_ID}.metainfo.xml").read_text(encoding="utf-8")
    new = upd.add_metainfo_release(meta, "2.3.4", "2027-01-02")
    assert new.index('version="2.3.4"') < new.index('version="1.0.0"')     # newest first
    assert upd.add_metainfo_release(new, "2.3.4", "2027-01-02") == new     # only once


# -- the committed manifests --------------------------------------------------------------------

def _committed_version():
    cask = (PKG / "homebrew" / "Casks" / "adichtion.rb").read_text(encoding="utf-8")
    return re.search(r'version "([^"]+)"', cask).group(1)


def test_committed_manifests_describe_one_version(upd):
    v = _committed_version()
    folders = [p.name for p in (PKG / "winget" / "manifests" / "e" / "EmilWalsted" / "adichtion").iterdir()]
    assert folders == [v]
    manifest = (PKG / "flatpak" / f"{upd.APP_ID}.yml").read_text(encoding="utf-8")
    assert f"tag: v{v}\n" in manifest
    meta = (PKG / "flatpak" / f"{upd.APP_ID}.metainfo.xml").read_text(encoding="utf-8")
    assert re.search(r'<release version="([^"]+)"', meta).group(1) == v
    modules = json.loads((PKG / "flatpak" / "python3-modules.json").read_text(encoding="utf-8"))
    assert f'"adichtion=={v}"' in modules["build-commands"][0]
    assert any(f"/adichtion-{v}-py3-none-any.whl" in s["url"] for s in modules["sources"])


def test_flatpak_pins_satisfy_the_ui_extra(upd):
    specifiers = pytest.importorskip("packaging.specifiers")
    requirements = pytest.importorskip("packaging.requirements")
    tomllib = pytest.importorskip("tomllib")                # Python 3.11+
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = {}
    for line in project["dependencies"] + project["optional-dependencies"]["ui"]:
        r = requirements.Requirement(line)
        wanted[r.name.lower()] = r.specifier
    pins = dict((n.lower(), v) for n, v in upd.read_pins(PKG / "flatpak" / "requirements.txt"))
    for name, spec in wanted.items():
        if name == "pyside6":
            continue                                   # comes from io.qt.PySide.BaseApp
        assert name in pins, f"{name} is not pinned for the Flatpak"
        assert spec.contains(pins[name]), f"{name}=={pins[name]} does not satisfy {spec}"
    command = json.loads((PKG / "flatpak" / "python3-modules.json").read_text(encoding="utf-8"))["build-commands"][0]
    for name, ver in pins.items():
        assert f'"{name}=={ver}"' in command


def test_desktop_metainfo_and_mime_agree(upd):
    desktop = (PKG / "flatpak" / f"{upd.APP_ID}.desktop").read_text(encoding="utf-8")
    meta = (PKG / "flatpak" / f"{upd.APP_ID}.metainfo.xml").read_text(encoding="utf-8")
    mime = (PKG / "flatpak" / f"{upd.APP_ID}.mime.xml").read_text(encoding="utf-8")
    assert f"Icon={upd.APP_ID}\n" in desktop
    assert "MimeType=application/x-adicht;" in desktop
    assert f"<id>{upd.APP_ID}</id>" in meta and f"{upd.APP_ID}.desktop</launchable>" in meta
    assert '<mime-type type="application/x-adicht">' in mime and 'pattern="*.adicht"' in mime
    for img in re.findall(r"<image>https://raw\.githubusercontent\.com/emilwalsted/adichtion/main/(.+?)</image>", meta):
        assert (ROOT / img).is_file(), img
