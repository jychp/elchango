#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${MACOS_DIR}/.." && pwd)"

SIGN_MODE="${ELCHANGO_SIGN_MODE:-adhoc}"
CONFIGURATION="${ELCHANGO_CONFIGURATION:-release}"
ARCHITECTURES="${ELCHANGO_ARCHITECTURES:-$(uname -m)}"
APP_VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
APP_BUILD="${ELCHANGO_BUILD:-1}"
APP_DIR="${MACOS_DIR}/dist/elChango.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_CONTENTS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
ICON_SOURCE="${REPO_DIR}/docs/assets/elchango-logo.png"

if [[ ! "${APP_VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "ERROR: VERSION must contain strict SemVer in X.Y.Z form." >&2
  exit 2
fi
if [[ ! "${APP_BUILD}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: ELCHANGO_BUILD must be a positive integer." >&2
  exit 2
fi

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
  developer-id)
    if [[ -z "${ELCHANGO_CODESIGN_IDENTITY:-}" ]]; then
      echo "ERROR: ELCHANGO_CODESIGN_IDENTITY is required for Developer ID signing." >&2
      exit 2
    fi
    SIGN_IDENTITY="${ELCHANGO_CODESIGN_IDENTITY}"
    ;;
  *)
    echo "ERROR: ELCHANGO_SIGN_MODE must be 'adhoc', 'identity', or 'developer-id'." >&2
    exit 2
    ;;
esac

npm --prefix "${REPO_DIR}/web" run build

read -r -a ARCHITECTURE_LIST <<<"${ARCHITECTURES}"
if [[ "${#ARCHITECTURE_LIST[@]}" -eq 0 ]]; then
  echo "ERROR: ELCHANGO_ARCHITECTURES must name at least one architecture." >&2
  exit 2
fi

BUILD_ROOT="${MACOS_DIR}/.build/elchango-package"
mkdir -p "${BUILD_ROOT}"

declare -a APP_BINARIES=()
declare -a HOOK_BINARIES=()
for architecture in "${ARCHITECTURE_LIST[@]}"; do
  case "${architecture}" in
    arm64|x86_64) ;;
    *)
      echo "ERROR: unsupported macOS architecture '${architecture}'." >&2
      exit 2
      ;;
  esac

  scratch_path="${BUILD_ROOT}/${architecture}"
  triple="${architecture}-apple-macosx14.0"
  for product in ElChangoApp ElChangoHookReporter; do
    swift build \
      --package-path "${MACOS_DIR}" \
      --scratch-path "${scratch_path}" \
      --configuration "${CONFIGURATION}" \
      --triple "${triple}" \
      --product "${product}"
  done
  bin_dir="$(
    swift build \
      --package-path "${MACOS_DIR}" \
      --scratch-path "${scratch_path}" \
      --configuration "${CONFIGURATION}" \
      --triple "${triple}" \
      --show-bin-path
  )"
  APP_BINARIES+=("${bin_dir}/ElChangoApp")
  HOOK_BINARIES+=("${bin_dir}/ElChangoHookReporter")
done

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
if [[ "${#ARCHITECTURE_LIST[@]}" -eq 1 ]]; then
  cp "${APP_BINARIES[0]}" "${MACOS_CONTENTS_DIR}/elChango"
  cp "${HOOK_BINARIES[0]}" "${MACOS_CONTENTS_DIR}/elChangoHookReporter"
else
  lipo -create \
    "${APP_BINARIES[@]}" \
    -output "${MACOS_CONTENTS_DIR}/elChango"
  lipo -create \
    "${HOOK_BINARIES[@]}" \
    -output "${MACOS_CONTENTS_DIR}/elChangoHookReporter"
fi
cp "${ICON_WORK_DIR}/elChango.icns" "${RESOURCES_DIR}/elChango.icns"
cp "${REPO_DIR}/LICENSE" "${RESOURCES_DIR}/LICENSE"
cp \
  "${REPO_DIR}/THIRD_PARTY_NOTICES.md" \
  "${RESOURCES_DIR}/THIRD_PARTY_NOTICES.md"
cp "${REPO_DIR}/TRADEMARKS.md" "${RESOURCES_DIR}/TRADEMARKS.md"
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
sign_path() {
  local identifier="$1"
  local path="$2"
  if [[ "${SIGN_MODE}" == "developer-id" ]]; then
    codesign \
      --force \
      --sign "${SIGN_IDENTITY}" \
      --options runtime \
      --timestamp \
      --identifier "${identifier}" \
      "${path}"
  else
    codesign \
      --force \
      --sign "${SIGN_IDENTITY}" \
      --identifier "${identifier}" \
      "${path}"
  fi
}

sign_path \
  "com.jychp.elchango.hook-reporter" \
  "${MACOS_CONTENTS_DIR}/elChangoHookReporter"
sign_path "com.jychp.elchango" "${APP_DIR}"

ELCHANGO_EXPECTED_ARCHITECTURES="${ARCHITECTURES}" \
ELCHANGO_VERIFY_DISTRIBUTION="$(
  [[ "${SIGN_MODE}" == "developer-id" ]] && printf '1' || printf '0'
)" \
  "${SCRIPT_DIR}/verify-package.sh" "${APP_DIR}"

echo "Packaged ${APP_DIR}"
if [[ "${SIGN_MODE}" == "adhoc" ]]; then
  echo "WARNING: ad-hoc signing is not suitable for TCC permission persistence testing."
fi
