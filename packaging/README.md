# Package catalogues

Manifests that put the released installers into three package catalogues, so a user can
install and update adichtion with the system's own tool:

| Catalogue | Platform | Install command, once accepted | What it installs |
|---|---|---|---|
| Homebrew (cask) | macOS, Apple silicon | `brew install --cask emilwalsted/tap/adichtion` | the release's dmg |
| winget | Windows 10/11, x64 | `winget install EmilWalsted.adichtion` | the release's MSI |
| Flathub | Linux, x86_64 and aarch64 | `flatpak install flathub dk.emilwalsted.adichtion` | built from PyPI wheels |

Nothing here is published by this repository. Each catalogue is a separate repository that
takes a pull request; the steps below are done by the maintainer, with his own accounts.

**Before submitting anywhere, read the catalogue's policy on AI-assisted contributions.** These
files were drafted with an AI coding assistant. Flathub's requirements say that a submission's
manifest must not contain AI-generated or AI-assisted content, and that AI tools must not open
or write the pull request: for Flathub the maintainer writes the manifest himself (the files
here are then a reference, not something to copy). Homebrew's pull request template asks for
AI use to be disclosed. Check winget's current contribution rules the same way.

```
packaging/
  update.py                       rewrites everything below for a released version
  homebrew/Casks/adichtion.rb     the cask
  winget/manifests/e/EmilWalsted/adichtion/<version>/   the three winget manifests
  flatpak/
    dk.emilwalsted.adichtion.yml            Flatpak manifest
    python3-modules.json                    the Python wheels, with sha256 (generated)
    requirements.txt                        pinned versions of those wheels
    dk.emilwalsted.adichtion.desktop        desktop entry
    dk.emilwalsted.adichtion.metainfo.xml   AppStream metadata (the store page)
    dk.emilwalsted.adichtion.mime.xml       the .adicht file type
    screenshots.py, screenshots/            store screenshots, from a synthetic recording
```

## After a release

When the GitHub release `vX.Y.Z` has its installers (and the Windows MSI is signed, see
`docs/SIGNING.md`: signing replaces the asset and changes its sha256) and the wheel is on
PyPI:

```
pip install jsonschema pyyaml        # for --validate
python packaging/update.py X.Y.Z --verify --validate
```

It reads the release from GitHub's REST API (no token), downloads the installers to check
their sha256, reads the MSI's product and upgrade code with `msiinfo` (msitools: `brew
install msitools`, `apt install msitools`), looks up the Flatpak's wheels on PyPI, writes the
files above, and validates them:

- the winget files against winget's JSON schemas (`microsoft/winget-cli`, `schemas/JSON/manifests/v1.12.0`);
- the Flatpak manifest and `python3-modules.json` against the schema on schemastore.org;
- the metainfo with `appstreamcli validate --pedantic` and the desktop entry with
  `desktop-file-validate`, where installed (`brew install appstream desktop-file-utils`);
- the cask with `ruby -c` (syntax only; `brew audit` below is the real check).

Without msitools, pass the codes by hand: `--product-code "{…}" --upgrade-code "{…}"` (on
Windows, Orca shows them in the Property table); `--verify` then still checks the hashes. The
upgrade code stays the same from release to release; the product code changes with every
build. `--validate-only` validates what is committed without touching it. Every file is
written only after all lookups have succeeded.

Commit the result, then send each catalogue its update as described below.

## Homebrew

`adichtion.rb` installs `adichtion.app` from the dmg. The dmg is built on Apple silicon only
(`universal_build = false` in `pyproject.toml`), so the cask declares `depends_on arch: :arm64`
and macOS 12 or later (the app's own minimum). `livecheck` follows the latest GitHub release.
The `zap` stanza removes the app's settings (`QSettings("adichtion", "adichtion")`, which Qt
stores as `~/Library/Preferences/com.adichtion.adichtion.plist`) and its saved window state.

### Own tap (the route that works now)

1. Create the public repository `emilwalsted/homebrew-tap` on GitHub, with a `Casks/` folder.
2. Copy `packaging/homebrew/Casks/adichtion.rb` to `Casks/adichtion.rb` there and push.
3. Check it on a Mac:
   ```
   brew tap emilwalsted/tap
   brew audit --cask --strict --online emilwalsted/tap/adichtion
   brew install --cask emilwalsted/tap/adichtion
   brew uninstall --cask --zap adichtion
   ```
4. Users install with `brew install --cask emilwalsted/tap/adichtion`.

At each release: run `update.py`, copy the cask over the tap's and push. (`brew bump-cask-pr`
can do the same once the cask is in homebrew-cask.)

### homebrew-cask (the main catalogue)

Homebrew's own catalogue (`Homebrew/homebrew-cask`) accepts a cask that is signed and
notarised (the dmg is), has a stable download, and meets its notability rules for a
GitHub-hosted app: per Homebrew's acceptance policy (read 03-11-2026) the repository needs
30 forks, 30 watchers or 75 stars, and three times that (90, 90 or 225) when its own author
submits it; a repository under 30 days old is normally not accepted. Check the current rules
first. When the project qualifies:

1. Fork `Homebrew/homebrew-cask`, add `Casks/a/adichtion.rb` (the same file).
2. From the fork, with `HOMEBREW_NO_INSTALL_FROM_API=1` set (otherwise brew ignores a local
   cask): `brew audit --new --cask adichtion`, `brew style --fix --cask adichtion`,
   `brew install --cask adichtion`, and `brew lgtm --online` last.
3. Open the pull request with the template's checklist filled in (including the question on
   AI use).

## winget

The manifests use schema 1.12.0 and the identifier `EmilWalsted.adichtion` (folder
`manifests/e/EmilWalsted/adichtion/<version>/`, as in `microsoft/winget-pkgs`). The installer
manifest lists the MSI, its sha256, its product code, the upgrade code (in
`AppsAndFeaturesEntries`, so winget recognises an installed copy) and the `.adicht` extension.
No `Scope` is declared: the MSI's properties (`ALLUSERS=2` with `MSIINSTALLPERUSER=1`) make a
per-user installation the default, which has not been tried on Windows; check it (below)
before adding `Scope: user`.

1. On Windows, install `wingetcreate` (`winget install Microsoft.WingetCreate`) and check the
   folder locally:
   ```
   winget validate --manifest packaging\winget\manifests\e\EmilWalsted\adichtion\X.Y.Z
   winget settings --enable LocalManifestFiles      # in an administrator shell
   winget install --manifest packaging\winget\manifests\e\EmilWalsted\adichtion\X.Y.Z
   ```
   Then confirm in *Settings ▸ Apps* that adichtion is listed once, and that
   `winget list adichtion` finds it.
2. Fork `microsoft/winget-pkgs`, copy the folder to the same path there, and open a pull
   request (or `wingetcreate submit <folder>`, which does the fork and the pull request
   with a GitHub token).
3. The pull request is checked by Microsoft's validation pipeline (it installs the MSI in a
   sandbox and scans it). The MSI should be the signed one: signing replaces the release
   asset, so run `update.py --verify` after signing, or the hash will not match. One package
   version per pull request; the first pull request asks for Microsoft's CLA to be signed.
   `Tools/SandboxTest.ps1` in winget-pkgs is another way to try a manifest.

For a later version, `wingetcreate update EmilWalsted.adichtion --version X.Y.Z --urls <msi url> --submit`
does the same from the published manifest; `update.py` keeps the copy here in step.

## Flathub

The Flatpak builds on Flathub's PySide base (`io.qt.PySide.BaseApp` 6.11 on the KDE 6.11
runtime: PySide6 and Python 3.13) and installs adichtion and its other dependencies from PyPI
wheels pinned in `flatpak/requirements.txt`, one wheel per architecture where the package has
compiled code. numpy is pinned below 2.3 and replaces the base's own numpy, because
opencv-python-headless 4.12 declares that limit; opencv stays below 4.13 for the reason in
`pyproject.toml`. Keep the pins inside the `[ui]` extra's ranges (a test checks it). The
desktop entry, the AppStream file and the icons (from `installer/` at the release tag) are
installed by the second module. The app gets read and write access to the home folder,
because a recording's video lies in a folder beside it and exports go next to the recording
by default; Flathub's reviewers ask for such a reason.

Nothing here has been built: `flatpak-builder` is needed for that, on Linux.

1. Build and run it locally (Flathub's own build command, which also mirrors the
   screenshots as the linter requires):
   ```
   flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
   flatpak install --user flathub org.flatpak.Builder
   cd packaging/flatpak
   flatpak run --command=flathub-build org.flatpak.Builder --install dk.emilwalsted.adichtion.yml
   flatpak run dk.emilwalsted.adichtion
   flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest dk.emilwalsted.adichtion.yml
   flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
   ```
   Open a recording with video and export an interval with its clip, to see that the
   bundled ffmpeg (imageio-ffmpeg) runs inside the sandbox.
2. Submit (the maintainer, by hand, see the AI note at the top): fork `flathub/flathub` with
   *Copy the master branch only* unticked, `git checkout -b add-adichtion new-pr`, add the
   manifest, `python3-modules.json`, the desktop, metainfo and mime files to the root, and
   open the pull request against `new-pr` titled `Add dk.emilwalsted.adichtion`. Say in the
   description why the app needs the home folder (above) and why the wheels bundle prebuilt
   FFmpeg (opencv-python-headless and imageio-ffmpeg; the reason opencv stays below 4.13 is
   in `pyproject.toml`). A reviewer's `bot, build` comment starts a test build. After the
   merge, accept the invitation to `flathub/dk.emilwalsted.adichtion` within a week (the
   GitHub account needs two-factor authentication); later releases are pull requests there.
3. App id: `dk.emilwalsted.adichtion` is the bundle id of the other installers. After
   acceptance, Flathub's Developer Portal shows a token that marks the app as verified, to be
   published either at `https://emilwalsted.dk/.well-known/org.flathub.VerifiedApps.txt`
   (Flathub follows redirects; the file belongs to the website's own repository) or as a DNS
   TXT record on `_flathub.emilwalsted.dk`, which leaves the website alone.

The store screenshots come from a synthetic recording (`adichtion.synth`), so they hold no
real data. After a visible change to the viewer, regenerate them and commit:

```
QT_QPA_PLATFORM=offscreen python packaging/flatpak/screenshots.py
```

The metainfo refers to them on `main` (raw.githubusercontent.com); Flathub mirrors them at
build time, so a later change on `main` reaches the store page only with the next build.

### Known gap

On Wayland, a desktop shows a window's icon by matching its app id to a desktop entry. The
app does not set one (`QGuiApplication.setDesktopFileName`), so GNOME and KDE may show a
generic icon for the running window even though the launcher has the right one. Setting it
to `dk.emilwalsted.adichtion` in `ui/app.py` would fix it for the Flatpak and does not affect
the other installers.
