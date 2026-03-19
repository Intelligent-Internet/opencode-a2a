#!/usr/bin/env bash
# Enable and start systemd services for a project.
# Usage: ./enable_instance.sh <project_name>
# Requires sudo to manage systemd services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../shell_helpers.sh
source "${SCRIPT_DIR}/../shell_helpers.sh"

PROJECT_NAME="${1:-}"

if [[ -z "$PROJECT_NAME" ]]; then
  echo "Usage: $0 <project_name>" >&2
  exit 1
fi

FORCE_RESTART="${FORCE_RESTART:-false}"
: "${DATA_ROOT:=/data/opencode-a2a}"
: "${A2A_HOST:=127.0.0.1}"
: "${A2A_PORT:=8000}"
: "${DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS:=30}"
: "${DEPLOY_HEALTHCHECK_INTERVAL_SECONDS:=1}"
A2A_HEALTHCHECK_URL="${A2A_HEALTHCHECK_URL:-}"
A2A_HEALTHCHECK_AUTH_HEADER_FILE=""

json_escape() {
  local value="${1//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

emit_status() {
  local status="$1"
  local category="$2"
  local detail="$3"
  printf '{"status":"%s","category":"%s","project":"%s","service":"%s","health_url":"%s","detail":"%s"}\n' \
    "$(json_escape "$status")" \
    "$(json_escape "$category")" \
    "$(json_escape "$PROJECT_NAME")" \
    "$(json_escape "opencode-a2a-server@${PROJECT_NAME}.service")" \
    "$(json_escape "${A2A_HEALTHCHECK_URL:-}")" \
    "$(json_escape "$detail")"
}

fail_with_status() {
  local exit_code="$1"
  local category="$2"
  local detail="$3"
  emit_status "error" "$category" "$detail" >&2
  exit "$exit_code"
}

cleanup_healthcheck_auth() {
  if [[ -n "$A2A_HEALTHCHECK_AUTH_HEADER_FILE" ]]; then
    rm -f "$A2A_HEALTHCHECK_AUTH_HEADER_FILE"
  fi
}

trap cleanup_healthcheck_auth EXIT

require_positive_integer() {
  local key="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [[ "$value" == "0" ]]; then
    fail_with_status 31 "invalid_argument" "${key} must be a positive integer, got: ${value}"
  fi
}

resolve_healthcheck_host() {
  case "${A2A_HOST}" in
    0.0.0.0|::|[::])
      echo "127.0.0.1"
      ;;
    *)
      echo "${A2A_HOST}"
      ;;
  esac
}

require_unit_active() {
  local unit="$1"
  if ! sudo systemctl is-active --quiet "$unit"; then
    sudo systemctl status "$unit" --no-pager >&2 || true
    fail_with_status 22 "systemd_not_active" "${unit} is not active after deploy"
  fi
}

resolve_healthcheck_bearer_token() {
  if [[ -n "${A2A_BEARER_TOKEN:-}" ]]; then
    printf '%s' "${A2A_BEARER_TOKEN}"
    return 0
  fi

  local secret_file="${DATA_ROOT}/${PROJECT_NAME}/config/a2a.secret.env"
  if ! sudo test -f "$secret_file"; then
    fail_with_status 25 "missing_runtime_secret" "Missing Bearer Token secret file for health probe: ${secret_file}"
  fi

  local token=""
  token="$(sudo sed -n 's/^A2A_BEARER_TOKEN=//p' "$secret_file" | head -n 1)"
  if [[ -z "$token" ]]; then
    fail_with_status 25 "missing_runtime_secret" "A2A_BEARER_TOKEN is not defined in ${secret_file}"
  fi

  printf '%s' "$token"
}

prepare_healthcheck_auth_header() {
  local token="$1"
  local header_file
  header_file="$(mktemp)"
  chmod 600 "$header_file"
  printf 'Authorization: Bearer %s\n' "$token" >"$header_file"
  A2A_HEALTHCHECK_AUTH_HEADER_FILE="$header_file"
}

wait_for_health() {
  local timeout="$1"
  local interval="$2"
  local elapsed=0
  local response=""
  while (( elapsed < timeout )); do
    require_unit_active "opencode@${PROJECT_NAME}.service"
    require_unit_active "opencode-a2a-server@${PROJECT_NAME}.service"
    if response="$(curl -fsS -H "@${A2A_HEALTHCHECK_AUTH_HEADER_FILE}" "$A2A_HEALTHCHECK_URL" 2>/dev/null)" && [[ "$response" == *'"status"'*'"ok"'* ]]; then
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  sudo systemctl status "opencode-a2a-server@${PROJECT_NAME}.service" --no-pager >&2 || true
  fail_with_status 23 "readiness_timeout" "Timed out waiting for ${A2A_HEALTHCHECK_URL}"
}

ensure_sudo_ready
require_positive_integer "DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS" "$DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS"
require_positive_integer "DEPLOY_HEALTHCHECK_INTERVAL_SECONDS" "$DEPLOY_HEALTHCHECK_INTERVAL_SECONDS"

if ! command -v curl >/dev/null 2>&1; then
  fail_with_status 24 "missing_dependency" "curl not found in PATH; cannot probe /health"
fi

A2A_HEALTHCHECK_URL="${A2A_HEALTHCHECK_URL:-http://$(resolve_healthcheck_host):${A2A_PORT}/health}"
prepare_healthcheck_auth_header "$(resolve_healthcheck_bearer_token)"

if ! sudo systemctl daemon-reload; then
  fail_with_status 20 "systemd_reload_failed" "systemctl daemon-reload failed"
fi

start_or_restart() {
  local unit="$1"
  if [[ "$FORCE_RESTART" == "true" ]]; then
    if sudo systemctl is-active --quiet "$unit"; then
      if ! sudo systemctl restart "$unit"; then
        fail_with_status 21 "systemd_start_failed" "systemctl restart failed for ${unit}"
      fi
    else
      if ! sudo systemctl enable --now "$unit"; then
        fail_with_status 21 "systemd_start_failed" "systemctl enable --now failed for ${unit}"
      fi
    fi
  else
    if ! sudo systemctl enable --now "$unit"; then
      fail_with_status 21 "systemd_start_failed" "systemctl enable --now failed for ${unit}"
    fi
  fi
}

start_or_restart "opencode@${PROJECT_NAME}.service"
start_or_restart "opencode-a2a-server@${PROJECT_NAME}.service"

wait_for_health "$DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS" "$DEPLOY_HEALTHCHECK_INTERVAL_SECONDS"
sudo systemctl status "opencode-a2a-server@${PROJECT_NAME}.service" --no-pager >&2
emit_status "ok" "ready" "systemd units active and /health returned ok"
