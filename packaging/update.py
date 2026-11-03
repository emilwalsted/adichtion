"""Write the package-catalogue manifests for one released version of adichtion.

    python packaging/update.py 1.0.0              # rewrite everything from the release
    python packaging/update.py 1.0.0 --verify     # also download the installers and hash them
    python packaging/update.py 1.0.0 --validate   # then validate against the catalogues' schemas

After a release (the tag, the installers on the GitHub release, the wheel on PyPI) this one
call rewrites, under ``packaging/``:

* ``homebrew/Casks/adichtion.rb``: version and sha256 of the dmg;
* ``winget/manifests/e/EmilWalsted/adichtion/<version>/``: the three manifests, with the
  MSI's sha256, product code and upgrade code (any older version folder is removed);
* ``flatpak/python3-modules.json``: adichtion's wheel and the pinned Python dependencies
  (``flatpak/requirements.txt``) with their sha256 from PyPI, per architecture;
* ``flatpak/dk.emilwalsted.adichtion.yml``: the tag and commit the icons are taken from;
* ``flatpak/dk.emilwalsted.adichtion.metainfo.xml``: a ``<release>`` entry for the version.

Only the public REST endpoints of GitHub and PyPI are read (no token). The sha256 of a
release asset is GitHub's own ``digest``; ``--verify`` downloads each installer and checks
it. The MSI's product code is read with ``msiinfo`` (msitools) from the downloaded MSI, or
given with ``--product-code`` and ``--upgrade-code`` where msitools is missing (on Windows,
Orca shows the MSI's Property table; see packaging/README.md). Every file is written only
after all lookups have succeeded, so a failed run leaves packaging/ as it was.

Standard library only, except ``--validate`` (``jsonschema`` and ``PyYAML``).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import http.client
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
REPO = "emilwalsted/adichtion"
APP_ID = "dk.emilwalsted.adichtion"            # briefcase: bundle + app name (pyproject.toml)
WINGET_ID = "EmilWalsted.adichtion"
WINGET_SCHEMA = "1.12.0"
PUBLISHER = "Emil Ingerslev Walsted"
HOMEPAGE = "https://www.respmech.dk/adichtion.html"
FLATPAK_PYTHON = "313"                         # CPython of the KDE 6.11 runtime (freedesktop 25.08)
FLATPAK_ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64"}

DMG = "adichtion-{v}.dmg"
MSI = "adichtion-{v}.msi"

Fetch = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "adichtion-packaging",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


# -- release data ----------------------------------------------------------------------------

def release_info(version: str, fetch: Fetch = http_get, need_digest: bool = True) -> dict:
    """The GitHub release ``v<version>``: its date and, per asset, the URL and sha256 (GitHub's
    ``digest``; ``None`` where GitHub gives none, allowed only without ``need_digest``)."""
    rel = json.loads(fetch(f"https://api.github.com/repos/{REPO}/releases/tags/v{version}"))
    assets = {}
    for a in rel.get("assets", []):
        digest = a.get("digest") or ""
        sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
        assets[a["name"]] = {"url": a["browser_download_url"], "sha256": sha}
    for name in (DMG.format(v=version), MSI.format(v=version)):
        if name not in assets:
            raise SystemExit(f"release v{version} has no asset {name}")
        if need_digest and not assets[name]["sha256"]:
            raise SystemExit(f"GitHub gives no sha256 for {name}: run again with --verify")
    date = (rel.get("published_at") or rel.get("created_at") or "")[:10]
    return {"version": version, "date": date, "assets": assets}


def download(url: str, dest: Path) -> str:
    """Download ``url`` to ``dest`` and return its sha256."""
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "adichtion-packaging"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            fh.write(chunk)
    return h.hexdigest()


def checked_download(url: str, dest: Path, expected: Optional[str], what: str, tries: int = 3) -> str:
    """Download until the sha256 matches GitHub's and return it; a cut-off transfer gets another
    try. Without ``expected`` (GitHub gave no digest), two downloads must agree instead."""
    got, previous = "", None
    for _ in range(tries):
        try:
            got = download(url, dest)
        except (OSError, http.client.HTTPException) as exc:
            got = f"(failed: {exc})"
            continue
        if got == expected or (expected is None and got == previous):
            print(f"{what} sha256 checked: {got}")
            return got
        previous = got
    raise SystemExit(f"{what} sha256 {got} differs from GitHub's {expected} after {tries} tries")


def msi_codes(path: Path) -> dict:
    """ProductCode, UpgradeCode and Manufacturer from the MSI's Property table (msitools)."""
    if shutil.which("msiinfo") is None:
        raise SystemExit("msiinfo (msitools) not found: install it, or pass --product-code and --upgrade-code")
    out = subprocess.run(["msiinfo", "export", str(path), "Property"], check=True,
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return parse_msi_properties(out)


def parse_msi_properties(text: str) -> dict:
    props = {}
    for line in text.splitlines():
        key, _, value = line.partition("\t")
        if key in ("ProductCode", "UpgradeCode", "Manufacturer", "ProductVersion"):
            props[key] = value.strip()
    return props


def tag_commit(version: str, run=subprocess.run) -> str:
    """The commit the tag ``v<version>`` points at (annotated tags dereferenced)."""
    out = run(["git", "ls-remote", f"https://github.com/{REPO}.git",
               f"refs/tags/v{version}", f"refs/tags/v{version}^{{}}"],
              check=True, capture_output=True, text=True, encoding="utf-8").stdout
    refs = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref:
            refs[ref.strip()] = sha.strip()
    commit = refs.get(f"refs/tags/v{version}^{{}}") or refs.get(f"refs/tags/v{version}")
    if not commit:
        raise SystemExit(f"tag v{version} not found on GitHub")
    return commit


# -- PyPI wheels for the Flatpak ---------------------------------------------------------------

def read_pins(path: Path) -> list[tuple[str, str]]:
    """``name==version`` lines (comments and blank lines ignored)."""
    pins = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, ver = line.partition("==")
        if not sep or not name.strip() or not ver.strip():
            raise SystemExit(f"{path.name}: not a 'name==version' pin: {raw!r}")
        pins.append((name.strip(), ver.strip()))
    return pins


def _wheel_tags(filename: str) -> tuple[list[str], list[str], list[str]]:
    parts = filename[:-4].split("-")
    py, abi, plat = parts[-3], parts[-2], parts[-1]
    return py.split("."), abi.split("."), plat.split(".")


def wheel_fits(filename: str, arch: Optional[str]) -> bool:
    """Whether a wheel installs on the Flatpak's CPython on ``arch`` (None: pure Python)."""
    pys, abis, plats = _wheel_tags(filename)
    if plats == ["any"]:
        return arch is None and any(p in ("py3", "py2.py3") or p.startswith("py3") for p in pys) \
            and abis == ["none"]
    if arch is None:
        return False
    if not any(p.startswith("manylinux") and p.endswith("_" + arch) for p in plats):
        return False
    ok_py = any(p == f"cp{FLATPAK_PYTHON}" for p in pys) and f"cp{FLATPAK_PYTHON}" in abis
    ok_abi3 = "abi3" in abis and any(p.startswith("cp3") and int(p[3:] or 0) <= int(FLATPAK_PYTHON[1:])
                                     for p in pys)
    ok_none = abis == ["none"] and any(p.startswith("py3") for p in pys)
    return ok_py or ok_abi3 or ok_none


def _glibc(filename: str) -> tuple[int, int]:
    """The oldest glibc a manylinux wheel asks for (lower is more portable)."""
    best = (99, 99)
    for p in _wheel_tags(filename)[2]:
        m = re.match(r"manylinux_(\d+)_(\d+)_", p)
        legacy = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
        v = (int(m.group(1)), int(m.group(2))) if m else legacy.get(p.split("_")[0], best)
        best = min(best, v)
    return best


def pypi_wheels(name: str, version: str, fetch: Fetch = http_get) -> list[dict]:
    """The wheels the Flatpak needs for one pin: one pure wheel, or one per architecture."""
    data = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
    files = sorted((u for u in data["urls"] if u["packagetype"] == "bdist_wheel"), key=lambda u: u["filename"])
    # a pure wheel wins; fine for the pins, which ship either a pure wheel or compiled ones
    pure = [u for u in files if wheel_fits(u["filename"], None)]
    if pure:
        u = pure[0]
        return [{"type": "file", "url": u["url"], "sha256": u["digests"]["sha256"]}]
    chosen = []
    for arch in FLATPAK_ARCHES:
        fits = [u for u in files if wheel_fits(u["filename"], arch)]
        if not fits:
            raise SystemExit(f"{name}=={version} has no wheel for CPython 3.{FLATPAK_PYTHON[1:]} on {arch}")
        u = min(fits, key=lambda w: (_glibc(w["filename"]), w["filename"]))
        chosen.append({"type": "file", "url": u["url"], "sha256": u["digests"]["sha256"], "only-arches": [arch]})
    return chosen


def flatpak_modules(version: str, pins: list[tuple[str, str]], fetch: Fetch = http_get) -> dict:
    """The ``python3-modules.json`` module: every wheel, installed in one pip call."""
    reqs = [("adichtion", version)] + pins
    sources = []
    for name, ver in reqs:
        sources.extend(pypi_wheels(name, ver, fetch))
    spec = " ".join(f'"{n}=={v}"' for n, v in reqs)
    return {
        "name": "python3-modules",
        "buildsystem": "simple",
        "build-commands": [
            # replace the base app's own numpy cleanly (see requirements.txt)
            "pip3 uninstall -y numpy",
            "pip3 install --verbose --no-index --find-links=\"file://${PWD}\" --prefix=${FLATPAK_DEST} "
            f"--no-deps --no-build-isolation {spec}"
        ],
        "sources": sources,
    }


# -- renderers (pure: the tests call them) ------------------------------------------------------

def render_cask(version: str, sha256: str) -> str:
    return f'''cask "adichtion" do
  version "{version}"
  sha256 "{sha256}"

  url "https://github.com/{REPO}/releases/download/v#{{version}}/adichtion-#{{version}}.dmg"
  name "adichtion"
  desc "Viewer and exporter for LabChart .adicht recordings"
  homepage "{HOMEPAGE}"

  livecheck do
    url :url
    strategy :github_latest
  end

  # The dmg is built on Apple silicon only (briefcase, universal_build = false).
  depends_on arch: :arm64
  depends_on macos: ">= :monterey"

  app "adichtion.app"

  zap trash: [
    "~/Library/Preferences/com.adichtion.adichtion.plist",
    "~/Library/Saved Application State/{APP_ID}.savedState",
  ]
end
'''


def _yaml_header(kind: str) -> str:
    return f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{kind}.{WINGET_SCHEMA}.schema.json\n\n"


def render_winget(version: str, date: str, msi_url: str, sha256: str, product_code: str,
                  upgrade_code: str) -> dict[str, str]:
    """The three files of the winget manifest, by file name."""
    base = f"PackageIdentifier: {WINGET_ID}\nPackageVersion: {version}\n"
    files = {}
    files[f"{WINGET_ID}.yaml"] = (
        _yaml_header("version") + base
        + f"DefaultLocale: en-US\nManifestType: version\nManifestVersion: {WINGET_SCHEMA}\n")
    files[f"{WINGET_ID}.installer.yaml"] = (
        _yaml_header("installer") + base
        + "InstallerType: msi\n"
        + "UpgradeBehavior: install\n"
        + "FileExtensions:\n- adicht\n"
        + (f"ReleaseDate: {date}\n" if date else "")
        + "AppsAndFeaturesEntries:\n"
        + f"- UpgradeCode: '{upgrade_code}'\n"
        + "Installers:\n"
        + f"- Architecture: x64\n  InstallerUrl: {msi_url}\n  InstallerSha256: {sha256.upper()}\n"
        + f"  ProductCode: '{product_code}'\n"
        + f"ManifestType: installer\nManifestVersion: {WINGET_SCHEMA}\n")
    files[f"{WINGET_ID}.locale.en-US.yaml"] = (
        _yaml_header("defaultLocale") + base
        + "PackageLocale: en-US\n"
        + f"Publisher: {PUBLISHER}\n"
        + "PublisherUrl: https://github.com/emilwalsted\n"
        + f"PublisherSupportUrl: https://github.com/{REPO}/issues\n"
        + f"Author: {PUBLISHER}\n"
        + "PackageName: adichtion\n"
        + f"PackageUrl: {HOMEPAGE}\n"
        + "License: GPL-3.0-or-later\n"
        + f"LicenseUrl: https://github.com/{REPO}/blob/main/LICENSE\n"
        + "ShortDescription: View and export LabChart .adicht recordings without LabChart.\n"
        + "Description: |-\n"
        + "  adichtion opens LabChart .adicht files: every channel on one time axis with the blocks\n"
        + "  laid end to end, comments, the video recorded with a block, and the definitions of\n"
        + "  calculated channels recovered from the data. It exports the file, a block or a selection\n"
        + "  as LabChart-style text, CSV or Excel.\n"
        + "Moniker: adichtion\n"
        + "Tags:\n- adicht\n- physiology\n- recording\n- viewer\n- export\n- csv\n- excel\n"
        + f"ReleaseNotesUrl: https://github.com/{REPO}/releases/tag/v{version}\n"
        + f"ManifestType: defaultLocale\nManifestVersion: {WINGET_SCHEMA}\n")
    return files


def set_flatpak_tag(manifest: str, version: str, commit: str) -> str:
    """Point the manifest's git source at ``v<version>`` / ``commit``."""
    out, n1 = re.subn(r"(?m)^(\s*tag:\s*)v\S+$", rf"\g<1>v{version}", manifest)
    out, n2 = re.subn(r"(?m)^(\s*commit:\s*)[0-9a-f]{40}$", rf"\g<1>{commit}", out)
    if n1 != 1 or n2 != 1:
        raise SystemExit("the Flatpak manifest should have exactly one git source with tag: and commit:")
    return out


def add_metainfo_release(metainfo: str, version: str, date: str) -> str:
    """Add ``<release version= date=>`` at the top of ``<releases>`` unless it is there."""
    if re.search(rf'<release\s+version="{re.escape(version)}"', metainfo):
        return metainfo
    entry = (f'    <release version="{version}" date="{date}">\n'
             f'      <url type="details">https://github.com/{REPO}/releases/tag/v{version}</url>\n'
             f'    </release>\n')
    out, n = re.subn(r"(<releases>\n)", lambda m: m.group(1) + entry, metainfo, count=1)
    if n != 1:
        raise SystemExit("the metainfo has no <releases> element")
    return out


# -- validation ---------------------------------------------------------------------------------

def validate(fetch: Fetch = http_get) -> int:
    """Validate the manifests in packaging/ against the catalogues' schemas and tools."""
    import jsonschema
    import yaml

    failures = 0

    class WingetLoader(yaml.SafeLoader):
        """winget reads ``ReleaseDate: 2026-10-06`` as a string; PyYAML would make it a date."""

    WingetLoader.yaml_implicit_resolvers = {
        k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
        for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}

    def report(what: str, errors: list[str]) -> None:
        nonlocal failures
        if errors:
            failures += 1
            print(f"FAIL {what}")
            for e in errors:
                print(f"     {e}")
        else:
            print(f"ok   {what}")

    base = f"https://raw.githubusercontent.com/microsoft/winget-cli/master/schemas/JSON/manifests/v{WINGET_SCHEMA}"
    kinds = {".installer.yaml": "installer", ".locale.en-US.yaml": "defaultLocale", ".yaml": "version"}
    for folder in sorted((HERE / "winget").glob("manifests/*/*/*/*")):
        for f in sorted(folder.glob("*.yaml")):
            kind = next(k for suffix, k in kinds.items() if f.name.endswith(suffix))
            schema = json.loads(fetch(f"{base}/manifest.{kind}.{WINGET_SCHEMA}.json"))
            doc = yaml.load(f.read_text(encoding="utf-8"), Loader=WingetLoader)
            errs =[f"{'/'.join(map(str, e.path))}: {e.message}"
                    for e in jsonschema.Draft7Validator(schema).iter_errors(doc)]
            report(f"winget {f.relative_to(HERE)}", errs)

    schema = json.loads(fetch("https://json.schemastore.org/flatpak-manifest.json"))
    v = jsonschema.validators.validator_for(schema)(schema)
    for f in [HERE / "flatpak" / f"{APP_ID}.yml"]:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        report(f"flatpak {f.name}", [f"{'/'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(doc)])
        for mod in doc["modules"]:
            if isinstance(mod, str):
                sub = json.loads((f.parent / mod).read_text(encoding="utf-8"))
                module_schema = dict(schema, **{"$ref": "#/definitions/module"}) if "definitions" in schema \
                    and "module" in schema["definitions"] else None
                if module_schema is None:
                    print(f"skip {mod}: the schema has no module definition")
                else:
                    mv = jsonschema.validators.validator_for(module_schema)(module_schema)
                    report(f"flatpak {mod}", [f"{'/'.join(map(str, e.path))}: {e.message}"
                                              for e in mv.iter_errors(sub)])

    tools = [("appstreamcli", ["validate", "--no-net", "--pedantic"], f"{APP_ID}.metainfo.xml"),
             ("desktop-file-validate", [], f"{APP_ID}.desktop")]
    for tool, args, name in tools:
        if shutil.which(tool) is None:
            print(f"skip {name}: {tool} not installed")
            continue
        r = subprocess.run([tool, *args, str(HERE / "flatpak" / name)], capture_output=True, text=True)
        report(f"{tool} {name}", [] if r.returncode == 0 else (r.stdout + r.stderr).strip().splitlines())

    if shutil.which("ruby"):
        r = subprocess.run(["ruby", "-c", str(HERE / "homebrew" / "Casks" / "adichtion.rb")],
                           capture_output=True, text=True)
        report("ruby -c Casks/adichtion.rb", [] if r.returncode == 0 else r.stderr.strip().splitlines())
    else:
        print("skip Casks/adichtion.rb: ruby not installed (brew audit runs on the Mac)")
    return 1 if failures else 0


# -- main ---------------------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("version", nargs="?", help="the released version, e.g. 1.0.0")
    ap.add_argument("--verify", action="store_true", help="download the installers and check their sha256")
    ap.add_argument("--product-code", help="the MSI's ProductCode, where msitools is missing")
    ap.add_argument("--upgrade-code", help="the MSI's UpgradeCode, where msitools is missing")
    ap.add_argument("--validate", action="store_true", help="validate the manifests afterwards")
    ap.add_argument("--validate-only", action="store_true", help="only validate what is in packaging/")
    args = ap.parse_args(argv)

    if args.validate_only:
        return validate()
    if not args.version or not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        ap.error("give the released version as X.Y.Z")
    version = args.version
    rel = release_info(version, need_digest=not args.verify)
    dmg, msi = rel["assets"][DMG.format(v=version)], rel["assets"][MSI.format(v=version)]

    # 1. every lookup first: a failure here leaves packaging/ untouched
    codes = {"ProductCode": args.product_code, "UpgradeCode": args.upgrade_code}
    with tempfile.TemporaryDirectory() as tmp:
        if args.verify:
            dmg["sha256"] = checked_download(dmg["url"], Path(tmp) / "a.dmg", dmg["sha256"], "dmg")
            (Path(tmp) / "a.dmg").unlink()
        if args.verify or not (codes["ProductCode"] and codes["UpgradeCode"]):
            msi["sha256"] = checked_download(msi["url"], Path(tmp) / "a.msi", msi["sha256"], "msi")
        if not (codes["ProductCode"] and codes["UpgradeCode"]):
            read = msi_codes(Path(tmp) / "a.msi")
            if read.get("ProductVersion") and read["ProductVersion"].split(".")[:3] != version.split("."):
                raise SystemExit(f"the MSI says version {read['ProductVersion']}, not {version}")
            codes = {k: codes[k] or read.get(k) for k in codes}
    if not codes["ProductCode"] or not codes["UpgradeCode"]:
        raise SystemExit("could not determine the MSI's ProductCode and UpgradeCode")

    flat = HERE / "flatpak"
    manifest, meta = flat / f"{APP_ID}.yml", flat / f"{APP_ID}.metainfo.xml"
    writes: dict[Path, str] = {
        HERE / "homebrew" / "Casks" / "adichtion.rb": render_cask(version, dmg["sha256"]),
        flat / "python3-modules.json":
            json.dumps(flatpak_modules(version, read_pins(flat / "requirements.txt")), indent=4) + "\n",
        manifest: set_flatpak_tag(manifest.read_text(encoding="utf-8"), version, tag_commit(version)),
        meta: add_metainfo_release(meta.read_text(encoding="utf-8"), version,
                                   rel["date"] or _dt.date.today().isoformat()),
    }
    pkg = HERE / "winget" / "manifests" / "e" / "EmilWalsted" / "adichtion"
    for name, text in render_winget(version, rel["date"], msi["url"], msi["sha256"],
                                    codes["ProductCode"], codes["UpgradeCode"]).items():
        writes[pkg / version / name] = text

    # 2. then write (LF line endings on every platform)
    if pkg.exists():
        for old in pkg.iterdir():
            if old.is_dir() and old.name != version:
                shutil.rmtree(old)
    for path, text in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {path.relative_to(HERE).as_posix()}")

    return validate() if args.validate else 0


if __name__ == "__main__":
    sys.exit(main())
