#!/usr/bin/env bash
# Create project user, directories, and env files for systemd services.
# Usage: ./setup_instance.sh <project_name> <github_token> <a2a_bearer_token>
# Requires env: DATA_ROOT, OPENCODE_BIND_HOST, OPENCODE_BIND_PORT, OPENCODE_LOG_LEVEL,
#               A2A_HOST, A2A_PORT, A2A_PUBLIC_URL.
# Optional env: GOOGLE_GENERATIVE_AI_API_KEY (persisted into config/opencode.secret.env when provided).
set -euo pipefail

PROJECT_NAME="${1:-}"
GH_TOKEN="${2:-}"
A2A_BEARER_TOKEN="${3:-}"

if [[ -z "$PROJECT_NAME" || -z "$GH_TOKEN" || -z "$A2A_BEARER_TOKEN" ]]; then
  echo "Usage: $0 <project_name> <github_token> <a2a_bearer_token>" >&2
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

PROJECT_DIR="${DATA_ROOT}/${PROJECT_NAME}"
WORKSPACE_DIR="${PROJECT_DIR}/workspace"
CONFIG_DIR="${PROJECT_DIR}/config"
OPENCODE_SECRET_ENV_FILE="${CONFIG_DIR}/opencode.secret.env"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_DIR="${PROJECT_DIR}/run"
ASKPASS_SCRIPT="${RUN_DIR}/git-askpass.sh"
CACHE_DIR="${PROJECT_DIR}/.cache/opencode"
LOCAL_DIR="${PROJECT_DIR}/.local"
STATE_DIR="${LOCAL_DIR}/state"
OPENCODE_LOCAL_SHARE_DIR="${PROJECT_DIR}/.local/share/opencode"
OPENCODE_BIN_DIR="${OPENCODE_LOCAL_SHARE_DIR}/bin"
DATA_DIR="${PROJECT_DIR}/.local/share/opencode/storage/session"

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
git_author_email="${PROJECT_NAME}@internal"
if [[ -n "${GIT_IDENTITY_NAME:-}" ]]; then
  git_author_name="${GIT_IDENTITY_NAME}"
fi
if [[ -n "${GIT_IDENTITY_EMAIL:-}" ]]; then
  git_author_email="${GIT_IDENTITY_EMAIL}"
fi

opencode_env_tmp="$(mktemp)"
{
  echo "OPENCODE_LOG_LEVEL=${OPENCODE_LOG_LEVEL}"
  echo "OPENCODE_BIND_HOST=${OPENCODE_BIND_HOST}"
  echo "OPENCODE_BIND_PORT=${OPENCODE_BIND_PORT}"
  echo "OPENCODE_EXTRA_ARGS=${OPENCODE_EXTRA_ARGS:-}"
  echo "GH_TOKEN=${GH_TOKEN}"
  echo "GIT_ASKPASS=${ASKPASS_SCRIPT}"
  echo "GIT_ASKPASS_REQUIRE=force"
  echo "GIT_TERMINAL_PROMPT=0"
  echo "GIT_AUTHOR_NAME=${git_author_name}"
  echo "GIT_COMMITTER_NAME=${git_author_name}"
  echo "GIT_AUTHOR_EMAIL=${git_author_email}"
  echo "GIT_COMMITTER_EMAIL=${git_author_email}"
  if [[ -n "${OPENCODE_PROVIDER_ID:-}" ]]; then
    echo "OPENCODE_PROVIDER_ID=${OPENCODE_PROVIDER_ID}"
  fi
  if [[ -n "${OPENCODE_MODEL_ID:-}" ]]; then
    echo "OPENCODE_MODEL_ID=${OPENCODE_MODEL_ID}"
  fi
} >"$opencode_env_tmp"
sudo install -m 600 -o root -g root "$opencode_env_tmp" "$CONFIG_DIR/opencode.env"
rm -f "$opencode_env_tmp"

if [[ -n "${GOOGLE_GENERATIVE_AI_API_KEY:-}" ]]; then
  opencode_secret_env_tmp="$(mktemp)"
  {
    echo "GOOGLE_GENERATIVE_AI_API_KEY=${GOOGLE_GENERATIVE_AI_API_KEY}"
  } >"$opencode_secret_env_tmp"
  sudo install -m 600 -o root -g root "$opencode_secret_env_tmp" "$OPENCODE_SECRET_ENV_FILE"
  rm -f "$opencode_secret_env_tmp"
fi

a2a_env_tmp="$(mktemp)"
{
  echo "A2A_HOST=${A2A_HOST}"
  echo "A2A_PORT=${A2A_PORT}"
  echo "A2A_PUBLIC_URL=${A2A_PUBLIC_URL}"
  echo "A2A_BEARER_TOKEN=${A2A_BEARER_TOKEN}"
  echo "A2A_STREAMING=${A2A_STREAMING}"
  echo "A2A_LOG_LEVEL=${A2A_LOG_LEVEL:-INFO}"
  echo "A2A_LOG_PAYLOADS=${A2A_LOG_PAYLOADS:-false}"
  echo "A2A_LOG_BODY_LIMIT=${A2A_LOG_BODY_LIMIT:-0}"
  echo "OPENCODE_BASE_URL=http://${OPENCODE_BIND_HOST}:${OPENCODE_BIND_PORT}"
  echo "OPENCODE_DIRECTORY=${WORKSPACE_DIR}"
  echo "OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT:-300}"
  if [[ -n "${OPENCODE_TIMEOUT_STREAM:-}" ]]; then
    echo "OPENCODE_TIMEOUT_STREAM=${OPENCODE_TIMEOUT_STREAM}"
  fi
  if [[ -n "${OPENCODE_PROVIDER_ID:-}" ]]; then
    echo "OPENCODE_PROVIDER_ID=${OPENCODE_PROVIDER_ID}"
  fi
  if [[ -n "${OPENCODE_MODEL_ID:-}" ]]; then
    echo "OPENCODE_MODEL_ID=${OPENCODE_MODEL_ID}"
  fi
} >"$a2a_env_tmp"
sudo install -m 600 -o root -g root "$a2a_env_tmp" "$CONFIG_DIR/a2a.env"
rm -f "$a2a_env_tmp"

if command -v gh >/dev/null 2>&1; then
  sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" \
    "${PROJECT_DIR}/.config" "${PROJECT_DIR}/.config/gh"
  if ! printf '%s' "$GH_TOKEN" | sudo -u "$PROJECT_NAME" -H \
    gh auth login --hostname github.com --with-token >/dev/null 2>&1; then
    echo "gh auth login failed for ${PROJECT_NAME}" >&2
    exit 1
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
    sudo -u "$PROJECT_NAME" -H env \
      GH_TOKEN="$GH_TOKEN" \
      GIT_ASKPASS="$ASKPASS_SCRIPT" \
      GIT_ASKPASS_REQUIRE=force \
      GIT_TERMINAL_PROMPT=0 \
      git clone "${clone_args[@]}"
  fi
fi
