#!/usr/bin/env bash
# Deploy an isolated OpenCode + A2A instance (systemd services).
# Usage: ./deploy.sh project=<name> github_token=<token> a2a_bearer_token=<token> [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] [opencode_provider_id=<id>] [opencode_model_id=<id>] [repo_url=<url>] [repo_branch=<branch>] [opencode_timeout=<seconds>] [opencode_timeout_stream=<seconds>] [git_identity_name=<name>] [git_identity_email=<email>] [update_a2a=true] [force_restart=true]
# Optional: GOOGLE_GENERATIVE_AI_API_KEY=<key> to persist API key into opencode.secret.env for opencode@ service.
# Requires: sudo access to write systemd units and create users/directories.
#
# Key environment variables (optional):
# - OPENCODE_A2A_DIR: path to opencode-a2a-serve repo (default: /opt/opencode-a2a/opencode-a2a-serve)
# - OPENCODE_CORE_DIR: path to opencode core (default: /opt/.opencode)
# - UV_PYTHON_DIR: path to uv python pool (default: /opt/uv-python)
# - DATA_ROOT: instance root directory (default: /data/opencode-a2a)
# - OPENCODE_BIND_HOST/OPENCODE_BIND_PORT/OPENCODE_LOG_LEVEL/OPENCODE_EXTRA_ARGS
# - A2A_HOST/A2A_PORT/A2A_LOG_LEVEL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT_NAME=""
GH_TOKEN=""
A2A_BEARER_TOKEN=""
A2A_PORT_INPUT=""
A2A_HOST_INPUT=""
A2A_PUBLIC_URL_INPUT=""
OPENCODE_PROVIDER_ID_INPUT=""
OPENCODE_MODEL_ID_INPUT=""
GOOGLE_API_KEY_INPUT="${GOOGLE_GENERATIVE_AI_API_KEY:-}"
REPO_URL_INPUT=""
REPO_BRANCH_INPUT=""
OPENCODE_TIMEOUT_INPUT=""
OPENCODE_TIMEOUT_STREAM_INPUT=""
GIT_IDENTITY_NAME_INPUT=""
GIT_IDENTITY_EMAIL_INPUT=""
UPDATE_A2A_INPUT=""
FORCE_RESTART_INPUT=""

for arg in "$@"; do
  if [[ "$arg" == *=* ]]; then
    key="${arg%%=*}"
    value="${arg#*=}"
  else
    echo "Unknown argument format: $arg (expected key=value)" >&2
    exit 1
  fi

  case "${key,,}" in
    project|project_name)
      PROJECT_NAME="$value"
      ;;
    github_token|gh_token)
      GH_TOKEN="$value"
      ;;
    a2a_bearer_token|bearer_token)
      A2A_BEARER_TOKEN="$value"
      ;;
    a2a_port)
      A2A_PORT_INPUT="$value"
      ;;
    a2a_host)
      A2A_HOST_INPUT="$value"
      ;;
    a2a_public_url)
      A2A_PUBLIC_URL_INPUT="$value"
      ;;
    opencode_provider_id)
      OPENCODE_PROVIDER_ID_INPUT="$value"
      ;;
    opencode_model_id)
      OPENCODE_MODEL_ID_INPUT="$value"
      ;;
    google_generative_ai_api_key|google_api_key)
      GOOGLE_API_KEY_INPUT="$value"
      ;;
    repo_url)
      REPO_URL_INPUT="$value"
      ;;
    repo_branch)
      REPO_BRANCH_INPUT="$value"
      ;;
    opencode_timeout)
      OPENCODE_TIMEOUT_INPUT="$value"
      ;;
    opencode_timeout_stream)
      OPENCODE_TIMEOUT_STREAM_INPUT="$value"
      ;;
    git_identity_name)
      GIT_IDENTITY_NAME_INPUT="$value"
      ;;
    git_identity_email)
      GIT_IDENTITY_EMAIL_INPUT="$value"
      ;;
    update_a2a)
      UPDATE_A2A_INPUT="$value"
      ;;
    force_restart)
      FORCE_RESTART_INPUT="$value"
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" || -z "$GH_TOKEN" || -z "$A2A_BEARER_TOKEN" ]]; then
  echo "Usage: $0 project=<name> github_token=<token> a2a_bearer_token=<token> [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] [opencode_provider_id=<id>] [opencode_model_id=<id>] [repo_url=<url>] [repo_branch=<branch>] [opencode_timeout=<seconds>] [opencode_timeout_stream=<seconds>] [git_identity_name=<name>] [git_identity_email=<email>] [update_a2a=true] [force_restart=true]" >&2
  exit 1
fi

export OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-serve}"
export OPENCODE_CORE_DIR="${OPENCODE_CORE_DIR:-/opt/.opencode}"
export UV_PYTHON_DIR="${UV_PYTHON_DIR:-/opt/uv-python}"
export DATA_ROOT="${DATA_ROOT:-/data/opencode-a2a}"

if [[ -n "$OPENCODE_PROVIDER_ID_INPUT" ]]; then
  export OPENCODE_PROVIDER_ID="$OPENCODE_PROVIDER_ID_INPUT"
fi
if [[ -n "$OPENCODE_MODEL_ID_INPUT" ]]; then
  export OPENCODE_MODEL_ID="$OPENCODE_MODEL_ID_INPUT"
fi
if [[ -n "$GOOGLE_API_KEY_INPUT" ]]; then
  export GOOGLE_GENERATIVE_AI_API_KEY="$GOOGLE_API_KEY_INPUT"
fi
if [[ -n "$REPO_URL_INPUT" ]]; then
  export REPO_URL="$REPO_URL_INPUT"
fi
if [[ -n "$REPO_BRANCH_INPUT" ]]; then
  export REPO_BRANCH="$REPO_BRANCH_INPUT"
fi
if [[ -n "$OPENCODE_TIMEOUT_INPUT" ]]; then
  export OPENCODE_TIMEOUT="$OPENCODE_TIMEOUT_INPUT"
fi
if [[ -n "$OPENCODE_TIMEOUT_STREAM_INPUT" ]]; then
  export OPENCODE_TIMEOUT_STREAM="$OPENCODE_TIMEOUT_STREAM_INPUT"
fi
if [[ -n "$GIT_IDENTITY_NAME_INPUT" ]]; then
  export GIT_IDENTITY_NAME="$GIT_IDENTITY_NAME_INPUT"
fi
if [[ -n "$GIT_IDENTITY_EMAIL_INPUT" ]]; then
  export GIT_IDENTITY_EMAIL="$GIT_IDENTITY_EMAIL_INPUT"
fi

export OPENCODE_BIND_HOST="${OPENCODE_BIND_HOST:-127.0.0.1}"
export OPENCODE_LOG_LEVEL="${OPENCODE_LOG_LEVEL:-DEBUG}"
export OPENCODE_EXTRA_ARGS="${OPENCODE_EXTRA_ARGS:-}"

if [[ -n "$A2A_HOST_INPUT" ]]; then
  export A2A_HOST="$A2A_HOST_INPUT"
else
  export A2A_HOST="${A2A_HOST:-127.0.0.1}"
fi
if [[ -n "$A2A_PORT_INPUT" ]]; then
  export A2A_PORT="$A2A_PORT_INPUT"
else
  export A2A_PORT="${A2A_PORT:-8000}"
fi

if [[ -z "${OPENCODE_BIND_PORT:-}" ]]; then
  if [[ "$A2A_PORT" =~ ^[0-9]+$ ]]; then
    export OPENCODE_BIND_PORT="$((A2A_PORT + 1))"
  else
    export OPENCODE_BIND_PORT="4096"
  fi
fi
if [[ -n "$A2A_PUBLIC_URL_INPUT" ]]; then
  export A2A_PUBLIC_URL="$A2A_PUBLIC_URL_INPUT"
else
  export A2A_PUBLIC_URL="http://${A2A_HOST}:${A2A_PORT}"
fi
export A2A_LOG_LEVEL="${A2A_LOG_LEVEL:-DEBUG}"
export A2A_STREAMING="${A2A_STREAMING:-true}"
export A2A_LOG_PAYLOADS="${A2A_LOG_PAYLOADS:-true}"
export A2A_LOG_BODY_LIMIT="${A2A_LOG_BODY_LIMIT:-0}"

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

UPDATE_A2A="false"
FORCE_RESTART="false"
if [[ -n "$UPDATE_A2A_INPUT" ]] && is_truthy "$UPDATE_A2A_INPUT"; then
  UPDATE_A2A="true"
fi
if [[ -n "$FORCE_RESTART_INPUT" ]] && is_truthy "$FORCE_RESTART_INPUT"; then
  FORCE_RESTART="true"
fi

if [[ "$UPDATE_A2A" == "true" ]]; then
  "${SCRIPT_DIR}/deploy/update_a2a.sh"
fi

"${SCRIPT_DIR}/deploy/install_units.sh"
"${SCRIPT_DIR}/deploy/setup_instance.sh" "$PROJECT_NAME" "$GH_TOKEN" "$A2A_BEARER_TOKEN"
FORCE_RESTART="$FORCE_RESTART" "${SCRIPT_DIR}/deploy/enable_instance.sh" "$PROJECT_NAME"
