#!/usr/bin/env bash
# Validate that a locally built wheel can be installed as a uv tool and serves /health.
set -euo pipefail

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

shopt -s nullglob
wheel_paths=(dist/opencode_a2a_server-*.whl)
shopt -u nullglob

if [[ "${#wheel_paths[@]}" -eq 0 ]]; then
  echo "No built wheel found in dist/" >&2
  exit 1
fi

wheel_path="$(ls -1t "${wheel_paths[@]}" | head -n 1)"

if [[ ! -f "${wheel_path}" ]]; then
  echo "Wheel path does not exist: ${wheel_path}" >&2
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
uv tool install "${wheel_path}" --python 3.13

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
"${tool_bin_dir}/opencode-a2a-server" >"${server_log}" 2>&1 &
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
