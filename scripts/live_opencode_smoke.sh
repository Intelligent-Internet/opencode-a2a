#!/usr/bin/env bash
# Run a live opencode-a2a integration smoke against a real OpenCode runtime.
# Everything runs inside a temporary, isolated environment: a throwaway HOME/XDG
# for the upstream OpenCode process, a throwaway workspace directory, and
# ephemeral ports. No repository data directory or user OpenCode state is read
# or written, and all artifacts are removed on exit.
set -euo pipefail

# shellcheck source=./health_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"

usage() {
  cat <<'EOF'
Usage:
  bash ./scripts/live_opencode_smoke.sh

Purpose:
  Boot a real `opencode serve` (hardened with a server password), start the
  opencode-a2a runtime against it, and verify the adapter's upstream
  adaptation end-to-end: authenticated upstream connectivity, JSON-RPC
  extension read methods, a streaming prompt round-trip, and that upstream
  auth is actually enforced.

Isolation:
  The script creates a fresh temporary root (mktemp -d) used for the OpenCode
  process HOME/XDG directories, the OpenCode workspace, and the adapter's
  working directory (SQLite state). Everything is removed on exit; no valid
  data directory is touched.

Requirements:
  - opencode CLI on PATH (or OPENCODE_BIN), any 1.18.x build
  - uv, git, curl, python3

Selected environment variables:
  OPENCODE_BIN                         Override the opencode binary path (default: opencode from PATH)
  LIVE_SMOKE_UPSTREAM_PORT             Fixed upstream port (default: ephemeral)
  LIVE_SMOKE_A2A_PORT                  Fixed adapter port (default: ephemeral)
  LIVE_SMOKE_SKIP_PROMPT=1             Skip the streaming prompt round-trip (network/model dependent)
  LIVE_SMOKE_SKIP_SYNC=1               Skip uv sync for this repository
  LIVE_SMOKE_KEEP_ARTIFACTS=1          Keep the temporary root and print its path
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "$#" -gt 0 ]]; then
  echo "Expected no positional arguments" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found in PATH" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git not found in PATH" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found in PATH" >&2
  exit 1
fi

opencode_bin="${OPENCODE_BIN:-}"
if [[ -z "${opencode_bin}" ]]; then
  if command -v opencode >/dev/null 2>&1; then
    opencode_bin="$(command -v opencode)"
  else
    echo "opencode CLI not found in PATH; install the 1.18.x line or set OPENCODE_BIN." >&2
    exit 1
  fi
fi

opencode_version="$("${opencode_bin}" --version 2>/dev/null | tr -d '[:space:]')"
if [[ -z "${opencode_version}" ]]; then
  echo "Failed to read opencode version from ${opencode_bin}" >&2
  exit 1
fi
echo "[live-smoke] opencode binary: ${opencode_bin} (${opencode_version})"

if [[ "${LIVE_SMOKE_SKIP_SYNC:-0}" != "1" ]]; then
  run_shared_repo_health_prerequisites "live-smoke"
fi

free_port() {
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

upstream_port="${LIVE_SMOKE_UPSTREAM_PORT:-$(free_port)}"
a2a_port="${LIVE_SMOKE_A2A_PORT:-$(free_port)}"
upstream_url="http://127.0.0.1:${upstream_port}"
a2a_url="http://127.0.0.1:${a2a_port}"
upstream_username="opencode"
# Non-secret throwaway credential for the isolated smoke instance only.
upstream_password="live-smoke-password"  # pragma: allowlist secret
a2a_bearer_token="live-smoke-token"  # pragma: allowlist secret

run_dir="$(mktemp -d)"
opencode_home="${run_dir}/opencode-home"
workspace_dir="${run_dir}/workspace"
adapter_dir="${run_dir}/adapter"
upstream_log="${run_dir}/opencode.log"
adapter_log="${run_dir}/adapter.log"
stream_result="${run_dir}/stream-result.jsonl"
mkdir -p "${opencode_home}" "${workspace_dir}" "${adapter_dir}"

cleanup() {
  local exit_code="$1"
  if [[ -n "${adapter_pid:-}" ]] && kill -0 "${adapter_pid}" >/dev/null 2>&1; then
    kill "${adapter_pid}" >/dev/null 2>&1 || true
    wait "${adapter_pid}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${upstream_pid:-}" ]] && kill -0 "${upstream_pid}" >/dev/null 2>&1; then
    kill "${upstream_pid}" >/dev/null 2>&1 || true
    wait "${upstream_pid}" >/dev/null 2>&1 || true
  fi
  if [[ "${LIVE_SMOKE_KEEP_ARTIFACTS:-0}" == "1" ]]; then
    echo "[live-smoke] artifacts kept at ${run_dir}"
  else
    rm -rf "${run_dir}"
  fi
  exit "${exit_code}"
}

trap 'cleanup $?' EXIT

(
  cd "${workspace_dir}"
  git init -q
  printf 'live smoke workspace\n' > README.md
  git add README.md
  git -c user.name="live-smoke" -c user.email="live-smoke@localhost" commit -qm init
)

echo "[live-smoke] starting upstream opencode on ${upstream_url}"
HOME="${opencode_home}" \
XDG_CONFIG_HOME="${opencode_home}/.config" \
XDG_DATA_HOME="${opencode_home}/.local/share" \
XDG_CACHE_HOME="${opencode_home}/.cache" \
OPENCODE_SERVER_PASSWORD="${upstream_password}" \
"${opencode_bin}" serve --hostname 127.0.0.1 --port "${upstream_port}" \
  >"${upstream_log}" 2>&1 &
upstream_pid="$!"

upstream_ready=""
for _ in $(seq 1 60); do
  if ! kill -0 "${upstream_pid}" >/dev/null 2>&1; then
    echo "[live-smoke] upstream opencode exited before becoming ready" >&2
    cat "${upstream_log}" >&2
    exit 1
  fi
  if curl -fsS --max-time 5 -u "${upstream_username}:${upstream_password}" \
    "${upstream_url}/doc" >/dev/null 2>&1; then
    upstream_ready="1"
    break
  fi
  sleep 0.5
done
if [[ -z "${upstream_ready}" ]]; then
  echo "[live-smoke] upstream opencode did not become ready at ${upstream_url}" >&2
  cat "${upstream_log}" >&2
  exit 1
fi
echo "[live-smoke] upstream opencode ready (${opencode_version})"

echo "[live-smoke] starting opencode-a2a on ${a2a_url}"
adapter_bin="${ROOT_DIR}/.venv/bin/opencode-a2a"
if [[ ! -x "${adapter_bin}" ]]; then
  echo "[live-smoke] ${adapter_bin} not found; run without LIVE_SMOKE_SKIP_SYNC=1 (or sync the repository first)." >&2
  exit 1
fi

OPENCODE_BASE_URL="${upstream_url}" \
OPENCODE_WORKSPACE_ROOT="${workspace_dir}" \
OPENCODE_AUTH_USERNAME="${upstream_username}" \
OPENCODE_AUTH_PASSWORD="${upstream_password}" \
A2A_STATIC_AUTH_CREDENTIALS="[{\"scheme\":\"bearer\",\"token\":\"${a2a_bearer_token}\",\"principal\":\"automation\"}]" \
A2A_HOST="127.0.0.1" \
A2A_PORT="${a2a_port}" \
A2A_PUBLIC_URL="${a2a_url}" \
A2A_TASK_STORE_DATABASE_URL="sqlite+aiosqlite:///${run_dir}/adapter/opencode-a2a.db" \
"${adapter_bin}" serve >"${adapter_log}" 2>&1 &
adapter_pid="$!"

a2a_ready=""
for _ in $(seq 1 60); do
  if ! kill -0 "${adapter_pid}" >/dev/null 2>&1; then
    echo "[live-smoke] adapter exited before becoming ready" >&2
    cat "${adapter_log}" >&2
    exit 1
  fi
  if curl -fsS --max-time 5 -H "Authorization: Bearer ${a2a_bearer_token}" \
    "${a2a_url}/health" >/dev/null 2>&1; then
    a2a_ready="1"
    break
  fi
  sleep 0.5
done
if [[ -z "${a2a_ready}" ]]; then
  echo "[live-smoke] adapter did not become ready at ${a2a_url}" >&2
  cat "${adapter_log}" >&2
  exit 1
fi
echo "[live-smoke] adapter ready"

auth_header="Authorization: Bearer ${a2a_bearer_token}"
extension_header="A2A-Extensions: urn:opencode-a2a:extension:session-management:v1, urn:opencode-a2a:extension:workspace-control:v1, urn:opencode-a2a:extension:provider-discovery:v1"

jsonrpc_probe() {
  local payload="$1"
  curl -sS --max-time 30 -H "${auth_header}" -H "${extension_header}" \
    -H "Content-Type: application/json" \
    -d "${payload}" "${a2a_url}/"
}

echo "[live-smoke] probe: upstream auth is enforced"
unauth_status="$(
  curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
    "${upstream_url}/session/status?directory=${workspace_dir}"
)"
if [[ "${unauth_status}" != "401" ]]; then
  echo "[live-smoke] FAIL: upstream /session/status without credentials returned ${unauth_status}, expected 401" >&2
  exit 1
fi
echo "[live-smoke] PASS: upstream auth enforced (401 without credentials)"

echo "[live-smoke] probe: opencode.projects.current"
projects_result="$(jsonrpc_probe '{"jsonrpc":"2.0","id":1,"method":"opencode.projects.current","params":{}}')"
if ! python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if isinstance(d.get("result", {}).get("item", {}).get("id"), str) else 1)' \
  <<<"${projects_result}"; then
  echo "[live-smoke] FAIL: opencode.projects.current returned unexpected result: ${projects_result}" >&2
  exit 1
fi
echo "[live-smoke] PASS: opencode.projects.current"

echo "[live-smoke] probe: opencode.sessions.status"
sessions_result="$(jsonrpc_probe '{"jsonrpc":"2.0","id":2,"method":"opencode.sessions.status","params":{}}')"
if ! python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if "items" in d.get("result", {}) else 1)' \
  <<<"${sessions_result}"; then
  echo "[live-smoke] FAIL: opencode.sessions.status returned unexpected result: ${sessions_result}" >&2
  exit 1
fi
echo "[live-smoke] PASS: opencode.sessions.status"

if [[ "${LIVE_SMOKE_SKIP_PROMPT:-0}" != "1" ]]; then
  echo "[live-smoke] probe: streaming prompt round-trip"
  curl -sS -N \
    -H "${auth_header}" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":10,"method":"SendStreamingMessage","params":{"message":{"messageId":"live-smoke-msg","role":"ROLE_USER","parts":[{"text":"Reply with exactly: smoke-ok"}]}}}' \
    "${a2a_url}/" --max-time 180 >"${stream_result}"
  if ! grep -q '"TASK_STATE_COMPLETED"' "${stream_result}"; then
    echo "[live-smoke] FAIL: streaming prompt did not reach TASK_STATE_COMPLETED" >&2
    tail -c 2000 "${stream_result}" >&2
    exit 1
  fi
  if ! grep -q 'smoke-ok' "${stream_result}"; then
    echo "[live-smoke] FAIL: streaming prompt reply did not contain the expected text" >&2
    tail -c 2000 "${stream_result}" >&2
    exit 1
  fi
  echo "[live-smoke] PASS: streaming prompt round-trip"
else
  echo "[live-smoke] skip: streaming prompt round-trip (LIVE_SMOKE_SKIP_PROMPT=1)"
fi

echo "[live-smoke] ALL CHECKS PASSED (opencode ${opencode_version})"
