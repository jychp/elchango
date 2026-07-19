#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NOTARIZE_SCRIPT="${REPO_DIR}/macos-app/Scripts/notarize-app.sh"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

BIN_DIR="${WORK_DIR}/bin"
DIST_DIR="${WORK_DIR}/dist"
APP_DIR="${WORK_DIR}/elChango.app"
KEY_PATH="${WORK_DIR}/AuthKey_TEST.p8"
CALLS_PATH="${WORK_DIR}/calls"
VERIFY_PATH="${WORK_DIR}/verify-package"
mkdir -p "${BIN_DIR}" "${DIST_DIR}" "${APP_DIR}/Contents"
touch "${APP_DIR}/Contents/Info.plist" "${KEY_PATH}" "${CALLS_PATH}"

cat >"${BIN_DIR}/plutil" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "com.jychp.elchango"
SH

cat >"${BIN_DIR}/ditto" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
destination="${@: -1}"
if [[ "$1" == "-c" ]]; then
  printf 'archive\n' >"${destination}"
elif [[ "$1" == "-x" ]]; then
  mkdir -p "${destination}/elChango.app/Contents"
  touch "${destination}/elChango.app/Contents/Info.plist"
else
  mkdir -p "${destination}/Contents"
  touch "${destination}/Contents/Info.plist"
fi
SH

cat >"${BIN_DIR}/xcrun" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${MOCK_CALLS_PATH}"
if [[ "$1" == "notarytool" && "$2" == "submit" ]]; then
  printf '%s\n' '{"id":"submission-123","status":"In Progress"}'
elif [[ "$1" == "notarytool" && "$2" == "wait" ]]; then
  printf '{"id":"submission-123","status":"%s"}\n' "${MOCK_WAIT_STATUS:-Accepted}"
  [[ "${MOCK_WAIT_EXIT:-0}" == "0" ]]
elif [[ "$1" == "notarytool" && "$2" == "log" ]]; then
  printf '%s\n' '{"issues":[]}'
fi
SH

cat >"${VERIFY_PATH}" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
test -d "$1"
SH

chmod +x \
  "${BIN_DIR}/ditto" \
  "${BIN_DIR}/plutil" \
  "${BIN_DIR}/xcrun" \
  "${VERIFY_PATH}"

export APPLE_API_ISSUER_ID="issuer"
export APPLE_API_KEY_ID="TEST"
export APPLE_API_KEY_PATH="${KEY_PATH}"
export ELCHANGO_DIST_DIR="${DIST_DIR}"
export ELCHANGO_SOURCE_COMMIT="commit-123"
export ELCHANGO_VERIFY_PACKAGE_SCRIPT="${VERIFY_PATH}"
export MOCK_CALLS_PATH="${CALLS_PATH}"
export PATH="${BIN_DIR}:${PATH}"

PENDING_ARCHIVE="${DIST_DIR}/elChango-notarization-pending.zip"
RECEIPT_PATH="${DIST_DIR}/elChango-notarization-receipt.json"

"${NOTARIZE_SCRIPT}" submit \
  "${APP_DIR}" \
  "${PENDING_ARCHIVE}" \
  "${RECEIPT_PATH}"

python3 - "${RECEIPT_PATH}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    receipt = json.load(handle)
assert receipt["schema_version"] == 1
assert receipt["submission_id"] == "submission-123"
assert receipt["source_commit"] == "commit-123"
assert len(receipt["archive_sha256"]) == 64
PY

MOCK_WAIT_STATUS="In Progress" MOCK_WAIT_EXIT=1 \
  "${NOTARIZE_SCRIPT}" finish \
    "${PENDING_ARCHIVE}" \
    "${RECEIPT_PATH}" &&
  {
    echo "ERROR: timed-out notarization unexpectedly succeeded." >&2
    exit 1
  }

[[ "$(
  awk '/notarytool submit/ {count += 1} END {print count + 0}' "${CALLS_PATH}"
)" == "1" ]]

MOCK_WAIT_STATUS="Accepted" MOCK_WAIT_EXIT=0 \
  "${NOTARIZE_SCRIPT}" finish \
    "${PENDING_ARCHIVE}" \
    "${RECEIPT_PATH}"
MOCK_WAIT_STATUS="Accepted" MOCK_WAIT_EXIT=0 \
  "${NOTARIZE_SCRIPT}" finish \
    "${PENDING_ARCHIVE}" \
    "${RECEIPT_PATH}"
[[ "$(
  awk '/notarytool submit/ {count += 1} END {print count + 0}' "${CALLS_PATH}"
)" == "1" ]]

cp "${PENDING_ARCHIVE}" "${PENDING_ARCHIVE}.valid"
printf 'tampered\n' >>"${PENDING_ARCHIVE}"
"${NOTARIZE_SCRIPT}" finish \
  "${PENDING_ARCHIVE}" \
  "${RECEIPT_PATH}" &&
  {
    echo "ERROR: a receipt checksum mismatch unexpectedly succeeded." >&2
    exit 1
  }
mv "${PENDING_ARCHIVE}.valid" "${PENDING_ARCHIVE}"

MOCK_WAIT_STATUS="Invalid" MOCK_WAIT_EXIT=1 \
  "${NOTARIZE_SCRIPT}" finish \
    "${PENDING_ARCHIVE}" \
    "${RECEIPT_PATH}" &&
  {
    echo "ERROR: rejected notarization unexpectedly succeeded." >&2
    exit 1
  }
awk '/notarytool log submission-123/ {found = 1} END {exit found ? 0 : 1}' \
  "${CALLS_PATH}"

python3 - "${RECEIPT_PATH}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    receipt = json.load(handle)
receipt["source_commit"] = "different-commit"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(receipt, handle)
PY
"${NOTARIZE_SCRIPT}" finish \
  "${PENDING_ARCHIVE}" \
  "${RECEIPT_PATH}" &&
  {
    echo "ERROR: a receipt source mismatch unexpectedly succeeded." >&2
    exit 1
  }

echo "Notarization script tests passed."
