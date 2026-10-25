#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash ./scripts/live_opencode_v2_session_smoke.sh

Purpose:
  Start an isolated, password-protected OpenCode 2.0.28 server and exercise
  the application-selected V2 adapter's /api/info, /api/location, and session
  create/get/list routes. This smoke does not submit a model prompt.

Optional environment:
  OPENCODE_BIN             OpenCode executable (default: opencode from PATH)
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
opencode_bin="${OPENCODE_BIN:-opencode}"
if ! command -v "${opencode_bin}" >/dev/null 2>&1; then
  echo "OpenCode CLI not found: ${opencode_bin}" >&2
  exit 1
fi
for required_command in curl python3 uv; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "${required_command} not found in PATH" >&2
    exit 1
  fi
done

opencode_version="$("${opencode_bin}" --version 2>/dev/null | tr -d '[:space:]')"
if [[ "${opencode_version}" != "opencodev2.0.28" ]]; then
  echo "Expected OpenCode 2.0.28; found ${opencode_version}" >&2
  exit 1
fi

free_port() {
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

run_dir="$(mktemp -d "${TMPDIR:-/tmp}/opencode-a2a-v2-session.XXXXXX")"
workspace_dir="${run_dir}/workspace"
home_dir="${run_dir}/home"
mkdir -p "${workspace_dir}" "${home_dir}/.config" "${home_dir}/.local/share" "${home_dir}/.cache"
adapter_home="${run_dir}/adapter-home"
mkdir -p "${adapter_home}/.config" "${adapter_home}/.cache" "${run_dir}/uv-cache"
upstream_port="$(free_port)"
upstream_url="http://127.0.0.1:${upstream_port}"
upstream_username="opencode"
read -r upstream_password inbound_token < <(
  python3 -c 'import secrets; print(secrets.token_urlsafe(32), secrets.token_urlsafe(32))'
)
upstream_log="${run_dir}/opencode.log"
credentials_file="${run_dir}/smoke-credentials.json"
server_password_file="${run_dir}/server-password"
curl_auth_config="${run_dir}/curl-auth.conf"
curl_invalid_auth_config="${run_dir}/curl-invalid-auth.conf"
server_pid=""
umask 077
printf '%s' "${upstream_password}" >"${server_password_file}"
printf '%s\0' "${upstream_url}" "${workspace_dir}" "${upstream_username}" \
  "${upstream_password}" "${inbound_token}" |
  python3 -c '
import json
import os
import sys
from pathlib import Path

values = sys.stdin.buffer.read().split(b"\0")
if values[-1] == b"":
    values.pop()
if len(values) != 5:
    raise RuntimeError("Expected five V2 smoke configuration values")
configuration = dict(
    zip(
        ("base_url", "workspace", "username", "upstream_password", "inbound_token"),
        (value.decode("utf-8") for value in values),
        strict=True,
    )
)
path = Path(sys.argv[1])
with path.open("x", encoding="utf-8") as credentials:
    json.dump(configuration, credentials)
    credentials.flush()
    os.fchmod(credentials.fileno(), 0o600)
' \
  "${credentials_file}"
python3 - "${credentials_file}" "${curl_auth_config}" "${curl_invalid_auth_config}" <<'PY'
import json
import os
import sys
from pathlib import Path

configuration = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
auth_settings = (
    (Path(sys.argv[2]), f"user = \"{configuration['username']}:{configuration['upstream_password']}\"\n"),
    (
        Path(sys.argv[3]),
        f"user = \"{configuration['username']}:invalid-{configuration['inbound_token']}\"\n",
    ),
)
for path, contents in auth_settings:
    with path.open("x", encoding="utf-8") as auth_file:
        auth_file.write(contents)
        auth_file.flush()
        os.fchmod(auth_file.fileno(), 0o600)
PY

cleanup() {
  local exit_code="$1"
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" >/dev/null 2>&1; then
    kill "${server_pid}" >/dev/null 2>&1 || true
    wait "${server_pid}" >/dev/null 2>&1 || true
  fi
  rm -rf "${run_dir}"
  exit "${exit_code}"
}
trap 'cleanup $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "[v2-session-smoke] starting OpenCode 2.0.28 on an ephemeral localhost port"
env -i \
  PATH="${PATH}" \
  HOME="${home_dir}" \
  XDG_CONFIG_HOME="${home_dir}/.config" \
  XDG_DATA_HOME="${home_dir}/.local/share" \
  XDG_CACHE_HOME="${home_dir}/.cache" \
  TMPDIR="${run_dir}" \
  SERVER_PASSWORD_FILE="${server_password_file}" \
  OPENCODE_SERVER_USERNAME="${upstream_username}" \
  bash -c '
    export OPENCODE_SERVER_PASSWORD="$(<"${SERVER_PASSWORD_FILE}")"
    exec "$@"
  ' _ "${opencode_bin}" serve --hostname 127.0.0.1 --port "${upstream_port}" \
  >"${upstream_log}" 2>&1 &
server_pid="$!"

upstream_ready=""
for _ in $(seq 1 60); do
  if ! kill -0 "${server_pid}" >/dev/null 2>&1; then
    echo "[v2-session-smoke] OpenCode exited before becoming ready" >&2
    exit 1
  fi
  if curl --config "${curl_auth_config}" -fsS --max-time 2 \
    "${upstream_url}/api/info" >/dev/null 2>&1; then
    upstream_ready="1"
    break
  fi
  sleep 0.25
done
if [[ -z "${upstream_ready}" ]]; then
  echo "[v2-session-smoke] OpenCode did not become ready" >&2
  exit 1
fi

auth_status="$(curl --config "${curl_invalid_auth_config}" -sS --max-time 2 \
  -o /dev/null -w '%{http_code}' "${upstream_url}/api/info")"
if [[ "${auth_status}" != "401" ]]; then
  echo "[v2-session-smoke] expected invalid credentials to return 401; got ${auth_status}" >&2
  exit 1
fi

env -i \
  PATH="${PATH}" \
  HOME="${adapter_home}" \
  XDG_CONFIG_HOME="${adapter_home}/.config" \
  XDG_CACHE_HOME="${adapter_home}/.cache" \
  TMPDIR="${run_dir}" \
  UV_CACHE_DIR="${run_dir}/uv-cache" \
  UV_PROJECT_ENVIRONMENT="${run_dir}/.venv" \
  V2_SMOKE_CREDENTIALS_FILE="${credentials_file}" \
  uv run --project "${ROOT_DIR}" python - <<'PY'
import asyncio
import json
import os
from pathlib import Path

from opencode_a2a.config import Settings, StaticAuthCredentialSettings
from opencode_a2a.server.application import create_app
from opencode_a2a.upstream.v2 import V2UpstreamAdapter


async def main() -> None:
    credentials = json.loads(
        Path(os.environ["V2_SMOKE_CREDENTIALS_FILE"]).read_text(encoding="utf-8")
    )
    workspace = Path(credentials["workspace"])
    settings = Settings(
        _env_file=None,
        opencode_base_url=credentials["base_url"],
        opencode_workspace_root=str(workspace),
        opencode_auth_username=credentials["username"],
        opencode_auth_password=credentials["upstream_password"],
        a2a_task_store_backend="memory",
        a2a_static_auth_credentials=(
            StaticAuthCredentialSettings(
                scheme="bearer",
                token=credentials["inbound_token"],
                principal="v2-session-smoke",
            ),
        ),
    )
    app = create_app(settings)
    upstream = app.state.upstream_client
    if settings.opencode_api_version != "v2" or settings.opencode_v2_execution != "disabled":
        raise RuntimeError("Default V2 transport must retain the execution opt-in gate")
    if not isinstance(upstream, V2UpstreamAdapter):
        raise RuntimeError("Application did not select the V2 upstream adapter")

    try:
        server_info = await upstream.get_server_info()
        if server_info.get("version") != "2.0.28":
            raise RuntimeError("V2 /api/info reported an unexpected version")
        location = await upstream.get_location(directory=str(workspace))
        session_id = await upstream.create_session("V2 session smoke", directory=str(workspace))
        session = await upstream.get_session(session_id, directory=str(workspace))
        sessions = await upstream.list_sessions(
            directory=str(workspace),
            params={"limit": 5},
        )
        if location.get("directory") != str(workspace):
            raise RuntimeError("V2 location did not preserve the selected workspace")
        if session.get("id") != session_id:
            raise RuntimeError("V2 session lookup returned a different session")
        if not any(item.get("id") == session_id for item in sessions.get("data", [])):
            raise RuntimeError("V2 session list did not include the created session")
    finally:
        await upstream.close()
        await app.state.a2a_client_manager.close_all()


asyncio.run(main())
PY

echo "[v2-session-smoke] Basic-auth rejection: passed"
echo "[v2-session-smoke] /api/info, /api/location, session create/get/list: passed"
echo "[v2-session-smoke] no model prompt was submitted"
