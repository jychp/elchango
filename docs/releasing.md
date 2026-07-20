# Releasing elChango

elChango uses one version from the root `VERSION` file. A release tag publishes:

- a signed, notarized Universal 2 macOS app in a ZIP archive;
- a SHA-256 checksum for the macOS archive;
- the installable Stream Deck plugin;
- a SHA-256 checksum for the Stream Deck plugin.

Cursor and Claude provider plugins are installed from their marketplaces or the
repository. Their deterministic ZIP builds are validation artifacts, not public
release downloads.

This document is the release runbook. The one-time pipeline configuration it
relies on (Apple credentials, GitHub environment and secrets, secret rotation)
lives in [release-setup.md](release-setup.md).

## Test before publishing

The Release workflow supports manual dispatch from `main`. A manual run:

1. verifies that the selected commit exactly matches `origin/main`;
2. packages the Stream Deck plugin;
3. builds `arm64` and `x86_64` app executables without release credentials;
4. combines them into a Universal 2 application;
5. signs the helper and outer app in a protected job with Developer ID,
   Hardened Runtime, and an Apple timestamp;
6. submits the exact signed archive and immediately preserves it with a receipt
   containing the Apple submission ID, source commit, version, and SHA-256;
7. waits for an `Accepted` result in a separate job;
8. staples and validates the notarization ticket;
9. checks Gatekeeper acceptance;
10. uploads private workflow artifacts without creating a GitHub Release.

Every workflow job and step has a bounded timeout. The Apple waiter uses a
shorter native timeout than its job, leaving time for credential cleanup.
Apple continues processing after a local or GitHub timeout.

If **Wait for and staple macOS app** times out, use **Re-run failed jobs** on
the same workflow run. The successful submission job is not rerun, so the
failed job downloads the preserved signed archive and receipt, waits on the
same Apple submission ID, and never submits a duplicate. Do not choose
**Re-run all jobs** for this recovery path because that intentionally creates
a new submission.

Download the manual run's macOS artifact and verify it on another Mac or a clean
user account:

```bash
unzip elChango-X.Y.Z-macos-universal.zip
lipo -archs elChango.app/Contents/MacOS/elChango
codesign --verify --deep --strict --verbose=2 elChango.app
xcrun stapler validate elChango.app
spctl --assess --type execute --verbose=4 elChango.app
```

The architecture output must contain both `arm64` and `x86_64`. Gatekeeper must
report acceptance without removing quarantine attributes or using an
installation override.

## Publish a release

Before changing the version, make sure the complete CI workflow is green on
`main`. Update the root `VERSION` and all metadata enforced by
`make test-versions` in the same pull request.

From an updated, clean `main` checkout:

```bash
make test
make release VERSION=X.Y.Z
```

The Make target verifies that local `main` exactly matches `origin/main`, then
creates and pushes the annotated `vX.Y.Z` tag. The tag workflow repeats its own
exact-main and version checks. It publishes only after both release artifacts
have built successfully.

The publish job creates the GitHub Release in a single atomic call that attaches
every asset before publication. This is required for immutable releases, which
freeze assets at publish time and reject any later change, so there is no
post-publish asset re-upload. If a previous run left an incomplete draft, the
job deletes it and recreates the release cleanly; a release that is already
published is left untouched. After publication you can confirm the attestation
with `gh release verify vX.Y.Z` and check an individual asset with
`gh release verify-asset vX.Y.Z <asset>`.

After the GitHub Release exists, the publish job sends an `elchango-release`
repository dispatch to `jychp/homebrew-tap`. The tap downloads the immutable
release URL and checksum rather than trusting values in the dispatch payload.
It generates `Casks/elchango.rb`, opens a pull request, and requests auto-merge.
Required Intel and Apple Silicon checks audit and install the Cask, then verify
the Developer ID signature, stapled ticket, and Gatekeeper acceptance.

If the update pull request fails, the previous Cask remains published. Fix the
release or workflow issue and redispatch the same version. The generator avoids
duplicate changes and reuses an existing open version pull request.

## When a release fails

- If Apple rejects a submission, inspect the `notarytool log` emitted by the
  workflow. Do not retry blindly. Fix the reported signing or bundle issue and
  build a new artifact.
- If Apple remains in progress beyond the workflow timeout, rerun only the
  failed wait job. The receipt binds recovery to the original commit and exact
  signed archive checksum.
- Never publish an ad hoc signed artifact or bypass Gatekeeper to make a failed
  release appear installable.

## Rehearse the build locally

The normal development build remains native to the current Mac and ad hoc
signed:

```bash
make build-app-macos
```

Build an ad hoc Universal 2 bundle without contacting Apple:

```bash
make build-app-macos-universal
```

To exercise Developer ID signing locally, pass a stable identity:

```bash
ELCHANGO_SIGN_MODE=developer-id \
ELCHANGO_CODESIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
ELCHANGO_TEAM_ID="TEAMID" \
make build-app-macos-universal
```

Notarization additionally requires `APPLE_API_KEY_PATH`,
`APPLE_API_KEY_ID`, and `APPLE_API_ISSUER_ID`. `make notarize-app-macos`
submits, records `dist/elChango-notarization-receipt.json`, waits on that
submission, and creates the final ZIP and checksum under `dist/`.

If the local Apple wait times out, do not rerun `make notarize-app-macos`
because that target intentionally builds and submits a new artifact. Resume the
preserved submission instead:

```bash
make finish-notarization-app-macos
```

Use `make submit-notarization-app-macos` only after a Developer ID signed
Universal 2 app has been built and no receipt exists for that artifact.
