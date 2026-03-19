#!/usr/bin/env bash
# Create project directories and env files for systemd services.
# Usage: [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [ENABLE_SECRET_PERSISTENCE=true] ./setup_instance.sh <project_name>
# Requires env: DATA_ROOT, OPENCODE_BIND_HOST, OPENCODE_BIND_PORT, OPENCODE_LOG_LEVEL,
#               A2A_HOST, A2A_PORT, A2A_PUBLIC_URL.
# Optional provider secret env: see scripts/deploy/provider_secret_env_keys.sh
# Secret persistence is opt-in via ENABLE_SECRET_PERSISTENCE=true.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../shell_helpers.sh
source "${SCRIPT_DIR}/../shell_helpers.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/provider_secret_env_keys.sh"

PROJECT_NAME="${1:-}"

if [[ "$#" -ne 1 || -z "$PROJECT_NAME" ]]; then
  echo "Usage: [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [ENABLE_SECRET_PERSISTENCE=true] $0 <project_name>" >&2
  exit 1
fi

: "${DATA_ROOT:?}"
: "${OPENCODE_BIND_HOST:?}"
: "${OPENCODE_BIND_PORT:?}"
: "${OPENCODE_LOG_LEVEL:?}"
: "${A2A_HOST:?}"
: "${A2A_PORT:?}"
: "${A2A_PUBLIC_URL:?}"
: "${A2A_OTEL_INSTRUMENTATION_ENABLED:=false}"
: "${A2A_MAX_REQUEST_BODY_BYTES:=1048576}"
: "${A2A_CANCEL_ABORT_TIMEOUT_SECONDS:=2.0}"
: "${A2A_ENABLE_SESSION_SHELL:=false}"
: "${A2A_STRICT_ISOLATION:=false}"
: "${A2A_SYSTEMD_TASKS_MAX:=512}"
: "${A2A_SYSTEMD_LIMIT_NOFILE:=65536}"
: "${A2A_SYSTEMD_MEMORY_MAX:=}"
: "${A2A_SYSTEMD_CPU_QUOTA:=}"
: "${ENABLE_SECRET_PERSISTENCE:=false}"
: "${SERVICE_USER:?}"
: "${SERVICE_GROUP:=}"

PROJECT_DIR="${DATA_ROOT}/${PROJECT_NAME}"
WORKSPACE_DIR="${PROJECT_DIR}/workspace"
CONFIG_DIR="${PROJECT_DIR}/config"
OPENCODE_AUTH_ENV_FILE="${CONFIG_DIR}/opencode.auth.env"
OPENCODE_SECRET_ENV_FILE="${CONFIG_DIR}/opencode.secret.env"
A2A_SECRET_ENV_FILE="${CONFIG_DIR}/a2a.secret.env"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_DIR="${PROJECT_DIR}/run"
ASKPASS_SCRIPT="${RUN_DIR}/git-askpass.sh"
CACHE_DIR="${PROJECT_DIR}/.cache/opencode"
LOCAL_DIR="${PROJECT_DIR}/.local"
STATE_DIR="${LOCAL_DIR}/state"
OPENCODE_LOCAL_SHARE_DIR="${PROJECT_DIR}/.local/share/opencode"
OPENCODE_BIN_DIR="${OPENCODE_LOCAL_SHARE_DIR}/bin"
DATA_DIR="${PROJECT_DIR}/.local/share/opencode/storage/session"
SECRET_ENV_KEYS=("${PROVIDER_SECRET_ENV_KEYS[@]}")
SYSTEMD_UNIT_DIR="/etc/systemd/system"
OPENCODE_OVERRIDE_DIR="${SYSTEMD_UNIT_DIR}/opencode@${PROJECT_NAME}.service.d"
A2A_OVERRIDE_DIR="${SYSTEMD_UNIT_DIR}/opencode-a2a-server@${PROJECT_NAME}.service.d"

PERSIST_SECRETS="false" # pragma: allowlist secret
if is_truthy "${ENABLE_SECRET_PERSISTENCE}"; then
  PERSIST_SECRETS="true" # pragma: allowlist secret
fi

require_envfile_safe_value() {
  local key="$1"
  local value="$2"
  case "$value" in
    *$'\n'*|*$'\r'*)
      echo "Value for ${key} contains a newline or carriage return, which is not allowed in EnvironmentFile entries." >&2
      exit 1
      ;;
  esac
}

append_env_line() {
  local file="$1"
  local key="$2"
  local value="$3"
  require_envfile_safe_value "$key" "$value"
  printf '%s=%s\n' "$key" "$value" >>"$file"
}

require_nonnegative_integer() {
  local key="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "${key} must be a non-negative integer, got: ${value}" >&2
    exit 1
  fi
}

require_positive_integer() {
  local key="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [[ "$value" == "0" ]]; then
    echo "${key} must be a positive integer, got: ${value}" >&2
    exit 1
  fi
}

runtime_secret_key_available() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    return 0
  fi
  if sudo test -f "$OPENCODE_SECRET_ENV_FILE"; then
    sudo grep -q "^${key}=.\+" "$OPENCODE_SECRET_ENV_FILE"
    return $?
  fi
  return 1
}

validate_provider_secret_contract() {
  local provider="${OPENCODE_PROVIDER_ID:-}"
  local model="${OPENCODE_MODEL_ID:-}"
  local required_key=""
  if [[ -n "$provider" && -z "$model" ]]; then
    echo "OPENCODE_MODEL_ID is required when OPENCODE_PROVIDER_ID is set." >&2
    exit 1
  fi
  if [[ -n "$model" && -z "$provider" ]]; then
    echo "OPENCODE_PROVIDER_ID is required when OPENCODE_MODEL_ID is set." >&2
    exit 1
  fi
  if required_key="$(required_provider_secret_env_key "$provider" 2>/dev/null)"; then
    if ! runtime_secret_key_available "$required_key"; then
      echo "${required_key} is required when OPENCODE_PROVIDER_ID=${provider}." >&2
      echo "Provide it via environment variable or ${OPENCODE_SECRET_ENV_FILE} before starting services." >&2
      exit 1
    fi
  fi
}

data_root_supports_protect_home() {
  local root="${1%/}"
  case "$root" in
    /home|/root|/run/user|/home/*|/root/*|/run/user/*)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

resolve_service_group() {
  if [[ -n "$SERVICE_GROUP" ]]; then
    printf '%s\n' "$SERVICE_GROUP"
    return 0
  fi
  id -gn "$SERVICE_USER"
}

ensure_service_account_ready() {
  if ! id "$SERVICE_USER" &>/dev/null; then
    echo "Configured service user does not exist: ${SERVICE_USER}" >&2
    echo "Prepare the Linux service account before running deploy." >&2
    exit 1
  fi

  SERVICE_GROUP="$(resolve_service_group)"
  if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
    echo "Configured service group does not exist: ${SERVICE_GROUP}" >&2
    echo "Prepare the Linux service account before running deploy." >&2
    exit 1
  fi
}

ensure_data_root_accessible() {
  local root="$1"
  if ! sudo test -d "$root"; then
    echo "DATA_ROOT does not exist: ${root}" >&2
    echo "Prepare the base deploy directory before running deploy." >&2
    exit 1
  fi
  if ! sudo -u "$SERVICE_USER" test -x "$root"; then
    echo "DATA_ROOT is not traversable by SERVICE_USER=${SERVICE_USER}: ${root}" >&2
    echo "Prepare permissions so the service account can traverse DATA_ROOT." >&2
    exit 1
  fi
}

ensure_service_account_ready
ensure_data_root_accessible "$DATA_ROOT"
require_nonnegative_integer "A2A_MAX_REQUEST_BODY_BYTES" "$A2A_MAX_REQUEST_BODY_BYTES"
require_positive_integer "A2A_SYSTEMD_TASKS_MAX" "$A2A_SYSTEMD_TASKS_MAX"
require_positive_integer "A2A_SYSTEMD_LIMIT_NOFILE" "$A2A_SYSTEMD_LIMIT_NOFILE"

sudo install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$PROJECT_DIR" "$WORKSPACE_DIR" "$LOG_DIR" "$RUN_DIR"
sudo install -d -m 700 -o root -g root "$CONFIG_DIR"
# Ensure OpenCode can write its XDG cache/data paths under $HOME even if the
# instance was previously started with a different user (stale root-owned dirs).
sudo install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" \
  "$CACHE_DIR" \
  "$LOCAL_DIR" \
  "$STATE_DIR" \
  "$DATA_DIR" \
  "$OPENCODE_BIN_DIR"
# If the directory existed with wrong ownership (e.g., started as root once),
# fix it to avoid EACCES when opencode tries to mkdir under opencode/.
sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$CACHE_DIR" "$STATE_DIR" "$OPENCODE_LOCAL_SHARE_DIR"

opencode_auth_example_tmp="$(mktemp)"
cat <<'EOF' >"$opencode_auth_example_tmp"
# Root-only runtime secret file for opencode@.service.
# Populate GH_TOKEN here if ENABLE_SECRET_PERSISTENCE is not enabled during deploy.
GH_TOKEN=<github-token>
EOF
sudo install -m 600 -o root -g root "$opencode_auth_example_tmp" "$CONFIG_DIR/opencode.auth.env.example"
rm -f "$opencode_auth_example_tmp"

a2a_secret_example_tmp="$(mktemp)"
cat <<'EOF' >"$a2a_secret_example_tmp"
# Root-only runtime secret file for opencode-a2a-server@.service.
# Populate A2A_BEARER_TOKEN here if ENABLE_SECRET_PERSISTENCE is not enabled during deploy.
A2A_BEARER_TOKEN=<a2a-bearer-token>
EOF
sudo install -m 600 -o root -g root "$a2a_secret_example_tmp" "$CONFIG_DIR/a2a.secret.env.example"
rm -f "$a2a_secret_example_tmp"

opencode_secret_example_tmp="$(mktemp)"
{
  echo "# Optional root-only provider secret file for opencode@.service."
  echo "# Populate only the provider keys your deployment actually uses."
  for key in "${SECRET_ENV_KEYS[@]}"; do
    echo "${key}=<optional>"
  done
} >"$opencode_secret_example_tmp"
sudo install -m 600 -o root -g root "$opencode_secret_example_tmp" "$CONFIG_DIR/opencode.secret.env.example"
rm -f "$opencode_secret_example_tmp"

askpass_tmp="$(mktemp)"
cat <<'SCRIPT' >"$askpass_tmp"
#!/usr/bin/env bash
case "$1" in
  *Username*) echo "x-access-token" ;;
  *Password*) echo "${GH_TOKEN}" ;;
  *) echo "" ;;
esac
SCRIPT
sudo install -m 700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$askpass_tmp" "$ASKPASS_SCRIPT"
rm -f "$askpass_tmp"

git_author_name="OpenCode-${PROJECT_NAME}"
git_author_email="${PROJECT_NAME}@example.com"
if [[ -n "${GIT_IDENTITY_NAME:-}" ]]; then
  git_author_name="${GIT_IDENTITY_NAME}"
fi
if [[ -n "${GIT_IDENTITY_EMAIL:-}" ]]; then
  git_author_email="${GIT_IDENTITY_EMAIL}"
fi

opencode_env_tmp="$(mktemp)"
{
  append_env_line "$opencode_env_tmp" "OPENCODE_LOG_LEVEL" "${OPENCODE_LOG_LEVEL}"
  append_env_line "$opencode_env_tmp" "OPENCODE_BIND_HOST" "${OPENCODE_BIND_HOST}"
  append_env_line "$opencode_env_tmp" "OPENCODE_BIND_PORT" "${OPENCODE_BIND_PORT}"
  append_env_line "$opencode_env_tmp" "OPENCODE_EXTRA_ARGS" "${OPENCODE_EXTRA_ARGS:-}"
  append_env_line "$opencode_env_tmp" "OPENCODE_LSP" "${OPENCODE_LSP:-false}"
  append_env_line "$opencode_env_tmp" "GIT_ASKPASS" "${ASKPASS_SCRIPT}"
  append_env_line "$opencode_env_tmp" "GIT_ASKPASS_REQUIRE" "force"
  append_env_line "$opencode_env_tmp" "GIT_TERMINAL_PROMPT" "0"
  append_env_line "$opencode_env_tmp" "GIT_AUTHOR_NAME" "${git_author_name}"
  append_env_line "$opencode_env_tmp" "GIT_COMMITTER_NAME" "${git_author_name}"
  append_env_line "$opencode_env_tmp" "GIT_AUTHOR_EMAIL" "${git_author_email}"
  append_env_line "$opencode_env_tmp" "GIT_COMMITTER_EMAIL" "${git_author_email}"
  if [[ -n "${OPENCODE_PROVIDER_ID:-}" ]]; then
    append_env_line "$opencode_env_tmp" "OPENCODE_PROVIDER_ID" "${OPENCODE_PROVIDER_ID}"
  fi
  if [[ -n "${OPENCODE_MODEL_ID:-}" ]]; then
    append_env_line "$opencode_env_tmp" "OPENCODE_MODEL_ID" "${OPENCODE_MODEL_ID}"
  fi
}
sudo install -m 600 -o root -g root "$opencode_env_tmp" "$CONFIG_DIR/opencode.env"
rm -f "$opencode_env_tmp"

if [[ "$PERSIST_SECRETS" == "true" ]]; then # pragma: allowlist secret
  : "${GH_TOKEN:?GH_TOKEN is required when ENABLE_SECRET_PERSISTENCE=true}"
  : "${A2A_BEARER_TOKEN:?A2A_BEARER_TOKEN is required when ENABLE_SECRET_PERSISTENCE=true}"

  opencode_auth_env_tmp="$(mktemp)"
  append_env_line "$opencode_auth_env_tmp" "GH_TOKEN" "${GH_TOKEN}"
  sudo install -m 600 -o root -g root "$opencode_auth_env_tmp" "$OPENCODE_AUTH_ENV_FILE"
  rm -f "$opencode_auth_env_tmp"

  opencode_secret_env_tmp="$(mktemp)"
  has_secret_entry=0
  for key in "${SECRET_ENV_KEYS[@]}"; do
    value="${!key:-}"
    if [[ -z "$value" && -f "$OPENCODE_SECRET_ENV_FILE" ]]; then
      value="$(sed -n "s/^${key}=//p" "$OPENCODE_SECRET_ENV_FILE" | head -n 1)"
    fi
    if [[ -n "$value" ]]; then
      append_env_line "$opencode_secret_env_tmp" "$key" "$value"
      has_secret_entry=1
    fi
  done
  if [[ "$has_secret_entry" -eq 1 ]]; then
    sudo install -m 600 -o root -g root "$opencode_secret_env_tmp" "$OPENCODE_SECRET_ENV_FILE"
  fi
  rm -f "$opencode_secret_env_tmp"
else
  echo "ENABLE_SECRET_PERSISTENCE is disabled; deploy will not write GH_TOKEN, A2A_BEARER_TOKEN, or provider keys to disk." >&2
  echo "Provision root-only runtime secret files under ${CONFIG_DIR} before starting services:" >&2
  echo "  - opencode.auth.env (required: GH_TOKEN)" >&2
  echo "  - a2a.secret.env (required: A2A_BEARER_TOKEN)" >&2
  echo "  - opencode.secret.env (optional provider keys, if your OpenCode provider requires them)" >&2
  echo "Templates were generated as *.example files in ${CONFIG_DIR}." >&2
fi

a2a_env_tmp="$(mktemp)"
{
  append_env_line "$a2a_env_tmp" "A2A_HOST" "${A2A_HOST}"
  append_env_line "$a2a_env_tmp" "A2A_PORT" "${A2A_PORT}"
  append_env_line "$a2a_env_tmp" "A2A_PUBLIC_URL" "${A2A_PUBLIC_URL}"
  append_env_line "$a2a_env_tmp" "A2A_PROJECT" "${PROJECT_NAME}"
  append_env_line "$a2a_env_tmp" "A2A_LOG_LEVEL" "${A2A_LOG_LEVEL:-WARNING}"
  append_env_line "$a2a_env_tmp" "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED" "${A2A_OTEL_INSTRUMENTATION_ENABLED:-false}"
  append_env_line "$a2a_env_tmp" "A2A_LOG_PAYLOADS" "${A2A_LOG_PAYLOADS:-false}"
  append_env_line "$a2a_env_tmp" "A2A_LOG_BODY_LIMIT" "${A2A_LOG_BODY_LIMIT:-0}"
  append_env_line "$a2a_env_tmp" "A2A_MAX_REQUEST_BODY_BYTES" "${A2A_MAX_REQUEST_BODY_BYTES}"
  append_env_line "$a2a_env_tmp" "A2A_CANCEL_ABORT_TIMEOUT_SECONDS" "${A2A_CANCEL_ABORT_TIMEOUT_SECONDS}"
  append_env_line "$a2a_env_tmp" "A2A_ENABLE_SESSION_SHELL" "${A2A_ENABLE_SESSION_SHELL}"
  append_env_line "$a2a_env_tmp" "OPENCODE_BASE_URL" "http://${OPENCODE_BIND_HOST}:${OPENCODE_BIND_PORT}"
  append_env_line "$a2a_env_tmp" "OPENCODE_DIRECTORY" "${WORKSPACE_DIR}"
  append_env_line "$a2a_env_tmp" "OPENCODE_TIMEOUT" "${OPENCODE_TIMEOUT:-300}"
  if [[ -n "${OPENCODE_TIMEOUT_STREAM:-}" ]]; then
    append_env_line "$a2a_env_tmp" "OPENCODE_TIMEOUT_STREAM" "${OPENCODE_TIMEOUT_STREAM}"
  fi
  if [[ -n "${OPENCODE_PROVIDER_ID:-}" ]]; then
    append_env_line "$a2a_env_tmp" "OPENCODE_PROVIDER_ID" "${OPENCODE_PROVIDER_ID}"
  fi
  if [[ -n "${OPENCODE_MODEL_ID:-}" ]]; then
    append_env_line "$a2a_env_tmp" "OPENCODE_MODEL_ID" "${OPENCODE_MODEL_ID}"
  fi
}
sudo install -m 600 -o root -g root "$a2a_env_tmp" "$CONFIG_DIR/a2a.env"
rm -f "$a2a_env_tmp"

systemd_override_tmp="$(mktemp)"
{
  echo "[Service]"
  echo "User=${SERVICE_USER}"
  echo "Group=${SERVICE_GROUP}"
  echo "PrivateDevices=true"
  echo "ProtectKernelTunables=true"
  echo "ProtectKernelModules=true"
  echo "ProtectControlGroups=true"
  echo "RestrictSUIDSGID=true"
  echo "LockPersonality=true"
  echo "RestrictNamespaces=true"
  echo "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"
  echo "TasksMax=${A2A_SYSTEMD_TASKS_MAX}"
  echo "LimitNOFILE=${A2A_SYSTEMD_LIMIT_NOFILE}"
  if data_root_supports_protect_home "${DATA_ROOT}"; then
    echo "ProtectHome=true"
  else
    echo "# ProtectHome omitted because DATA_ROOT is under /home, /root, or /run/user"
  fi
  if [[ -n "${A2A_SYSTEMD_MEMORY_MAX}" ]]; then
    echo "MemoryMax=${A2A_SYSTEMD_MEMORY_MAX}"
  fi
  if [[ -n "${A2A_SYSTEMD_CPU_QUOTA}" ]]; then
    echo "CPUQuota=${A2A_SYSTEMD_CPU_QUOTA}"
  fi
  if is_truthy "${A2A_STRICT_ISOLATION}"; then
    # InaccessiblePaths cannot nest allow-lists underneath it. Use a tmpfs
    # view for DATA_ROOT and bind back only this project's directory.
    echo "TemporaryFileSystem=${DATA_ROOT}:ro"
    echo "BindPaths=${PROJECT_DIR}:${PROJECT_DIR}"
    echo "ReadWritePaths="
    echo "ReadWritePaths=${PROJECT_DIR}"
  fi
} >"$systemd_override_tmp"
sudo install -d -m 755 -o root -g root "$OPENCODE_OVERRIDE_DIR" "$A2A_OVERRIDE_DIR"
sudo install -m 644 -o root -g root "$systemd_override_tmp" "${OPENCODE_OVERRIDE_DIR}/override.conf"
sudo install -m 644 -o root -g root "$systemd_override_tmp" "${A2A_OVERRIDE_DIR}/override.conf"
rm -f "$systemd_override_tmp"

if [[ "$PERSIST_SECRETS" == "true" ]]; then # pragma: allowlist secret
  a2a_secret_env_tmp="$(mktemp)"
  append_env_line "$a2a_secret_env_tmp" "A2A_BEARER_TOKEN" "${A2A_BEARER_TOKEN}"
  sudo install -m 600 -o root -g root "$a2a_secret_env_tmp" "$A2A_SECRET_ENV_FILE"
  rm -f "$a2a_secret_env_tmp"
fi

require_runtime_secret_file() {
  local file="$1"
  local key="$2"
  local example="$3"
  if ! sudo test -f "$file"; then
    echo "Missing required runtime secret file: ${file}" >&2
    echo "Copy and edit the template: ${example}" >&2
    exit 1
  fi
  if ! sudo grep -q "^${key}=" "$file"; then
    echo "Runtime secret file does not define ${key}: ${file}" >&2
    echo "See template: ${example}" >&2
    exit 1
  fi
}

require_runtime_secret_file "$OPENCODE_AUTH_ENV_FILE" "GH_TOKEN" "$CONFIG_DIR/opencode.auth.env.example"
require_runtime_secret_file "$A2A_SECRET_ENV_FILE" "A2A_BEARER_TOKEN" "$CONFIG_DIR/a2a.secret.env.example"
validate_provider_secret_contract
