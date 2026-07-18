#!/usr/bin/env bash

set -euo pipefail

APP_DIR="${1:-}"
if [[ -z "${APP_DIR}" ]]; then
  echo "Usage: $0 /path/to/elChango.app" >&2
  exit 2
fi

PLIST="${APP_DIR}/Contents/Info.plist"
EXECUTABLE="${APP_DIR}/Contents/MacOS/elChango"
WEB_INDEX="${APP_DIR}/Contents/Resources/Web/index.html"

[[ -f "${PLIST}" ]] || {
  echo "ERROR: missing Info.plist." >&2
  exit 1
}
[[ -x "${EXECUTABLE}" ]] || {
  echo "ERROR: missing executable." >&2
  exit 1
}
[[ -f "${WEB_INDEX}" ]] || {
  echo "ERROR: missing bundled web deck." >&2
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
echo "Verified ${APP_DIR}"
