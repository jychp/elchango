#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${MACOS_DIR}/.." && pwd)"

SIGN_MODE="${ELCHANGO_SIGN_MODE:-adhoc}"
CONFIGURATION="${ELCHANGO_CONFIGURATION:-release}"
APP_VERSION="${ELCHANGO_VERSION:-0.1.0}"
APP_BUILD="${ELCHANGO_BUILD:-1}"
APP_DIR="${MACOS_DIR}/dist/elChango.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_CONTENTS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
ICON_SOURCE="${REPO_DIR}/docs/assets/elchango-logo.png"

case "${SIGN_MODE}" in
  adhoc)
    SIGN_IDENTITY="-"
    ;;
  identity)
    if [[ -z "${ELCHANGO_CODESIGN_IDENTITY:-}" ]]; then
      echo "ERROR: ELCHANGO_CODESIGN_IDENTITY is required for identity signing." >&2
      exit 2
    fi
    SIGN_IDENTITY="${ELCHANGO_CODESIGN_IDENTITY}"
    ;;
  *)
    echo "ERROR: ELCHANGO_SIGN_MODE must be 'adhoc' or 'identity'." >&2
    exit 2
    ;;
esac

npm --prefix "${REPO_DIR}/web" run build
swift build \
  --package-path "${MACOS_DIR}" \
  --configuration "${CONFIGURATION}" \
  --product ElChangoApp
swift build \
  --package-path "${MACOS_DIR}" \
  --configuration "${CONFIGURATION}" \
  --product ElChangoHookReporter

BIN_DIR="$(
  swift build \
    --package-path "${MACOS_DIR}" \
    --configuration "${CONFIGURATION}" \
    --show-bin-path
)"

ICON_WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${ICON_WORK_DIR}"' EXIT
ICONSET_DIR="${ICON_WORK_DIR}/elChango.iconset"
mkdir -p "${ICONSET_DIR}"
for specification in \
  "16 icon_16x16.png" \
  "32 icon_16x16@2x.png" \
  "32 icon_32x32.png" \
  "64 icon_32x32@2x.png" \
  "128 icon_128x128.png" \
  "256 icon_128x128@2x.png" \
  "256 icon_256x256.png" \
  "512 icon_256x256@2x.png" \
  "512 icon_512x512.png" \
  "1024 icon_512x512@2x.png"
do
  read -r size filename <<<"${specification}"
  sips \
    --resampleHeightWidth "${size}" "${size}" \
    "${ICON_SOURCE}" \
    --out "${ICONSET_DIR}/${filename}" \
    >/dev/null
done
iconutil \
  --convert icns \
  --output "${ICON_WORK_DIR}/elChango.icns" \
  "${ICONSET_DIR}"

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_CONTENTS_DIR}" "${RESOURCES_DIR}/Web"
cp "${BIN_DIR}/ElChangoApp" "${MACOS_CONTENTS_DIR}/elChango"
cp \
  "${BIN_DIR}/ElChangoHookReporter" \
  "${MACOS_CONTENTS_DIR}/elChangoHookReporter"
cp "${ICON_WORK_DIR}/elChango.icns" "${RESOURCES_DIR}/elChango.icns"
cp -R "${REPO_DIR}/web/dist/." "${RESOURCES_DIR}/Web/"

cat > "${CONTENTS_DIR}/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>elChango</string>
  <key>CFBundleExecutable</key>
  <string>elChango</string>
  <key>CFBundleIdentifier</key>
  <string>com.jychp.elchango</string>
  <key>CFBundleIconFile</key>
  <string>elChango</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>elChango</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${APP_BUILD}</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

plutil -lint "${CONTENTS_DIR}/Info.plist"
codesign \
  --force \
  --sign "${SIGN_IDENTITY}" \
  --identifier "com.jychp.elchango.hook-reporter" \
  "${MACOS_CONTENTS_DIR}/elChangoHookReporter"
codesign \
  --force \
  --sign "${SIGN_IDENTITY}" \
  --identifier "com.jychp.elchango" \
  "${APP_DIR}"

"${SCRIPT_DIR}/verify-package.sh" "${APP_DIR}"

echo "Packaged ${APP_DIR}"
if [[ "${SIGN_MODE}" == "adhoc" ]]; then
  echo "WARNING: ad-hoc signing is not suitable for TCC permission persistence testing."
fi
