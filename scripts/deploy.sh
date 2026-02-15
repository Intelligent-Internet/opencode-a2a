#!/usr/bin/env bash
# Deploy an isolated OpenCode + A2A instance (systemd services).
# Usage: ./deploy.sh project=<name> [data_root=<path>] [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] [a2a_streaming=<bool>] [a2a_log_level=<level>] [a2a_log_payloads=<bool>] [a2a_log_body_limit=<int>] [opencode_provider_id=<id>] [opencode_model_id=<id>] [opencode_lsp=<bool>] [repo_url=<url>] [repo_branch=<branch>] [opencode_timeout=<seconds>] [opencode_timeout_stream=<seconds>] [git_identity_name=<name>] [git_identity_email=<email>] [update_a2a=true] [force_restart=true]
# Required env: GH_TOKEN, A2A_BEARER_TOKEN
# Optional provider secret env: see scripts/deploy/provider_secret_env_keys.sh
# Requires: sudo access to write systemd units and create users/directories.
#
# Source of truth for all variable semantics/defaults:
# - docs/deployment.md (section: "deploy.sh Inputs and Generated Variables")
# Non-secret options that support both env and CLI key=value use precedence:
# - CLI > env > default
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/deploy/provider_secret_env_keys.sh"
PROVIDER_SECRET_ENV_LIST="$(join_provider_secret_env_keys " | ")"

PROJECT_NAME=""
GH_TOKEN="${GH_TOKEN:-}"
A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN:-}"
A2A_PORT_INPUT=""
A2A_HOST_INPUT=""
A2A_PUBLIC_URL_INPUT=""
A2A_STREAMING_INPUT=""
A2A_LOG_LEVEL_INPUT=""
A2A_LOG_PAYLOADS_INPUT=""
A2A_LOG_BODY_LIMIT_INPUT=""
DATA_ROOT_INPUT=""
OPENCODE_PROVIDER_ID_INPUT=""
OPENCODE_MODEL_ID_INPUT=""
OPENCODE_LSP_INPUT=""
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
      echo "Sensitive parameter '${key}' is not allowed via CLI. Use environment variable GH_TOKEN." >&2
      exit 1
      ;;
    a2a_bearer_token|bearer_token)
      echo "Sensitive parameter '${key}' is not allowed via CLI. Use environment variable A2A_BEARER_TOKEN." >&2
      exit 1
      ;;
    a2a_port)
      A2A_PORT_INPUT="$value"
      ;;
    data_root)
      DATA_ROOT_INPUT="$value"
      ;;
    a2a_host)
      A2A_HOST_INPUT="$value"
      ;;
    a2a_public_url)
      A2A_PUBLIC_URL_INPUT="$value"
      ;;
    a2a_streaming)
      A2A_STREAMING_INPUT="$value"
      ;;
    a2a_log_level)
      A2A_LOG_LEVEL_INPUT="$value"
      ;;
    a2a_log_payloads)
      A2A_LOG_PAYLOADS_INPUT="$value"
      ;;
    a2a_log_body_limit)
      A2A_LOG_BODY_LIMIT_INPUT="$value"
      ;;
    opencode_provider_id)
      OPENCODE_PROVIDER_ID_INPUT="$value"
      ;;
    opencode_model_id)
      OPENCODE_MODEL_ID_INPUT="$value"
      ;;
    opencode_lsp)
      OPENCODE_LSP_INPUT="$value"
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
      if provider_env_key="$(provider_secret_env_for_cli_key "${key,,}" 2>/dev/null)"; then
        echo "Sensitive parameter '${key}' is not allowed via CLI. Use environment variable ${provider_env_key}." >&2
        exit 1
      fi
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" || -z "$GH_TOKEN" || -z "$A2A_BEARER_TOKEN" ]]; then
  cat >&2 <<USAGE
Usage:
  GH_TOKEN=<token> A2A_BEARER_TOKEN=<token> [<PROVIDER_SECRET_ENV>=<key>] \
  ./scripts/deploy.sh project=<name> [data_root=<path>] [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] \
  [a2a_streaming=<bool>] [a2a_log_level=<level>] [a2a_log_payloads=<bool>] [a2a_log_body_limit=<int>] \
  [opencode_provider_id=<id>] [opencode_model_id=<id>] [opencode_lsp=<bool>] [repo_url=<url>] [repo_branch=<branch>] \
  [opencode_timeout=<seconds>] [opencode_timeout_stream=<seconds>] [git_identity_name=<name>] \
  [git_identity_email=<email>] [update_a2a=true] [force_restart=true]

Provider secret env vars:
  ${PROVIDER_SECRET_ENV_LIST}
USAGE
  exit 1
fi

export OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-serve}"
export OPENCODE_CORE_DIR="${OPENCODE_CORE_DIR:-/opt/.opencode}"
export UV_PYTHON_DIR="${UV_PYTHON_DIR:-/opt/uv-python}"
export DATA_ROOT="${DATA_ROOT:-/data/opencode-a2a}"

export_if_present() {
  local target="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    export "${target}=${value}"
  fi
}

export_if_present "OPENCODE_PROVIDER_ID" "$OPENCODE_PROVIDER_ID_INPUT"
export_if_present "OPENCODE_MODEL_ID" "$OPENCODE_MODEL_ID_INPUT"
export_if_present "OPENCODE_LSP" "$OPENCODE_LSP_INPUT"
export_if_present "REPO_URL" "$REPO_URL_INPUT"
export_if_present "REPO_BRANCH" "$REPO_BRANCH_INPUT"
export_if_present "OPENCODE_TIMEOUT" "$OPENCODE_TIMEOUT_INPUT"
export_if_present "OPENCODE_TIMEOUT_STREAM" "$OPENCODE_TIMEOUT_STREAM_INPUT"
export_if_present "GIT_IDENTITY_NAME" "$GIT_IDENTITY_NAME_INPUT"
export_if_present "GIT_IDENTITY_EMAIL" "$GIT_IDENTITY_EMAIL_INPUT"
export_if_present "DATA_ROOT" "$DATA_ROOT_INPUT"

export OPENCODE_BIND_HOST="${OPENCODE_BIND_HOST:-127.0.0.1}"
export OPENCODE_LOG_LEVEL="${OPENCODE_LOG_LEVEL:-DEBUG}"
export OPENCODE_EXTRA_ARGS="${OPENCODE_EXTRA_ARGS:-}"
export OPENCODE_LSP="${OPENCODE_LSP:-false}"

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
export A2A_LOG_PAYLOADS="${A2A_LOG_PAYLOADS:-false}"
export A2A_LOG_BODY_LIMIT="${A2A_LOG_BODY_LIMIT:-0}"
export_if_present "A2A_LOG_LEVEL" "$A2A_LOG_LEVEL_INPUT"
export_if_present "A2A_STREAMING" "$A2A_STREAMING_INPUT"
export_if_present "A2A_LOG_PAYLOADS" "$A2A_LOG_PAYLOADS_INPUT"
export_if_present "A2A_LOG_BODY_LIMIT" "$A2A_LOG_BODY_LIMIT_INPUT"

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
"${SCRIPT_DIR}/deploy/setup_instance.sh" "$PROJECT_NAME"
FORCE_RESTART="$FORCE_RESTART" "${SCRIPT_DIR}/deploy/enable_instance.sh" "$PROJECT_NAME"
