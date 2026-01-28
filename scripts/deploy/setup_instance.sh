#!/usr/bin/env bash
# Create project user, directories, and env files for systemd services.
# Usage: ./setup_instance.sh <project_name> <github_token> <a2a_bearer_token>
# Requires env: DATA_ROOT, OPENCODE_BIND_HOST, OPENCODE_BIND_PORT, OPENCODE_LOG_LEVEL,
#               A2A_HOST, A2A_PORT, A2A_PUBLIC_URL.
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

PROJECT_DIR="${DATA_ROOT}/${PROJECT_NAME}"
WORKSPACE_DIR="${PROJECT_DIR}/workspace"
CONFIG_DIR="${PROJECT_DIR}/config"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_DIR="${PROJECT_DIR}/run"
ASKPASS_SCRIPT="${RUN_DIR}/git-askpass.sh"

if ! id "$PROJECT_NAME" &>/dev/null; then
  sudo adduser --system --group --home "$PROJECT_DIR" "$PROJECT_NAME"
fi

sudo install -d -m 711 -o root -g root "$DATA_ROOT"
sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" "$PROJECT_DIR" "$WORKSPACE_DIR" "$LOG_DIR" "$RUN_DIR"
sudo install -d -m 700 -o root -g root "$CONFIG_DIR"

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
  echo "GIT_AUTHOR_NAME=OpenCode-${PROJECT_NAME}"
  echo "GIT_COMMITTER_NAME=OpenCode-${PROJECT_NAME}"
  echo "GIT_AUTHOR_EMAIL=${PROJECT_NAME}@internal"
  echo "GIT_COMMITTER_EMAIL=${PROJECT_NAME}@internal"
  if [[ -n "${OPENCODE_PROVIDER_ID:-}" ]]; then
    echo "OPENCODE_PROVIDER_ID=${OPENCODE_PROVIDER_ID}"
  fi
  if [[ -n "${OPENCODE_MODEL_ID:-}" ]]; then
    echo "OPENCODE_MODEL_ID=${OPENCODE_MODEL_ID}"
  fi
} >"$opencode_env_tmp"
sudo install -m 600 -o root -g root "$opencode_env_tmp" "$CONFIG_DIR/opencode.env"
rm -f "$opencode_env_tmp"

a2a_env_tmp="$(mktemp)"
cat <<ENV >"$a2a_env_tmp"
A2A_HOST=${A2A_HOST}
A2A_PORT=${A2A_PORT}
A2A_PUBLIC_URL=${A2A_PUBLIC_URL}
A2A_BEARER_TOKEN=${A2A_BEARER_TOKEN}
OPENCODE_BASE_URL=http://${OPENCODE_BIND_HOST}:${OPENCODE_BIND_PORT}
OPENCODE_DIRECTORY=${WORKSPACE_DIR}
OPENCODE_TIMEOUT=120
ENV
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
