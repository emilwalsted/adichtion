"""Is this process the packaged ``adichtion.app`` on macOS? (Qt-free, no PyObjC.)

The packaged app must not set a runtime application icon: the bundle's ``adichtion.icns``
is the Dock and app-switcher icon, and ``QApplication.setWindowIcon`` would replace it
with an image built by Qt (see ``app.application_icon_choice`` and docs/beslutninger.md
2026-09-28 (4)). Running from source there is no such icon, so the app sets its own.

The test is structural and exact: a candidate path counts only when it is the main
executable of a bundle, ``<X>.app/Contents/MacOS/<file>``, whose ``Contents/Info.plist``
carries our own ``CFBundleIdentifier``. A substring test for ``.app/Contents/MacOS`` is
not enough: framework Pythons (python.org, Homebrew) run as
``Python.framework/.../Resources/Python.app/Contents/MacOS/Python``, so from source
``QCoreApplication.applicationFilePath()`` also lies inside an ``.app``.
"""
from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Iterable, Optional, Union

PathLike = Union[str, "os.PathLike[str]"]

#: The ``CFBundleIdentifier`` briefcase writes into the app's Info.plist:
#: ``[tool.briefcase] bundle`` + "." + the app table's name (a test keeps them equal).
MACOS_BUNDLE_ID = "dk.emilwalsted.adichtion"


def enclosing_app_bundle(executable: Optional[PathLike]) -> Optional[Path]:
    """The ``*.app`` whose ``Contents/MacOS`` directly holds ``executable``, else None.

    Only the main-executable slot counts (no open-ended walk up), so a Python nested
    elsewhere inside a bundle (``Contents/Frameworks/.../bin/python3``) never does. The
    names compare case-insensitively: APFS usually is, and a typed path keeps its case."""
    if not executable:
        return None
    p = Path(os.fspath(executable))
    if not p.is_absolute():
        return None
    macos = p.parent
    contents = macos.parent
    bundle = contents.parent
    if (macos.name.lower() == "macos" and contents.name.lower() == "contents"
            and bundle.suffix.lower() == ".app" and len(bundle.name) > len(".app")):
        return bundle
    return None


def bundle_identifier(bundle: PathLike) -> Optional[str]:
    """``CFBundleIdentifier`` from ``<bundle>/Contents/Info.plist`` (XML or binary), or
    None when the file is missing or unreadable; never raises."""
    try:
        with open(Path(bundle) / "Contents" / "Info.plist", "rb") as fh:
            info = plistlib.load(fh)
    except Exception:                                   # OSError, InvalidFileException, ExpatError, ...
        return None
    ident = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    return ident if isinstance(ident, str) else None


def is_packaged_app(executables: Iterable[Optional[PathLike]],
                    bundle_id: str = MACOS_BUNDLE_ID) -> bool:
    """True when any candidate is the main executable of a bundle whose Info.plist carries
    ``bundle_id``, i.e. we run as adichtion.app (under any file name or location: the
    dmg, /Applications, a Gatekeeper-translocated copy). Pass
    ``QCoreApplication.applicationFilePath()`` (the bundle AppKit uses) and ``sys.executable``."""
    for exe in executables:
        bundle = enclosing_app_bundle(exe)
        if bundle is not None and bundle_identifier(bundle) == bundle_id:
            return True
    return False
