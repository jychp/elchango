# Releasing elChango

elChango uses one version from the root `VERSION` file. A release tag publishes:

- a signed, notarized Universal 2 macOS app in a ZIP archive;
- a SHA-256 checksum for the macOS archive;
- the installable Stream Deck plugin;
- a SHA-256 checksum for the Stream Deck plugin.

Cursor and Claude provider plugins are installed from their marketplaces or the
repository. Their deterministic ZIP builds are validation artifacts, not public
release downloads.

## Apple prerequisites

The macOS artifact uses a `Developer ID Application` certificate and Apple's
notary service. It is distributed outside the Mac App Store and is not
sandboxed. Accessibility permission remains an explicit user decision after
installation.

### Create the Developer ID certificate

1. Open Keychain Access on a trusted Mac.
2. Choose **Certificate Assistant**, then **Request a Certificate From a
   Certificate Authority**.
3. Enter the Apple Developer account email, choose **Saved to disk**, and create
   the certificate signing request.
4. In Apple Developer, open **Certificates, Identifiers & Profiles**, create a
   **Developer ID Application** certificate, and upload the request.
5. Download and open the issued certificate. In Keychain Access, verify that it
   appears under **My Certificates** with its private key.
6. Export the certificate and private key together as a password-protected
   `.p12` file. Store the file and password in a secure password manager.

Do not create a `Mac App Distribution` certificate. That certificate is for Mac
App Store submissions, not direct downloads or Homebrew Casks.

### Create the notarization API key

In App Store Connect, open **Users and Access**, then **Integrations** and create
a team API key with the least role Apple currently permits for notarization.
The Developer role is normally sufficient. Record:

- the key ID;
- the issuer ID;
- the downloaded `AuthKey_<key-id>.p8` file.

Apple allows the private key to be downloaded only once. Store it in a secure
password manager. Use a dedicated key so it can be revoked without affecting
unrelated automation.

## Configure GitHub

Create a GitHub Actions environment named `release`. Restrict it to `main` and
release tags, and add an approval rule if the repository's maintainer model
supports one.

Add these environment secrets:

- `MACOS_CERTIFICATE_P12_BASE64`: base64-encoded `.p12` contents;
- `MACOS_CERTIFICATE_PASSWORD`: the `.p12` export password;
- `APPLE_API_KEY_P8_BASE64`: base64-encoded `.p8` contents;
- `APPLE_API_KEY_ID`: App Store Connect API key ID;
- `APPLE_API_ISSUER_ID`: App Store Connect issuer ID;
- `HOMEBREW_TAP_TOKEN`: a fine-grained PAT limited to `jychp/homebrew-tap`
  with `Contents: read/write` and `Pull requests: read/write`.

Add `APPLE_TEAM_ID` as an environment variable. The team ID is not a secret.

Encode each binary secret without writing an additional unencrypted file:

```bash
base64 < DeveloperIDApplication.p12 | pbcopy
base64 < AuthKey_KEYID.p8 | pbcopy
```

Paste the clipboard contents into the matching GitHub secret. Never commit,
upload as a workflow artifact, print, or send these values through an issue or
chat.

The release job reconstructs both files under the runner's temporary
directory, imports the certificate into an ephemeral keychain, and deletes the
keychain and key files in an `always()` cleanup step.

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
unzip elChango-1.0.0-macos-universal.zip
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
make release VERSION=1.0.0
```

The Make target verifies that local `main` exactly matches `origin/main`, then
creates and pushes the annotated `v1.0.0` tag. The tag workflow repeats its own
exact-main and version checks. It publishes only after both release artifacts
have built successfully.

After the GitHub Release exists, the publish job sends an `elchango-release`
repository dispatch to `jychp/homebrew-tap`. The tap downloads the immutable
release URL and checksum rather than trusting values in the dispatch payload.
It generates `Casks/elchango.rb`, opens a pull request, and requests auto-merge.
Required Intel and Apple Silicon checks audit and install the Cask, then verify
the Developer ID signature, stapled ticket, and Gatekeeper acceptance.

The same fine-grained PAT is stored as `HOMEBREW_TAP_TOKEN` in the tap. It is
required there because pull requests created with the default `GITHUB_TOKEN`
do not trigger another `pull_request` workflow. Protect the tap's `main` branch
with the Homebrew and elChango Cask checks, enable squash auto-merge, and delete
merged automation branches.

If the update pull request fails, the previous Cask remains published. Fix the
release or workflow issue and redispatch the same version. The generator avoids
duplicate changes and reuses an existing open version pull request.

## Local packaging

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

## Rotation and failure handling

- Revoke and replace the App Store Connect key if its private key may have been
  exposed.
- Revoke and replace the Developer ID certificate if its private key may have
  been exposed, then follow Apple's incident guidance for existing releases.
- Replace the matching GitHub environment secrets, then run a manual release
  test.
- Rotate the fine-grained Homebrew PAT before expiration and update
  `HOMEBREW_TAP_TOKEN` in both repositories.
- If Apple rejects a submission, inspect the `notarytool log` emitted by the
  workflow. Do not retry blindly. Fix the reported signing or bundle issue and
  build a new artifact.
- If Apple remains in progress beyond the workflow timeout, rerun only the
  failed wait job. The receipt binds recovery to the original commit and exact
  signed archive checksum.
- The cleanup steps run after ordinary failures and bounded Apple waits. A
  GitHub-hosted runner is ephemeral, so runner disposal remains the final
  cleanup boundary if GitHub forcibly terminates a job.
- Never publish an ad hoc signed artifact or bypass Gatekeeper to make a failed
  release appear installable.
