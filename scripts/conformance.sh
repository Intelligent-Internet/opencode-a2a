#!/usr/bin/env bash
# Run repository-owned black-box A2A compatibility probes.
set -euo pipefail

# shellcheck source=./health_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"

usage() {
  cat <<'EOF'
Usage:
  bash ./scripts/conformance.sh

Purpose:
  Run repository-owned A2A 1.0 black-box compatibility probes.
  This script does not download or depend on an external TCK.

Selected environment variables:
  CONFORMANCE_OUTPUT_DIR       Artifact directory (default: run/conformance/<timestamp>)
  CONFORMANCE_SUT_URL          Probe an already running runtime instead of a local test SUT
  CONFORMANCE_SUT_PORT         Local test SUT port (default: 8011)
  CONFORMANCE_AUTH_TOKEN       Bearer token (default: test-token for the local test SUT)
  CONFORMANCE_ALLOW_EXTERNAL=1 Required acknowledgement when CONFORMANCE_SUT_URL is set
  CONFORMANCE_SKIP_REPO_SYNC=1 Skip uv sync/uv pip check
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ "$#" -ne 0 ]]; then
  echo "This entrypoint accepts no positional arguments." >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_dir="${CONFORMANCE_OUTPUT_DIR:-${ROOT_DIR}/run/conformance/${timestamp}}"
sut_port="${CONFORMANCE_SUT_PORT:-8011}"
sut_log="${output_dir}/sut.log"
probe_log="${output_dir}/probe.log"

mkdir -p "${output_dir}"

cleanup() {
  local exit_code="$1"
  if [[ -n "${sut_pid:-}" ]] && kill -0 "${sut_pid}" >/dev/null 2>&1; then
    kill "${sut_pid}" >/dev/null 2>&1 || true
    wait "${sut_pid}" >/dev/null 2>&1 || true
  fi
  exit "${exit_code}"
}
trap 'cleanup $?' EXIT

cd "${ROOT_DIR}"
if [[ "${CONFORMANCE_SKIP_REPO_SYNC:-0}" != "1" ]]; then
  run_shared_repo_health_prerequisites "conformance" >"${output_dir}/repo-health.log" 2>&1
fi

sut_url="${CONFORMANCE_SUT_URL:-}"
auth_token="${CONFORMANCE_AUTH_TOKEN:-}"
if [[ -z "${sut_url}" ]]; then
  sut_url="http://127.0.0.1:${sut_port}"
  auth_token="${auth_token:-test-token}"
  if ! uv run python -c \
    'import socket, sys; server = socket.create_server(("127.0.0.1", int(sys.argv[1]))); server.close()' \
    "${sut_port}"; then
    echo "Local conformance port ${sut_port} is unavailable; choose CONFORMANCE_SUT_PORT." >&2
    exit 1
  fi
  CONFORMANCE_SUT_PORT="${sut_port}" \
  CONFORMANCE_SUT_URL="${sut_url}" \
  CONFORMANCE_AUTH_TOKEN="${auth_token}" \
    uv run python -m scripts.conformance_sut >"${sut_log}" 2>&1 &
  sut_pid="$!"

  sut_ready=0
  for _ in $(seq 1 50); do
    if curl --silent --fail --output /dev/null \
      "${sut_url}/.well-known/agent-card.json" 2>/dev/null; then
      sut_ready=1
      break
    fi
    if ! kill -0 "${sut_pid}" >/dev/null 2>&1; then
      echo "Local conformance SUT exited before becoming ready." >&2
      cat "${sut_log}" >&2 || true
      exit 1
    fi
    sleep 0.2
  done
  if [[ "${sut_ready}" != "1" ]]; then
    echo "Local conformance SUT did not become ready at ${sut_url}." >&2
    cat "${sut_log}" >&2 || true
    exit 1
  fi
elif [[ "${CONFORMANCE_ALLOW_EXTERNAL:-0}" != "1" ]]; then
  echo "Set CONFORMANCE_ALLOW_EXTERNAL=1 to acknowledge external SUT side effects." >&2
  exit 2
elif [[ -z "${auth_token}" ]]; then
  echo "CONFORMANCE_AUTH_TOKEN is required with CONFORMANCE_SUT_URL." >&2
  exit 2
fi

set +e
CONFORMANCE_AUTH_TOKEN="${auth_token}" \
  uv run python -m scripts.conformance_probe \
    --base-url "${sut_url}" \
    --output-dir "${output_dir}" 2>&1 | tee "${probe_log}"
probe_exit="${PIPESTATUS[0]}"
set -e

echo "Conformance artifacts: ${output_dir}"
exit "${probe_exit}"
