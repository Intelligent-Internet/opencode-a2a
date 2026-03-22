#!/usr/bin/env bash
# Validate that a locally built distribution artifact can be installed as a uv tool and serves /health.
set -euo pipefail

if [[ "$#" -gt 1 ]]; then
  echo "Expected at most one built artifact path argument" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found in PATH" >&2
  exit 1
fi

python_bin="${PYTHON_BIN:-}"
if [[ -z "${python_bin}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  else
    echo "python3 or python not found in PATH" >&2
    exit 1
  fi
fi

artifact_path="${1:-${SMOKE_TEST_ARTIFACT_PATH:-${SMOKE_TEST_WHEEL_PATH:-}}}"

if [[ -z "${artifact_path}" ]]; then
  shopt -s nullglob
  wheel_paths=(dist/opencode_a2a-*.whl)
  shopt -u nullglob

  if [[ "${#wheel_paths[@]}" -eq 0 ]]; then
    echo "No built wheel found in dist/" >&2
    exit 1
  fi

  if [[ "${#wheel_paths[@]}" -gt 1 ]]; then
    echo "Multiple built wheels found; pass an explicit artifact path or set SMOKE_TEST_ARTIFACT_PATH." >&2
    printf ' - %s\n' "${wheel_paths[@]}" >&2
    exit 1
  fi

  artifact_path="${wheel_paths[0]}"
fi

if [[ ! -f "${artifact_path}" ]]; then
  echo "Artifact path does not exist: ${artifact_path}" >&2
  exit 1
fi
tmpdir="$(mktemp -d)"
tool_dir="${tmpdir}/tools"
tool_bin_dir="${tmpdir}/bin"
server_log="${tmpdir}/server.log"

cleanup() {
  local exit_code="$1"
  if [[ -n "${server_pid:-}" ]] && kill -0 "${server_pid}" >/dev/null 2>&1; then
    kill "${server_pid}" >/dev/null 2>&1 || true
    wait "${server_pid}" >/dev/null 2>&1 || true
  fi
  rm -rf "${tmpdir}"
  exit "${exit_code}"
}

trap 'cleanup $?' EXIT

mkdir -p "${tool_dir}" "${tool_bin_dir}"

UV_TOOL_DIR="${tool_dir}" \
UV_TOOL_BIN_DIR="${tool_bin_dir}" \
uv tool install "${artifact_path}" --python "${python_bin}"

port="$(
  "${python_bin}" - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

bearer_token="smoke-test-token"

A2A_BEARER_TOKEN="${bearer_token}" \
A2A_PORT="${port}" \
A2A_HOST="127.0.0.1" \
"${tool_bin_dir}/opencode-a2a" >"${server_log}" 2>&1 &
server_pid="$!"

health_url="http://127.0.0.1:${port}/health"
for _ in $(seq 1 50); do
  if curl -fsS -H "Authorization: Bearer ${bearer_token}" "${health_url}" >/dev/null; then
    exit 0
  fi
  sleep 0.2
done

echo "CLI smoke test failed; server did not become healthy at ${health_url}" >&2
cat "${server_log}" >&2
exit 1
