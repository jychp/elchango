#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPECTED_VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
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

codesign --verify --strict --verbose=2 "${APP_DIR}"
codesign --verify --strict --verbose=2 "${HOOK_REPORTER}"
echo "Verified ${APP_DIR}"
