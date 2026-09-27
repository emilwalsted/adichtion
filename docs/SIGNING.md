# Code-signing the installers

Same setup as RespMech (its `docs/SIGNING.md` has the long version; the certificates and
accounts are the same ones):

- **macOS: signed in CI, secret-gated.** With the seven Apple secrets set on this repository,
  `.github/workflows/release.yml` produces a Developer ID-signed and notarised dmg; without
  them it falls back to an ad-hoc-signed dmg (right-click ▸ *Open* the first time). The
  release notes say which one was built.
- **Windows: signed locally after the release.** CI builds the MSI unsigned, because the
  Certum individual/OSS key is hardware-backed (cryptoCertum card or SimplySign cloud) and
  cannot sign on a GitHub runner. `scripts/sign-msi-certum.sh vX.Y.Z` signs the released MSI
  on the Mac and replaces the release asset. No GitHub secrets on the Windows side.
- **Linux:** the `.deb` is not signed.

## macOS secrets

| Secret | Value |
|---|---|
| `APPLE_DEV_ID_IDENTITY` | `Developer ID Application: Emil Ingerslev Walsted (TEAMID)`, exactly as `security find-identity -v -p codesigning` prints it |
| `APPLE_CERTIFICATE_P12_BASE64` | the Developer ID certificate exported *with its private key* as `.p12`, base64-encoded |
| `APPLE_CERTIFICATE_PASSWORD` | the `.p12` export password |
| `APPLE_ID` | the Apple ID e-mail |
| `APPLE_TEAM_ID` | the 10-character Team ID |
| `APPLE_APP_SPECIFIC_PASSWORD` | an app-specific password from appleid.apple.com (not the Apple ID password) |
| `KEYCHAIN_PASSWORD` | any random string; it protects the throwaway keychain the runner creates |

GitHub cannot copy secrets from one repository to another, so they are set again here with
the same values as on `emilwalsted/respmech`. Each command below prompts for the value
(nothing ends up in the shell history); the `.p12` is piped in:

```
gh auth status
gh secret set APPLE_DEV_ID_IDENTITY -R emilwalsted/adichtion
gh secret set APPLE_CERTIFICATE_PASSWORD -R emilwalsted/adichtion
gh secret set APPLE_ID -R emilwalsted/adichtion
gh secret set APPLE_TEAM_ID -R emilwalsted/adichtion
gh secret set APPLE_APP_SPECIFIC_PASSWORD -R emilwalsted/adichtion
openssl rand -base64 24 | gh secret set KEYCHAIN_PASSWORD -R emilwalsted/adichtion
base64 -i ~/path/to/DeveloperID.p12 | gh secret set APPLE_CERTIFICATE_P12_BASE64 -R emilwalsted/adichtion
gh secret list -R emilwalsted/adichtion
```

To find the identity string and the Team ID on the Mac that holds the certificate:

```
security find-identity -v -p codesigning
```

The Team ID is the ten characters in the parentheses. If the `.p12` was never exported (the
certificate only lives in the keychain): Keychain Access ▸ *My Certificates* ▸ right-click
the Developer ID Application certificate ▸ *Export…* ▸ format *.p12*, and choose the export
password that becomes `APPLE_CERTIFICATE_PASSWORD`.

A workflow run started after the secrets exist signs and notarises automatically
(`Detect signing config` prints nothing; an ad-hoc build prints a notice). Notarisation with
`--wait` takes a few minutes. If signing fails with "unable to build certificate chain", the
Apple intermediate certificates the workflow downloads did not import; check the step's log.

## Windows (Certum, locally)

One-time on the Mac: `brew install jsign osslsigncode gh`, and the cryptoCertum card + reader
(or SimplySign Desktop for the cloud key). After a `v*` tag has published:

```
scripts/sign-msi-certum.sh v0.3.0
```

The script refuses to run while `release.yml` is still building for that tag (the run would
overwrite the signed asset with its own unsigned one), downloads the MSI, signs it with an
RFC-3161 timestamp from Certum, verifies with `osslsigncode`, re-uploads it with `--clobber`
and flips the Windows row of the release notes to "(signed + timestamped)".

For an MSI that is not on a release (a `workflow_dispatch` artifact), call jsign directly:

```
jsign --storetype PKCS11 --keystore ~/certum-pkcs11.cfg --tsaurl http://time.certum.pl/ --alg SHA-256 adichtion-0.3.0.msi
osslsigncode verify adichtion-0.3.0.msi
```

(`--storetype CRYPTOCERTUM` without `--keystore` for the physical card.) A new certificate has
no SmartScreen reputation yet, so Windows may still warn for the first downloads even though
the signature is valid.
