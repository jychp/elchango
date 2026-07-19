#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${MACOS_DIR}/.." && pwd)"

APP_DIR="${1:-${MACOS_DIR}/dist/elChango.app}"
DIST_DIR="${ELCHANGO_DIST_DIR:-${REPO_DIR}/dist}"
APP_VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
API_KEY_PATH="${APPLE_API_KEY_PATH:-}"
API_KEY_ID="${APPLE_API_KEY_ID:-}"
API_ISSUER_ID="${APPLE_API_ISSUER_ID:-}"

for required_command in ditto python3 shasum xcrun; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "ERROR: required command '${required_command}' is unavailable." >&2
    exit 2
  fi
done

if [[ ! -d "${APP_DIR}" ]]; then
  echo "ERROR: application bundle not found at ${APP_DIR}." >&2
  exit 2
fi
if [[ -z "${API_KEY_PATH}" || ! -f "${API_KEY_PATH}" ]]; then
  echo "ERROR: APPLE_API_KEY_PATH must reference an App Store Connect API key." >&2
  exit 2
fi
if [[ -z "${API_KEY_ID}" || -z "${API_ISSUER_ID}" ]]; then
  echo "ERROR: APPLE_API_KEY_ID and APPLE_API_ISSUER_ID are required." >&2
  exit 2
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

UPLOAD_ARCHIVE="${WORK_DIR}/elChango-notarization-upload.zip"
RESULT_PATH="${WORK_DIR}/notary-result.json"
FINAL_NAME="elChango-${APP_VERSION}-macos-universal.zip"
FINAL_ARCHIVE="${DIST_DIR}/${FINAL_NAME}"

ditto \
  -c \
  -k \
  --sequesterRsrc \
  --keepParent \
  "${APP_DIR}" \
  "${UPLOAD_ARCHIVE}"

submission_exit=0
xcrun notarytool submit "${UPLOAD_ARCHIVE}" \
  --key "${API_KEY_PATH}" \
  --key-id "${API_KEY_ID}" \
  --issuer "${API_ISSUER_ID}" \
  --wait \
  --output-format json \
  >"${RESULT_PATH}" || submission_exit=$?

read -r submission_id submission_status < <(
  python3 - "${RESULT_PATH}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        result = json.load(handle)
except (OSError, json.JSONDecodeError):
    result = {}
print(result.get("id") or "-", result.get("status") or "-")
PY
)
[[ "${submission_id}" == "-" ]] && submission_id=""
[[ "${submission_status}" == "-" ]] && submission_status=""

if [[ "${submission_exit}" -ne 0 ||
      -z "${submission_id}" ||
      "${submission_status}" != "Accepted" ]]; then
  echo "ERROR: Apple notarization status is '${submission_status:-unknown}'." >&2
  if [[ -n "${submission_id}" ]]; then
    xcrun notarytool log "${submission_id}" \
      --key "${API_KEY_PATH}" \
      --key-id "${API_KEY_ID}" \
      --issuer "${API_ISSUER_ID}" || true
  fi
  exit 1
fi

xcrun stapler staple "${APP_DIR}"
xcrun stapler validate "${APP_DIR}"

ELCHANGO_VERIFY_DISTRIBUTION=1 \
ELCHANGO_VERIFY_NOTARIZATION=1 \
ELCHANGO_EXPECTED_ARCHITECTURES="arm64 x86_64" \
  "${SCRIPT_DIR}/verify-package.sh" "${APP_DIR}"

mkdir -p "${DIST_DIR}"
rm -f "${FINAL_ARCHIVE}" "${FINAL_ARCHIVE}.sha256"
ditto \
  -c \
  -k \
  --sequesterRsrc \
  --keepParent \
  "${APP_DIR}" \
  "${FINAL_ARCHIVE}"

EXTRACT_DIR="${WORK_DIR}/final-archive"
mkdir -p "${EXTRACT_DIR}"
ditto -x -k "${FINAL_ARCHIVE}" "${EXTRACT_DIR}"
EXTRACTED_APP="${EXTRACT_DIR}/$(basename "${APP_DIR}")"
if [[ ! -d "${EXTRACTED_APP}" ]]; then
  echo "ERROR: final archive does not contain the expected application." >&2
  exit 1
fi
ELCHANGO_VERIFY_DISTRIBUTION=1 \
ELCHANGO_VERIFY_NOTARIZATION=1 \
ELCHANGO_EXPECTED_ARCHITECTURES="arm64 x86_64" \
  "${SCRIPT_DIR}/verify-package.sh" "${EXTRACTED_APP}"

(
  cd "${DIST_DIR}"
  shasum -a 256 "${FINAL_NAME}" >"${FINAL_NAME}.sha256"
)

echo "Notarization accepted: ${submission_id}"
echo "Packaged ${FINAL_ARCHIVE}"
