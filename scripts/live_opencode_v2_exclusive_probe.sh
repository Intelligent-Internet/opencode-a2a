#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
binary="${OPENCODE_BIN:-$(command -v opencode)}"
[[ "$("${binary}" --version)" == "opencode v2.0.28" ]] || { echo 'OpenCode v2.0.28 required' >&2; exit 1; }
[[ -n "${OPENAI_API_KEY:-}" ]] || { echo 'OPENAI_API_KEY required (not printed)' >&2; exit 1; }
image="${V2_PROBE_IMAGE:-node:22-bookworm-slim}"
docker image inspect "${image}" >/dev/null
run="$(mktemp -d /tmp/opencode/v2-exclusive.XXXXXX)"
chmod 700 "${run}"
umask 077
container=""
cleanup() {
  local result="$?"
  if [[ -n "${container}" ]]; then
    if [[ "${result}" != 0 ]]; then docker logs "${container}" > "${run}/server.log" 2>&1; fi
    docker rm -f "${container}" >/dev/null
  fi
  rm -f "${run}/key" "${run}/password" "${run}/a2a-token" "${run}/other-token"
  if [[ "${result}" == 0 ]]; then rm -rf "${run}"; fi
  exit "${result}"
}
trap cleanup EXIT
mkdir -p "${run}/workspace" "${run}/home"
printf '%s' 'SAFE_FILE_CONTENT' > "${run}/workspace/acceptance.txt"
printf '%s' 'OUTSIDE_FILE_CONTENT' > "${run}/forbidden.txt"
mkdir -p "${run}/home/.config/opencode"
printf '%s' '{"model":"openai/gpt-6-luna-fast","warming":false,"experimental":{"subagent_depth":0}}' > "${run}/home/.config/opencode/opencode.json"
printf '%s' "${OPENAI_API_KEY}" > "${run}/key"
if [[ "${V2_PROBE_CASE:-normal}" == "provider-failure" ]]; then
  printf '%s' 'intentionally-invalid-test-provider-key' > "${run}/key"
fi
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "${run}/password"
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "${run}/a2a-token"
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "${run}/other-token"
container="$(docker run -d --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,source=${binary},target=/usr/local/bin/opencode,readonly" \
  --mount "type=bind,source=${run},target=/probe" \
  --mount "type=bind,source=${root},target=/repo" \
  --mount "type=bind,source=$(command -v uv),target=/usr/local/bin/uv,readonly" \
  --workdir /probe/workspace \
  --env HOME=/probe/home --env XDG_CONFIG_HOME=/probe/home/.config \
  --env XDG_DATA_HOME=/probe/home/.local/share --env XDG_CACHE_HOME=/probe/home/.cache \
  "${image}" sh -c '
    export OPENAI_API_KEY="$(cat /probe/key)"
    export OPENCODE_SERVER_PASSWORD="$(cat /probe/password)"
    export OPENCODE_SERVER_USERNAME=opencode
    exec opencode serve --hostname 127.0.0.1 --port 4096
  ')"
echo "[exclusive-probe] private runtime ${run}" >&2
docker exec -i "${container}" node --input-type=module <<'JS'
import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
const password = (await readFile("/probe/password", "utf8")).trim();
const headers = { Authorization: `Basic ${Buffer.from(`opencode:${password}`).toString("base64")}` };
let ready = false;
for (let attempt = 0; attempt < 100; attempt++) {
  try {
    const response = await fetch("http://127.0.0.1:4096/api/info", { headers, signal: AbortSignal.timeout(1000) });
    if (response.ok && (await response.json()).version === "2.0.28") { ready = true; break; }
  } catch (error) { if (error.name !== "TypeError" && error.name !== "TimeoutError") throw error; }
  await new Promise(resolve => setTimeout(resolve, 200));
}
assert.ok(ready, "private pinned upstream did not become ready");
JS
# No published port. A peer in the same bridge still cannot reach loopback.
address="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${container}")"
python3 - "${address}" <<'PY'
import socket
import sys
with socket.socket() as connection:
    connection.settimeout(2)
    if connection.connect_ex((sys.argv[1], 4096)) == 0:
        raise SystemExit("Host process reached the private upstream listener")
PY
docker run --rm node:22-bookworm-slim node --input-type=module -e '
  try { await fetch(`http://${process.argv[1]}:4096/api/info`, {signal:AbortSignal.timeout(2000)}); process.exit(1); }
  catch { console.log("[exclusive-probe] independent network peer cannot reach upstream listener"); }
' "${address}" >&2
if [[ "${V2_PROBE_NATIVE:-1}" == 1 ]]; then
  docker exec -i "${container}" node --input-type=module < "${root}/scripts/probes/v2_exclusive.mjs"
fi
if [[ "${V2_PROBE_A2A:-0}" == 1 ]]; then
  docker exec -d --env "V2_PROBE_CASE=${V2_PROBE_CASE:-normal}" "${container}" \
    node /repo/scripts/probes/v2_fault_proxy.mjs
  docker exec -d --env "V2_PROBE_CASE=${V2_PROBE_CASE:-normal}" "${container}" sh -c '
    export UV_PROJECT_ENVIRONMENT=/probe/venv UV_CACHE_DIR=/probe/uv-cache
    export OPENCODE_API_VERSION=v2 OPENCODE_V2_EXECUTION=exclusive
    export OPENCODE_BASE_URL=http://127.0.0.1:4097 OPENCODE_WORKSPACE_ROOT=/probe/workspace
    export OPENCODE_TIMEOUT_STREAM=30
    if [ "${V2_PROBE_CASE}" = expired-intervention ]; then export A2A_INTERRUPT_REQUEST_TTL_SECONDS=1; fi
    export OPENCODE_AUTH_PASSWORD="$(cat /probe/password)"
    export A2A_STATIC_AUTH_CREDENTIALS="[{\"scheme\":\"bearer\",\"token\":\"$(cat /probe/a2a-token)\",\"principal\":\"probe\"},{\"scheme\":\"bearer\",\"token\":\"$(cat /probe/other-token)\",\"principal\":\"other\"}]"
    export A2A_HOST=127.0.0.1 A2A_PORT=8000 A2A_PUBLIC_URL=http://127.0.0.1:8000
    export A2A_TASK_STORE_DATABASE_URL=sqlite+aiosqlite:////probe/a2a.db
    exec uv run --project /repo --frozen opencode-a2a serve >/probe/gateway.log 2>&1
  '
  docker exec -i --env "V2_PROBE_CASE=${V2_PROBE_CASE:-normal}" "${container}" node --input-type=module < "${root}/scripts/probes/v2_a2a.mjs"
fi
