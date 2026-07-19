#!/usr/bin/env bash

set -euo pipefail

APP_DIR="${1:-}"
PROFILE="${ELCHANGO_PROFILE:-stable}"
SIGN_MODE="${ELCHANGO_SIGN_MODE:-adhoc}"
SIGN_IDENTITY="${ELCHANGO_CODESIGN_IDENTITY:--}"

if [[ -z "${APP_DIR}" || ! -d "${APP_DIR}" ]]; then
  echo "Usage: $0 /path/to/elChango.app" >&2
  exit 2
fi

case "${PROFILE}" in
  stable)
    BUNDLE_IDENTIFIER="com.jychp.elchango"
    HOOK_IDENTIFIER="com.jychp.elchango.hook-reporter"
    HOOK_EXECUTABLE_NAME="elChangoHookReporter"
    ;;
  debug)
    BUNDLE_IDENTIFIER="com.jychp.elchango.debug"
    HOOK_IDENTIFIER="com.jychp.elchango.debug.hook-reporter"
    HOOK_EXECUTABLE_NAME="elChangoHookReporter-debug"
    ;;
  *)
    echo "ERROR: ELCHANGO_PROFILE must be 'stable' or 'debug'." >&2
    exit 2
    ;;
esac

case "${SIGN_MODE}" in
  adhoc|identity|developer-id) ;;
  *)
    echo "ERROR: ELCHANGO_SIGN_MODE must be 'adhoc', 'identity', or 'developer-id'." >&2
    exit 2
    ;;
esac
if [[ "${SIGN_MODE}" == "developer-id" && "${PROFILE}" != "stable" ]]; then
  echo "ERROR: the debug profile cannot use Developer ID signing." >&2
  exit 2
fi
if [[ "${SIGN_MODE}" != "adhoc" && -z "${SIGN_IDENTITY}" ]]; then
  echo "ERROR: ELCHANGO_CODESIGN_IDENTITY is required." >&2
  exit 2
fi

HOOK_REPORTER="${APP_DIR}/Contents/MacOS/${HOOK_EXECUTABLE_NAME}"
if [[ ! -x "${HOOK_REPORTER}" ]]; then
  echo "ERROR: native hook reporter is missing." >&2
  exit 2
fi

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

sign_path "${HOOK_IDENTIFIER}" "${HOOK_REPORTER}"
sign_path "${BUNDLE_IDENTIFIER}" "${APP_DIR}"
