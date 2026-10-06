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
- **The video clip, in each installer you built** (first release with `imageio-ffmpeg`, `docs/beslutninger.md`
  2026-10-05 (4)): install it, open a recording with video, export an interval with *Also export the video clip* in
  both kinds (original and H.264), and check that `adichtion.videoclip.find_ffmpeg()` points INSIDE the app (on a
  developer machine imageio-ffmpeg quietly falls back to an `ffmpeg` on the PATH, which hides a packaging fault).
  Also: the bundled ffmpeg binary is signed and notarised in the macOS dmg, keeps its executable bit in the `.deb`,
  starts from the MSI install, and the installers' size growth (about 80 MB per platform) is acceptable.
  The ffmpeg build is GPL (libx264): the release notes link to the source of the ffmpeg that imageio-ffmpeg bundles.

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

**A rebuilt app can still show the OLD icon — this is Briefcase, not a bug in this repo**
(found 28-09-2026, after the icon changed from an accent-blue square
to the a-trace drawing: `tests/ui/test_logo.py::test_installer_icons_exist_and_are_the_drawing`
was, and stayed, green throughout, and confirmed the committed `installer/` files always
matched the current drawing pixel for pixel). Briefcase only copies `installer/adichtion.*`
into the app bundle's resources when the bundle is scaffolded (`briefcase create`); a later
`briefcase build`/`briefcase run` on an EXISTING `build/adichtion/...` tree reuses whatever
icon was copied in at `create` time and does not notice that the source files changed. After
regenerating the icons, either:

```
rm -rf build/adichtion/macos   # (or windows / linux) — forces a fresh briefcase create
briefcase create macOS && briefcase build macOS && briefcase package macOS --adhoc-sign -p dmg
```

or keep the existing scaffold and explicitly refresh resources:

```
briefcase update macOS --update-resources && briefcase build macOS
```

(`--update-resources` also works passed directly to `build`/`run`.) macOS additionally
caches app icons at the OS level (Finder/Dock/LaunchServices); briefcase (0.4.5, this
repo's own floor) already carries the fix for that
(<https://github.com/beeware/briefcase/pull/1766>): an `--update-resources` run touches
the `.app` bundle to bust the cache. If the icon still looks stale after a
genuinely fresh `create`, clear it by hand: `killall Finder && killall Dock`, or as a last
resort `rm -rf ~/Library/Caches/com.apple.iconservices.store`. CI's *Build installers*
workflow always runs `briefcase create` from a clean checkout, so it is not exposed to this;
it only bites a local, iterative build.

**Re-running an old *Build installers* run rebuilds its OLD commit.** GitHub's *Re-run jobs*
checks out the commit the run was first started on, not the current `main`, so a re-run
finishes last and looks newest in the Actions list while carrying old code and old icons
(28-09-2026: a re-run of a run from the day before still had the accent-blue icon). Take
the dmg from a run whose commit is the one you want, or start a new run.

**The running app's Dock and app-switcher icon is the bundle's `adichtion.icns`.** In the
packaged macOS app `ui/app.py` sets no application icon (`application_icon_choice`,
`ui/macbundle.py` recognises the bundle by its `CFBundleIdentifier`). The dmg ships PySide6
6.9.3 (`min_os_version = "12.0"` excludes the macosx_13_0 wheels of 6.10 and later), and
its `QApplication.setWindowIcon` handed the Dock an image sized by the icon's first, 16 px
bare "a": a blurry "a" while the app ran, although Finder showed the right icon. From
source on macOS the app sets `logo.dock_icon()` (the full logo only). Check on a Mac after
an icon change: the pinned icon, the running Dock tile and Cmd-Tab all show the logo.

## 6. PyPI

`.github/workflows/publish-pypi.yml` (same shape as RespMech's) publishes the wheel and the sdist to PyPI when a
`v*` tag is pushed, in the same push that starts *Build installers*. There is no token: PyPI Trusted Publishing
(OIDC). The jobs: *Test gate* (`pytest -q` on Ubuntu, Python 3.12, Qt libraries installed), *Build* (`python -m
build`, built version must equal the tag, `CHANGELOG.md` must hold a `## [X.Y.Z]` entry, `twine check`, and the
wheel is installed in an empty venv where `adichtion --version` runs and the bare `adichtion` gives the plain
"install adichtion[ui]" message), then *Publish to PyPI* (environment `pypi`).

**One-time setup by Emil, before the first tag**:

1. pypi.org ▸ Account ▸ Publishing ▸ *Add a pending publisher*: project `adichtion`, owner `emilwalsted`,
   repository `adichtion`, workflow `publish-pypi.yml`, environment `pypi`.
2. The same on test.pypi.org with environment `testpypi` (for the dry run).
3. GitHub ▸ Settings ▸ Environments: create `pypi` and `testpypi` (a required reviewer on `pypi` turns the
   publish into a second go/no-go, as for RespMech).
4. Dry run: Actions ▸ *Publish to PyPI* ▸ Run workflow (publishes the version of the ref it is started on to TestPyPI;
   `skip-existing` makes a repeat harmless). Check `pip install -i https://test.pypi.org/simple/ adichtion`.

A PyPI version is immutable: a broken release is fixed with a patch version and a new tag, never by re-pushing the
tag. Check that the name is free right before the first publish (`pip index versions adichtion` said "no matching
distribution" on 2026-10-05; that does not prove nobody else has registered it).

What goes in the package: the wheel holds `src/adichtion` only (no `CLAUDE.md` files). The sdist also holds the tests,
the docs, the logo sources and the installer icons (the tests read them), but not `.claude/`, `.github/` or any
`CLAUDE.md`. The PyPI page is the README: PyPI never resolves relative links or images, so its relative links and
the logo image stay broken on PyPI until the README is given absolute URLs (github.com/.../blob/main/...,
raw.githubusercontent.com/...).

Things to know before the first tag: bump first (section 1), or the *Build* job fails by design (built version must
equal the tag, and `CHANGELOG.md` needs the `## [X.Y.Z]` entry). The build also refuses a tag whose commit is not on
`main`. *Build installers* (`release.yml`) runs in parallel and does not wait for the PyPI gate, so one tag can give a
GitHub release without a PyPI release or the reverse; check both runs. The gate tests Python 3.12 on Ubuntu only: check
the *Tests* workflow (3.10, three OSes) on the release commit. The dry run publishes whatever ref it is started on.

## Not done yet / known limits

- The dmg is built for the runner's architecture (Apple Silicon on `macos-latest`); there is
  no Intel build, as for RespMech.
- PyPI publishing is set up (section 6) but needs the one-time trusted-publisher registration on PyPI before the
  first tag; until then `pip install` is from the repository.
