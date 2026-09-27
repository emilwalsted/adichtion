---
name: release
description: Releasing adichtion — CHANGELOG entry, the two-place version bump, v* tag, the "Build installers" workflow (Windows MSI, macOS dmg, Ubuntu .deb), macOS notarisation secrets and local Certum MSI signing. Use for any release, changelog, installer, signing or version task.
---

# Releasing adichtion

Step-by-step runbook: `docs/RELEASING.md`. Signing detail and the secret commands:
`docs/SIGNING.md`. What follows is the project memory that sits above both.

## Ask first

A `v*` tag publishes a GitHub release (non-draft, marked Latest) with the installers
attached. Pushing one is an upload to a live site in the sense of the repo `CLAUDE.md`: ask
Emil before tagging, every time. A manual run of the workflow (*Actions ▸ Build installers ▸
Run workflow*) only produces artifacts and needs no permission.

## The release (`.github/workflows/release.yml` = "Build installers")

- Trigger: a `v*` tag, or manual dispatch (input *Ad-hoc sign the dmg* for a quick test build
  that skips Developer ID signing and notarisation). Three jobs build with briefcase: a
  Windows **MSI** (unsigned), a macOS **dmg** (Apple Silicon), an Ubuntu 24.04 **.deb** (built
  inside a Docker image with `--target ubuntu:noble`). On a tag, `publish-release` creates or
  updates the release with an install table as the notes.
- The version lives in **two places** that must agree: `src/adichtion/__init__.py`
  (`__version__`, hatchling reads it) and `pyproject.toml` `[tool.briefcase] version` (a manual
  copy; briefcase cannot read a dynamic version). `CHANGELOG.md` needs a `## [X.Y.Z] – date`
  entry too: the Debian package is built from it. `tests/test_main.py` fails when the three
  disagree, so the bump cannot be half-done.
- Tags are clean semver `vX.Y.Z`; the installer file names come from the briefcase version.
- macOS signing is **secret-gated**: Developer ID + notarisation when the seven Apple secrets
  are present (same names and values as on RespMech), else ad-hoc. `docs/SIGNING.md` has the
  `gh secret set` commands. The Windows MSI is **Certum-signed locally** after the release with
  `scripts/sign-msi-certum.sh vX.Y.Z` (ported from RespMech); it refuses to run while the
  build for that tag is still in flight, since that run would overwrite the signed asset.
- The packaged binary is `python -m adichtion`: the app without arguments, the CLI with a
  subcommand. Smoke-test an installer with `adichtion --version`, `adichtion info FILE` and a
  plain start.
- A `.adicht` double-click opens the app (document type registered by briefcase; macOS
  `FileOpen` handled in `ui/app.py`). This is the part only a real Mac or Windows machine can
  verify; the headless tests cannot.

## What is not there yet

- No PyPI publishing and no website hook (unlike RespMech); the release is the GitHub release.
- No Intel-Mac build (the dmg is built on `macos-latest`, Apple Silicon), as for RespMech.
- No `release-request` flow for environments that cannot push tags: ask Emil to tag from the
  Mac instead.
