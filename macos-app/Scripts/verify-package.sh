#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPECTED_VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
EXPECTED_ARCHITECTURES="${ELCHANGO_EXPECTED_ARCHITECTURES:-}"
VERIFY_DISTRIBUTION="${ELCHANGO_VERIFY_DISTRIBUTION:-0}"
VERIFY_NOTARIZATION="${ELCHANGO_VERIFY_NOTARIZATION:-0}"
APP_DIR="${1:-}"
if [[ -z "${APP_DIR}" ]]; then
  echo "Usage: $0 /path/to/elChango.app" >&2
  exit 2
fi

PLIST="${APP_DIR}/Contents/Info.plist"
EXECUTABLE="${APP_DIR}/Contents/MacOS/elChango"
HOOK_REPORTER="${APP_DIR}/Contents/MacOS/elChangoHookReporter"
APP_ICON="${APP_DIR}/Contents/Resources/elChango.icns"
WEB_INDEX="${APP_DIR}/Contents/Resources/Web/index.html"
LICENSE="${APP_DIR}/Contents/Resources/LICENSE"
THIRD_PARTY_NOTICES="${APP_DIR}/Contents/Resources/THIRD_PARTY_NOTICES.md"
TRADEMARKS="${APP_DIR}/Contents/Resources/TRADEMARKS.md"

[[ -f "${PLIST}" ]] || {
  echo "ERROR: missing Info.plist." >&2
  exit 1
}
[[ -x "${EXECUTABLE}" ]] || {
  echo "ERROR: missing executable." >&2
  exit 1
}
[[ -x "${HOOK_REPORTER}" ]] || {
  echo "ERROR: missing native hook reporter." >&2
  exit 1
}
[[ -f "${APP_ICON}" ]] || {
  echo "ERROR: missing application icon." >&2
  exit 1
}
[[ -f "${WEB_INDEX}" ]] || {
  echo "ERROR: missing bundled web deck." >&2
  exit 1
}
[[ -f "${LICENSE}" ]] || {
  echo "ERROR: missing project license." >&2
  exit 1
}
[[ -f "${THIRD_PARTY_NOTICES}" ]] || {
  echo "ERROR: missing third-party notices." >&2
  exit 1
}
[[ -f "${TRADEMARKS}" ]] || {
  echo "ERROR: missing trademark notice." >&2
  exit 1
}

plutil -lint "${PLIST}" >/dev/null

[[ "$(plutil -extract CFBundleIdentifier raw -o - "${PLIST}")" == \
  "com.jychp.elchango" ]] || {
  echo "ERROR: unexpected bundle identifier." >&2
  exit 1
}
[[ "$(plutil -extract CFBundleExecutable raw -o - "${PLIST}")" == \
  "elChango" ]] || {
  echo "ERROR: unexpected executable name." >&2
  exit 1
}
[[ "$(plutil -extract CFBundleIconFile raw -o - "${PLIST}")" == \
  "elChango" ]] || {
  echo "ERROR: unexpected application icon." >&2
  exit 1
}
[[ "$(plutil -extract CFBundleShortVersionString raw -o - "${PLIST}")" == \
  "${EXPECTED_VERSION}" ]] || {
  echo "ERROR: application version does not match root VERSION." >&2
  exit 1
}
[[ "$(plutil -extract LSUIElement raw -o - "${PLIST}")" == "true" ]] || {
  echo "ERROR: LSUIElement must be true." >&2
  exit 1
}
[[ "$(plutil -extract LSMinimumSystemVersion raw -o - "${PLIST}")" == \
  "14.0" ]] || {
  echo "ERROR: unexpected minimum macOS version." >&2
  exit 1
}

if [[ -n "${EXPECTED_ARCHITECTURES}" ]]; then
  expected_architectures="$(
    printf '%s\n' "${EXPECTED_ARCHITECTURES}" |
      tr ' ' '\n' |
      awk 'NF' |
      sort |
      tr '\n' ' '
  )"
  for executable_path in "${EXECUTABLE}" "${HOOK_REPORTER}"; do
    actual_architectures="$(
      lipo -archs "${executable_path}" |
        tr ' ' '\n' |
        awk 'NF' |
        sort |
        tr '\n' ' '
    )"
    if [[ "${actual_architectures}" != "${expected_architectures}" ]]; then
      echo "ERROR: ${executable_path} architectures are '${actual_architectures}', expected '${expected_architectures}'." >&2
      exit 1
    fi
  done
fi

codesign --verify --deep --strict --verbose=2 "${APP_DIR}"
codesign --verify --strict --verbose=2 "${HOOK_REPORTER}"

for signed_path in "${APP_DIR}" "${HOOK_REPORTER}"; do
  entitlements="$(
    codesign --display --entitlements - "${signed_path}" 2>&1 || true
  )"
  if [[ "${entitlements}" == *"com.apple.security.get-task-allow"* ]]; then
    echo "ERROR: ${signed_path} must not enable get-task-allow." >&2
    exit 1
  fi
done

if [[ "${VERIFY_DISTRIBUTION}" == "1" ]]; then
  signature="$(
    codesign --display --verbose=4 "${APP_DIR}" 2>&1
  )"
  if [[ "${signature}" != *"Authority=Developer ID Application:"* ]]; then
    echo "ERROR: app is not signed with Developer ID Application." >&2
    exit 1
  fi
  if [[ "${signature}" != *"flags=0x10000(runtime)"* ]]; then
    echo "ERROR: app signature does not enable Hardened Runtime." >&2
    exit 1
  fi
  if [[ "${signature}" != *"Timestamp="* ]]; then
    echo "ERROR: app signature does not contain an Apple timestamp." >&2
    exit 1
  fi
  if [[ -n "${ELCHANGO_TEAM_ID:-}" ]] &&
     [[ "${signature}" != *"TeamIdentifier=${ELCHANGO_TEAM_ID}"* ]]; then
    echo "ERROR: app signature does not use the expected Apple team." >&2
    exit 1
  fi

  hook_signature="$(
    codesign --display --verbose=4 "${HOOK_REPORTER}" 2>&1
  )"
  if [[ "${hook_signature}" != *"Authority=Developer ID Application:"* ]] ||
     [[ "${hook_signature}" != *"flags=0x10000(runtime)"* ]] ||
     [[ "${hook_signature}" != *"Timestamp="* ]]; then
    echo "ERROR: hook reporter lacks a timestamped Developer ID runtime signature." >&2
    exit 1
  fi

fi

if [[ "${VERIFY_NOTARIZATION}" == "1" ]]; then
  xcrun stapler validate "${APP_DIR}"
  spctl --assess --type execute --verbose=4 "${APP_DIR}"
fi

echo "Verified ${APP_DIR}"
