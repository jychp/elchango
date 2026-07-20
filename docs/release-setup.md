# Release pipeline setup

One-time configuration the elChango release pipeline depends on. For the release
procedure itself, see [releasing.md](releasing.md).

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

## Rotation

- Revoke and replace the App Store Connect key if its private key may have been
  exposed.
- Revoke and replace the Developer ID certificate if its private key may have
  been exposed, then follow Apple's incident guidance for existing releases.
- Replace the matching GitHub environment secrets, then run a manual release
  test.
- Rotate the fine-grained Homebrew PAT before expiration and update
  `HOMEBREW_TAP_TOKEN` in both repositories.
- The cleanup steps run after ordinary failures and bounded Apple waits. A
  GitHub-hosted runner is ephemeral, so runner disposal remains the final
  cleanup boundary if GitHub forcibly terminates a job.
