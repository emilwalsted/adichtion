# Releasing adichtion

How a version becomes the three installers (Windows MSI, macOS dmg, Ubuntu .deb) and a
GitHub release. The setup mirrors RespMech's; the tool is
[briefcase](https://briefcase.readthedocs.io/), configured in `pyproject.toml`
(`[tool.briefcase]`), driven by `.github/workflows/release.yml`.

**Rule:** a release tag and anything that publishes to a live site need Emil's explicit go
first (repo `CLAUDE.md`). A manual run of the workflow only produces artifacts and is fine
as a test.

## 1. Bump the version

The version lives in two places that must agree, plus the changelog:

1. `src/adichtion/__init__.py` — `__version__ = "X.Y.Z"` (what `adichtion --version` and
   the wheel report; hatchling reads it).
2. `pyproject.toml` — `[tool.briefcase] version = "X.Y.Z"` (briefcase cannot read a dynamic
   version, so this is a manual copy; the file names of the installers come from it).
3. `CHANGELOG.md` — move the `[Unreleased]` notes to a `## [X.Y.Z] – YYYY-MM-DD` section.
   The Linux build reads this file (a Debian package must carry a changelog), so a release
   without an entry fails there.

Commit the three together (`Release X.Y.Z`).

## 2. Check

- `pytest` green locally (unit, synthetic round-trips, headless viewer tests).
- The *Tests* workflow green on the release commit (three OSes, Python 3.10 and 3.12).
- With real recordings at hand: `ADICHTION_SAMPLES=… pytest tests/test_integration_samples.py`
  and `adichtion check FILE` on any new kind of file.

## 3. Build the installers

### In CI (the normal way)

*Actions ▸ Build installers ▸ Run workflow* builds all three and uploads them as workflow
artifacts (`adichtion-Windows-MSI`, `adichtion-macOS-dmg`, `adichtion-Linux-deb`); nothing is
published. Tick *Ad-hoc sign the dmg* for a quick test build that skips Developer ID signing
and notarisation.

Pushing a tag `vX.Y.Z` runs the same three jobs and then `publish-release`, which creates
(or updates) the GitHub release `adichtion vX.Y.Z` with the installers attached and a short
install table as the notes. Tag from the release commit:

```
git tag -a vX.Y.Z -m "adichtion X.Y.Z"
git push origin vX.Y.Z
```

The release is created non-draft, so this *is* the publication step: ask first.

### Locally

```
python -m venv .bcv && . .bcv/bin/activate
pip install "briefcase>=0.4.5"

briefcase create macOS   && briefcase build macOS   && briefcase package macOS --adhoc-sign -p dmg
briefcase create windows && briefcase build windows && briefcase package windows --adhoc-sign -p msi
briefcase create linux system --target ubuntu:noble && briefcase build linux system --target ubuntu:noble \
    && briefcase package linux system --target ubuntu:noble
```

Each platform builds on its own OS (macOS on a Mac, the MSI on Windows). The Linux `--target`
builds inside a Docker image of that distribution, so the `.deb` depends on Ubuntu 24.04's
own Python; without Docker, drop `--target` and the package targets the build host instead.
Output lands in `dist/`; `build/`, `dist/`, `.briefcase/` and `logs/` are gitignored.

The installed binary is `python -m adichtion`: check it with `adichtion --version`,
`adichtion info FILE` and a plain start (the app, maximised, with the open dialog).

## 4. Signing

Detail and the commands that set the secrets: [`SIGNING.md`](SIGNING.md).

- **macOS**: with the repository secrets `APPLE_DEV_ID_IDENTITY`, `APPLE_CERTIFICATE_P12_BASE64`,
  `APPLE_CERTIFICATE_PASSWORD`, `KEYCHAIN_PASSWORD`, `APPLE_ID`, `APPLE_TEAM_ID` and
  `APPLE_APP_SPECIFIC_PASSWORD` set, the dmg is Developer ID-signed and notarised
  (`notarytool`, stapled). Without them, or with *Ad-hoc sign* ticked, it is ad-hoc signed and
  the user opens it once with right-click ▸ *Open*. The release notes say which it is.
- **Windows**: the MSI is built unsigned. The Certum key is hardware-backed and cannot sign on
  an ephemeral runner; after the release, `scripts/sign-msi-certum.sh vX.Y.Z` signs the MSI on
  the Mac and replaces the release asset, as for RespMech.
- **Linux**: unsigned `.deb`, like RespMech's.

## 5. Icons

`installer/adichtion.{png,ico,icns}` and the sized `adichtion-{16..512}.png` (the Linux
build wants each size as its own file) are rendered from the app's own logo
(`src/adichtion/ui/logo.py`: the icon drawing, the bare "a", up to 64 px and the full logo
from 128 px). To regenerate after a logo change (needs the `[ui]` extra and Pillow):

```
QT_QPA_PLATFORM=offscreen python scripts/render_icons.py
```

The `.ico` and `.icns` are assembled from the render at each size, not by scaling the
1024 px logo down, so the small slots carry the icon drawing. The design renders in
`design/logo/` (the SVGs and the PNGs for the README) are regenerated with
`python design/logo/adichtion_logo.py`; a test fails when the checked-in SVGs no longer
match the module.

## Not done yet / known limits

- The dmg is built for the runner's architecture (Apple Silicon on `macos-latest`); there is
  no Intel build, as for RespMech.
- No PyPI release yet; `pip install` is from the repository.
