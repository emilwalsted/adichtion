"""Is this the packaged adichtion.app? (``adichtion.ui.macbundle``: Qt-free, runs everywhere.)

The fake bundles carry the Info.plist keys briefcase 0.4.5's macOS template writes for this
app, and the ones python.org's framework Python.app and conda's python.app carry: both of
those put a from-source run inside an ``.app`` too, which is why the check compares the
bundle identifier instead of looking for ``.app/Contents/MacOS`` in the path.
"""
import os
import plistlib
import re

import pytest

from adichtion.ui import macbundle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OURS = {                                                # briefcase's Info.plist for this app
    "CFBundleExecutable": "adichtion", "CFBundleIconFile": "adichtion.icns",
    "CFBundleIdentifier": "dk.emilwalsted.adichtion", "CFBundleName": "adichtion",
    "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.3.0",
    "LSMinimumSystemVersion": "12.0", "NSPrincipalClass": "NSApplication",
    "MainModule": "adichtion",
}
FRAMEWORK_PYTHON = {                                    # python.org / Homebrew Python.app
    "CFBundleExecutable": "Python", "CFBundleIconFile": "PythonInterpreter.icns",
    "CFBundleIdentifier": "org.python.python", "CFBundlePackageType": "APPL",
}
CONDA_PYTHON = {                                        # conda's python.app
    "CFBundleExecutable": "python", "CFBundleIconFile": "",
    "CFBundleIdentifier": "com.continuum.python", "CFBundlePackageType": "APPL",
}


def make_bundle(root, name, info, exe, binary=False):
    """``root/name/Contents/{MacOS/exe, Info.plist}``; returns the executable's path."""
    macos = root / name / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / exe).write_bytes(b"\xcf\xfa\xed\xfe")
    with open(macos.parent / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)
    return macos / exe


@pytest.mark.parametrize("where", [
    "Applications",
    "Volumes/adichtion",                                # run straight from the mounted dmg
    "private/var/folders/xy/T/AppTranslocation/0A1B/d",  # Gatekeeper translocation
])
def test_the_packaged_app_is_recognised_wherever_it_runs(tmp_path, where):
    exe = make_bundle(tmp_path / where, "adichtion.app", OURS, "adichtion")
    assert macbundle.enclosing_app_bundle(exe) == tmp_path / where / "adichtion.app"
    assert macbundle.bundle_identifier(tmp_path / where / "adichtion.app") == macbundle.MACOS_BUNDLE_ID
    assert macbundle.is_packaged_app([str(exe)])
    assert macbundle.is_packaged_app([None, "/usr/bin/python3", exe])   # any candidate will do


def test_a_renamed_copy_with_a_binary_plist_is_still_the_app(tmp_path):
    exe = make_bundle(tmp_path, "adichtion 2.app", OURS, "adichtion", binary=True)
    assert macbundle.is_packaged_app([exe])


def test_framework_python_is_not_the_app(tmp_path):
    """From source under python.org/Homebrew Python, applicationFilePath() is inside
    Python.app: a substring test would call that 'packaged' and drop the Dock icon."""
    fw = tmp_path / "Library/Frameworks/Python.framework/Versions/3.12/Resources"
    exe = make_bundle(fw, "Python.app", FRAMEWORK_PYTHON, "Python")
    assert ".app/Contents/MacOS/" in exe.as_posix()                     # what a naive check would see
    assert macbundle.enclosing_app_bundle(exe) is not None
    assert not macbundle.is_packaged_app([exe, tmp_path / "proj/.venv/bin/python"])


def test_conda_python_app_is_not_the_app(tmp_path):
    exe = make_bundle(tmp_path / "opt/anaconda3", "python.app", CONDA_PYTHON, "python")
    assert not macbundle.is_packaged_app([exe, "/opt/anaconda3/bin/python"])


def test_only_the_main_executable_slot_counts(tmp_path):
    exe = make_bundle(tmp_path, "adichtion.app", OURS, "adichtion")
    contents = exe.parent.parent
    inner = contents / "Frameworks/Python.framework/Versions/3.12/bin/python3.12"
    inner.parent.mkdir(parents=True)
    inner.write_bytes(b"")
    assert not macbundle.is_packaged_app([inner])
    assert not macbundle.is_packaged_app([contents / "Resources/app/adichtion/__main__.py"])


@pytest.mark.parametrize("bad", [None, "", "adichtion", "adichtion.app/Contents/MacOS/adichtion",
                                 "/.app/Contents/MacOS/x", "/Applications/adichtion.app"])
def test_degenerate_paths_are_not_the_app(bad):
    assert not macbundle.is_packaged_app([bad])


def test_a_missing_or_garbled_info_plist_is_not_the_app_and_never_raises(tmp_path):
    exe = make_bundle(tmp_path, "adichtion.app", OURS, "adichtion")
    plist = exe.parent.parent / "Info.plist"
    plist.write_bytes(b"<plist><dict><key>CFBundleIdentifier")
    assert macbundle.bundle_identifier(tmp_path / "adichtion.app") is None
    assert not macbundle.is_packaged_app([exe])
    plist.unlink()
    assert not macbundle.is_packaged_app([exe])


def test_another_bundle_id_is_not_the_app(tmp_path):
    exe = make_bundle(tmp_path, "adichtion.app", dict(OURS, CFBundleIdentifier="com.example.other"),
                      "adichtion")
    assert not macbundle.is_packaged_app([exe])


def test_the_bundle_structure_is_matched_case_insensitively(tmp_path):
    exe = tmp_path / "Applications" / "ADICHTION.APP" / "contents" / "macos" / "adichtion"
    assert macbundle.enclosing_app_bundle(exe) == tmp_path / "Applications" / "ADICHTION.APP"
    assert macbundle.enclosing_app_bundle(tmp_path / "x.appx" / "Contents" / "MacOS" / "x") is None


def test_the_bundle_id_is_the_one_briefcase_writes():
    """MACOS_BUNDLE_ID is a copy of pyproject's [tool.briefcase] bundle + "." + app name,
    the CFBundleIdentifier briefcase puts in Info.plist; an app- or macOS-level `bundle`
    would override the global one, so the test merges them the way briefcase does."""
    tomllib = pytest.importorskip("tomllib")
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        cfg = tomllib.load(fh)["tool"]["briefcase"]
    (app_name, app_cfg), = cfg["app"].items()
    bundle = app_cfg.get("macOS", {}).get("bundle") or app_cfg.get("bundle") or cfg["bundle"]
    assert f"{bundle}.{app_name}" == macbundle.MACOS_BUNDLE_ID
    assert re.fullmatch(r"[A-Za-z0-9.-]+", macbundle.MACOS_BUNDLE_ID)
