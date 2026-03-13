#!/usr/bin/env bash
# Create project user, directories, and env files for systemd services.
# Usage: [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [ENABLE_SECRET_PERSISTENCE=true] ./setup_instance.sh <project_name>
# Requires env: DATA_ROOT, OPENCODE_BIND_HOST, OPENCODE_BIND_PORT, OPENCODE_LOG_LEVEL,
#               A2A_HOST, A2A_PORT, A2A_PUBLIC_URL.
# Optional provider secret env: see scripts/deploy/provider_secret_env_keys.sh
# Secret persistence is opt-in via ENABLE_SECRET_PERSISTENCE=true.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
: "${A2A_STREAMING:=true}"
: "${A2A_OTEL_INSTRUMENTATION_ENABLED:=false}"
: "${A2A_CANCEL_ABORT_TIMEOUT_SECONDS:=2.0}"
: "${A2A_ENABLE_SESSION_SHELL:=false}"
: "${ENABLE_SECRET_PERSISTENCE:=false}"

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

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

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

# DATA_ROOT must be traversable by the per-project system user. In hardened
# deployments, using a non-traversable DATA_ROOT (missing o+x) will break
# OpenCode writes to $HOME/.cache, $HOME/.local/share, and $HOME/.local/state.
ensure_data_root_accessible() {
  local root="$1"
  if ! sudo test -d "$root"; then
    sudo install -d -m 711 -o root -g root "$root"
    return 0
  fi
  local mode
  mode="$(sudo stat -c '%a' "$root" 2>/dev/null || echo "")"
  if [[ ! "$mode" =~ ^[0-9]{3,4}$ ]]; then
    echo "Unable to stat DATA_ROOT: ${root}" >&2
    exit 1
  fi
  local other=$((mode % 10))
  if (( (other & 1) == 0 )); then
    echo "DATA_ROOT is not traversable by project users: ${root} (mode=${mode})." >&2
    echo "Fix: choose a different DATA_ROOT (recommended: /data/opencode-a2a) or chmod o+x on DATA_ROOT." >&2
    exit 1
  fi
}

ensure_data_root_accessible "$DATA_ROOT"

get_user_home() {
  getent passwd "$1" | awk -F: '{print $6}'
}

ensure_user_home_matches_project_dir() {
  # This deploy workflow expects each instance user to have HOME=${DATA_ROOT}/<project>.
  # If an operator previously deployed with a different DATA_ROOT, we fail fast to
  # avoid subtle systemd/unit mismatches and permission issues.
  local user="$1"
  local expected_home="$2"
  if ! id "$user" &>/dev/null; then
    return 0
  fi
  local current_home
  current_home="$(get_user_home "$user")"
  if [[ -z "$current_home" ]]; then
    echo "Unable to determine home directory for user: ${user}" >&2
    exit 1
  fi
  if [[ "$current_home" != "$expected_home" ]]; then
    echo "Existing user ${user} has a different home directory than expected:" >&2
    echo "  current:  ${current_home}" >&2
    echo "  expected: ${expected_home}" >&2
    echo "" >&2
    echo "This deploy script does not migrate instances automatically." >&2
    echo "Fix: uninstall/recreate the instance user, or migrate explicitly, then re-run deploy." >&2
    exit 1
  fi
}

ensure_user_home_matches_project_dir "$PROJECT_NAME" "$PROJECT_DIR"

if ! id "$PROJECT_NAME" &>/dev/null; then
  sudo adduser --system --group --home "$PROJECT_DIR" "$PROJECT_NAME"
fi
if [[ -n "${UV_PYTHON_DIR_GROUP:-}" ]] && getent group "${UV_PYTHON_DIR_GROUP}" >/dev/null 2>&1; then
  if command -v usermod >/dev/null 2>&1; then
    sudo usermod -aG "${UV_PYTHON_DIR_GROUP}" "$PROJECT_NAME"
  else
    echo "usermod not found; cannot add ${PROJECT_NAME} to UV_PYTHON_DIR_GROUP=${UV_PYTHON_DIR_GROUP}." >&2
  fi
fi

sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" "$PROJECT_DIR" "$WORKSPACE_DIR" "$LOG_DIR" "$RUN_DIR"
sudo install -d -m 700 -o root -g root "$CONFIG_DIR"
# Ensure OpenCode can write its XDG cache/data paths under $HOME even if the
# instance was previously started with a different user (stale root-owned dirs).
sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" \
  "$CACHE_DIR" \
  "$LOCAL_DIR" \
  "$STATE_DIR" \
  "$DATA_DIR" \
  "$OPENCODE_BIN_DIR"
# If the directory existed with wrong ownership (e.g., started as root once),
# fix it to avoid EACCES when opencode tries to mkdir under opencode/.
sudo chown -R "$PROJECT_NAME:$PROJECT_NAME" "$CACHE_DIR" "$STATE_DIR" "$OPENCODE_LOCAL_SHARE_DIR"

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
sudo install -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" "$askpass_tmp" "$ASKPASS_SCRIPT"
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
  append_env_line "$a2a_env_tmp" "A2A_STREAMING" "${A2A_STREAMING}"
  append_env_line "$a2a_env_tmp" "A2A_LOG_LEVEL" "${A2A_LOG_LEVEL:-WARNING}"
  append_env_line "$a2a_env_tmp" "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED" "${A2A_OTEL_INSTRUMENTATION_ENABLED:-false}"
  append_env_line "$a2a_env_tmp" "A2A_LOG_PAYLOADS" "${A2A_LOG_PAYLOADS:-false}"
  append_env_line "$a2a_env_tmp" "A2A_LOG_BODY_LIMIT" "${A2A_LOG_BODY_LIMIT:-0}"
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

read_runtime_secret_value() {
  local file="$1"
  local key="$2"
  sudo sed -n "s/^${key}=//p" "$file" | head -n 1
}

require_runtime_secret_file "$OPENCODE_AUTH_ENV_FILE" "GH_TOKEN" "$CONFIG_DIR/opencode.auth.env.example"
require_runtime_secret_file "$A2A_SECRET_ENV_FILE" "A2A_BEARER_TOKEN" "$CONFIG_DIR/a2a.secret.env.example"

GH_TOKEN_FOR_SETUP="${GH_TOKEN:-}"
if [[ -z "$GH_TOKEN_FOR_SETUP" ]]; then
  GH_TOKEN_FOR_SETUP="$(read_runtime_secret_value "$OPENCODE_AUTH_ENV_FILE" "GH_TOKEN")"
fi

if command -v gh >/dev/null 2>&1; then
  sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" \
    "${PROJECT_DIR}/.config" "${PROJECT_DIR}/.config/gh"
  if [[ -n "$GH_TOKEN_FOR_SETUP" ]]; then
    if ! printf '%s' "$GH_TOKEN_FOR_SETUP" | sudo -u "$PROJECT_NAME" -H \
      gh auth login --hostname github.com --with-token >/dev/null 2>&1; then
      echo "gh auth login failed for ${PROJECT_NAME}" >&2
      exit 1
    fi
  else
    echo "GH_TOKEN not available during deploy; skipping gh auth login for ${PROJECT_NAME}." >&2
  fi
else
  echo "gh not found; skipping gh auth setup." >&2
fi

if [[ -n "${REPO_URL:-}" ]]; then
  if sudo -u "$PROJECT_NAME" -H test -d "${WORKSPACE_DIR}/.git"; then
    echo "Workspace already initialized; skipping clone."
  elif [[ -n "$(sudo -u "$PROJECT_NAME" -H ls -A "$WORKSPACE_DIR" 2>/dev/null)" ]]; then
    echo "Workspace is not empty; skipping clone." >&2
  else
    clone_args=("$REPO_URL" "$WORKSPACE_DIR")
    if [[ -n "${REPO_BRANCH:-}" ]]; then
      clone_args=(--branch "$REPO_BRANCH" --single-branch "${clone_args[@]}")
    fi
    if [[ -n "$GH_TOKEN_FOR_SETUP" ]]; then
      sudo -u "$PROJECT_NAME" -H env \
        GH_TOKEN="$GH_TOKEN_FOR_SETUP" \
        GIT_ASKPASS="$ASKPASS_SCRIPT" \
        GIT_ASKPASS_REQUIRE=force \
        GIT_TERMINAL_PROMPT=0 \
        git clone "${clone_args[@]}"
    else
      sudo -u "$PROJECT_NAME" -H git clone "${clone_args[@]}"
    fi
  fi
fi
