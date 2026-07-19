#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${MACOS_DIR}/.." && pwd)"

DIST_DIR="${ELCHANGO_DIST_DIR:-${REPO_DIR}/dist}"
APP_VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
API_KEY_PATH="${APPLE_API_KEY_PATH:-}"
API_KEY_ID="${APPLE_API_KEY_ID:-}"
API_ISSUER_ID="${APPLE_API_ISSUER_ID:-}"
SOURCE_COMMIT="${ELCHANGO_SOURCE_COMMIT:-$(git -C "${REPO_DIR}" rev-parse HEAD)}"
NOTARY_TIMEOUT="${ELCHANGO_NOTARY_TIMEOUT:-30m}"
VERIFY_PACKAGE_SCRIPT="${ELCHANGO_VERIFY_PACKAGE_SCRIPT:-${SCRIPT_DIR}/verify-package.sh}"

FINAL_NAME="elChango-${APP_VERSION}-macos-universal.zip"
FINAL_ARCHIVE="${DIST_DIR}/${FINAL_NAME}"

require_commands() {
  local required_command
  for required_command in ditto git plutil python3 shasum xcrun; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
      echo "ERROR: required command '${required_command}' is unavailable." >&2
      exit 2
    fi
  done
}

require_credentials() {
  if [[ -z "${API_KEY_PATH}" || ! -f "${API_KEY_PATH}" ]]; then
    echo "ERROR: APPLE_API_KEY_PATH must reference an App Store Connect API key." >&2
    exit 2
  fi
  if [[ -z "${API_KEY_ID}" || -z "${API_ISSUER_ID}" ]]; then
    echo "ERROR: APPLE_API_KEY_ID and APPLE_API_ISSUER_ID are required." >&2
    exit 2
  fi
}

require_stable_app() {
  local app_dir="$1"
  if [[ ! -d "${app_dir}" ]]; then
    echo "ERROR: application bundle not found at ${app_dir}." >&2
    exit 2
  fi
  if [[ "$(
    plutil -extract CFBundleIdentifier raw -o - \
      "${app_dir}/Contents/Info.plist"
  )" != "com.jychp.elchango" ]]; then
    echo "ERROR: only the stable elChango profile can be notarized." >&2
    exit 2
  fi
}

notarytool() {
  xcrun notarytool "$@" \
    --key "${API_KEY_PATH}" \
    --key-id "${API_KEY_ID}" \
    --issuer "${API_ISSUER_ID}"
}

submit_notarization() {
  local app_dir="$1"
  local pending_archive="$2"
  local receipt_path="$3"
  local work_dir result_path submission_exit submission_id submission_status
  local archive_sha

  require_stable_app "${app_dir}"
  require_credentials
  mkdir -p "$(dirname "${pending_archive}")" "$(dirname "${receipt_path}")"
  rm -f "${pending_archive}" "${receipt_path}"
  ditto \
    -c \
    -k \
    --sequesterRsrc \
    --keepParent \
    "${app_dir}" \
    "${pending_archive}"
  archive_sha="$(shasum -a 256 "${pending_archive}" | awk '{print $1}')"

  work_dir="$(mktemp -d)"
  result_path="${work_dir}/submit-result.json"
  submission_exit=0
  notarytool submit "${pending_archive}" \
    --output-format json \
    >"${result_path}" || submission_exit=$?
  read -r submission_id submission_status < <(
    python3 - "${result_path}" <<'PY'
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
  rm -rf "${work_dir}"
  [[ "${submission_id}" == "-" ]] && submission_id=""
  [[ "${submission_status}" == "-" ]] && submission_status=""
  if [[ "${submission_exit}" -ne 0 || -z "${submission_id}" ]]; then
    echo "ERROR: Apple did not return a notarization submission ID." >&2
    exit 1
  fi

  python3 - \
    "${receipt_path}" \
    "${submission_id}" \
    "${submission_status}" \
    "${SOURCE_COMMIT}" \
    "${APP_VERSION}" \
    "${archive_sha}" \
    "$(basename "${pending_archive}")" <<'PY'
import json
import os
import sys
import tempfile

path, submission_id, status, source_commit, version, archive_sha, archive_name = (
    sys.argv[1:]
)
payload = {
    "schema_version": 1,
    "submission_id": submission_id,
    "submission_status": status,
    "source_commit": source_commit,
    "version": version,
    "archive_sha256": archive_sha,
    "archive_name": archive_name,
}
directory = os.path.dirname(path) or "."
descriptor, temporary = tempfile.mkstemp(prefix=".notary-receipt-", dir=directory)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

  echo "Submitted notarization: ${submission_id} (${submission_status:-unknown})"
  echo "Receipt: ${receipt_path}"
}

finish_notarization() (
  local pending_archive="$1"
  local receipt_path="$2"
  local work_dir result_path submission_exit submission_id submission_status
  local extracted_app expected_archive_sha actual_archive_sha receipt_commit

  require_credentials
  if [[ ! -f "${pending_archive}" || ! -f "${receipt_path}" ]]; then
    echo "ERROR: pending archive and notarization receipt are required." >&2
    exit 2
  fi

  read -r submission_id receipt_commit expected_archive_sha < <(
    python3 - "${receipt_path}" "${APP_VERSION}" "$(basename "${pending_archive}")" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    receipt = json.load(handle)
if receipt.get("schema_version") != 1:
    raise SystemExit("ERROR: unsupported notarization receipt schema.")
if receipt.get("version") != sys.argv[2]:
    raise SystemExit("ERROR: notarization receipt version does not match VERSION.")
if receipt.get("archive_name") != sys.argv[3]:
    raise SystemExit("ERROR: notarization receipt archive name does not match.")
print(
    receipt.get("submission_id") or "-",
    receipt.get("source_commit") or "-",
    receipt.get("archive_sha256") or "-",
)
PY
  )
  if [[ "${submission_id}" == "-" ||
        "${receipt_commit}" != "${SOURCE_COMMIT}" ||
        ! "${expected_archive_sha}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "ERROR: notarization receipt does not match the release source." >&2
    exit 1
  fi
  actual_archive_sha="$(shasum -a 256 "${pending_archive}" | awk '{print $1}')"
  if [[ "${actual_archive_sha}" != "${expected_archive_sha}" ]]; then
    echo "ERROR: pending archive checksum does not match its receipt." >&2
    exit 1
  fi

  work_dir="$(mktemp -d)"
  trap 'rm -rf "${work_dir}"' EXIT
  result_path="${work_dir}/wait-result.json"
  submission_exit=0
  notarytool wait "${submission_id}" \
    --timeout "${NOTARY_TIMEOUT}" \
    --output-format json \
    >"${result_path}" || submission_exit=$?
  submission_status="$(
    python3 - "${result_path}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        result = json.load(handle)
except (OSError, json.JSONDecodeError):
    result = {}
print(result.get("status") or "unknown")
PY
  )"
  if [[ "${submission_exit}" -ne 0 || "${submission_status}" != "Accepted" ]]; then
    echo "ERROR: Apple notarization status is '${submission_status}' for ${submission_id}." >&2
    notarytool log "${submission_id}" || true
    return 1
  fi

  mkdir -p "${work_dir}/pending"
  ditto -x -k "${pending_archive}" "${work_dir}/pending"
  extracted_app="${work_dir}/pending/elChango.app"
  require_stable_app "${extracted_app}"
  xcrun stapler staple "${extracted_app}"
  xcrun stapler validate "${extracted_app}"

  ELCHANGO_VERIFY_DISTRIBUTION=1 \
  ELCHANGO_VERIFY_NOTARIZATION=1 \
  ELCHANGO_EXPECTED_PROFILE=stable \
  ELCHANGO_EXPECTED_ARCHITECTURES="arm64 x86_64" \
    "${VERIFY_PACKAGE_SCRIPT}" "${extracted_app}"

  mkdir -p "${DIST_DIR}" "${MACOS_DIR}/dist"
  rm -rf "${MACOS_DIR}/dist/elChango.app"
  ditto "${extracted_app}" "${MACOS_DIR}/dist/elChango.app"
  rm -f "${FINAL_ARCHIVE}" "${FINAL_ARCHIVE}.sha256"
  ditto \
    -c \
    -k \
    --sequesterRsrc \
    --keepParent \
    "${extracted_app}" \
    "${FINAL_ARCHIVE}"

  mkdir -p "${work_dir}/final"
  ditto -x -k "${FINAL_ARCHIVE}" "${work_dir}/final"
  ELCHANGO_VERIFY_DISTRIBUTION=1 \
  ELCHANGO_VERIFY_NOTARIZATION=1 \
  ELCHANGO_EXPECTED_PROFILE=stable \
  ELCHANGO_EXPECTED_ARCHITECTURES="arm64 x86_64" \
    "${VERIFY_PACKAGE_SCRIPT}" \
      "${work_dir}/final/elChango.app"
  (
    cd "${DIST_DIR}"
    shasum -a 256 "${FINAL_NAME}" >"${FINAL_NAME}.sha256"
  )
  echo "Notarization accepted: ${submission_id}"
  echo "Packaged ${FINAL_ARCHIVE}"
)

require_commands
COMMAND="${1:-all}"
LEGACY_APP_DIR=""
if [[ "${COMMAND}" != "all" &&
      "${COMMAND}" != "submit" &&
      "${COMMAND}" != "finish" &&
      -d "${COMMAND}" ]]; then
  LEGACY_APP_DIR="${COMMAND}"
  COMMAND="all"
fi
case "${COMMAND}" in
  submit)
    submit_notarization \
      "${2:-${MACOS_DIR}/dist/elChango.app}" \
      "${3:-${DIST_DIR}/elChango-notarization-pending.zip}" \
      "${4:-${DIST_DIR}/elChango-notarization-receipt.json}"
    ;;
  finish)
    finish_notarization \
      "${2:-${DIST_DIR}/elChango-notarization-pending.zip}" \
      "${3:-${DIST_DIR}/elChango-notarization-receipt.json}"
    ;;
  all)
    PENDING_ARCHIVE="${DIST_DIR}/elChango-notarization-pending.zip"
    RECEIPT_PATH="${DIST_DIR}/elChango-notarization-receipt.json"
    submit_notarization \
      "${LEGACY_APP_DIR:-${2:-${MACOS_DIR}/dist/elChango.app}}" \
      "${PENDING_ARCHIVE}" \
      "${RECEIPT_PATH}"
    finish_notarization "${PENDING_ARCHIVE}" "${RECEIPT_PATH}"
    ;;
  *)
    echo "Usage: $0 [all|submit|finish] [paths...]" >&2
    exit 2
    ;;
esac
