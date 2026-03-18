#!/usr/bin/env bash
# Docs: scripts/deploy_readme.md
# Deploy one isolated OpenCode + A2A systemd instance.
# Secret env vars are only required when persisting them during deploy or when
# setup actions need them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./shell_helpers.sh
source "${SCRIPT_DIR}/shell_helpers.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/deploy/provider_secret_env_keys.sh"
PROVIDER_SECRET_ENV_LIST="$(join_provider_secret_env_keys " | ")"

PROJECT_NAME=""
A2A_PORT_INPUT=""
A2A_HOST_INPUT=""
A2A_PUBLIC_URL_INPUT=""
A2A_STREAMING_INPUT=""
A2A_LOG_LEVEL_INPUT=""
A2A_OTEL_INSTRUMENTATION_ENABLED_INPUT=""
A2A_LOG_PAYLOADS_INPUT=""
A2A_LOG_BODY_LIMIT_INPUT=""
A2A_MAX_REQUEST_BODY_BYTES_INPUT=""
A2A_CANCEL_ABORT_TIMEOUT_SECONDS_INPUT=""
A2A_ENABLE_SESSION_SHELL_INPUT=""
A2A_STRICT_ISOLATION_INPUT=""
A2A_SYSTEMD_TASKS_MAX_INPUT=""
A2A_SYSTEMD_LIMIT_NOFILE_INPUT=""
A2A_SYSTEMD_MEMORY_MAX_INPUT=""
A2A_SYSTEMD_CPU_QUOTA_INPUT=""
DATA_ROOT_INPUT=""
OPENCODE_PROVIDER_ID_INPUT=""
OPENCODE_MODEL_ID_INPUT=""
OPENCODE_LSP_INPUT=""
OPENCODE_LOG_LEVEL_INPUT=""
REPO_URL_INPUT=""
REPO_BRANCH_INPUT=""
A2A_INSTALL_MODE_INPUT=""
A2A_RELEASE_VERSION_INPUT=""
OPENCODE_TIMEOUT_INPUT=""
OPENCODE_TIMEOUT_STREAM_INPUT=""
GIT_IDENTITY_NAME_INPUT=""
GIT_IDENTITY_EMAIL_INPUT=""
ENABLE_SECRET_PERSISTENCE_INPUT=""
UPDATE_A2A_INPUT=""
FORCE_RESTART_INPUT=""
DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS_INPUT=""
DEPLOY_HEALTHCHECK_INTERVAL_SECONDS_INPUT=""

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
    a2a_otel_instrumentation_enabled)
      A2A_OTEL_INSTRUMENTATION_ENABLED_INPUT="$value"
      ;;
    a2a_log_payloads)
      A2A_LOG_PAYLOADS_INPUT="$value"
      ;;
    a2a_log_body_limit)
      A2A_LOG_BODY_LIMIT_INPUT="$value"
      ;;
    a2a_max_request_body_bytes)
      A2A_MAX_REQUEST_BODY_BYTES_INPUT="$value"
      ;;
    a2a_cancel_abort_timeout_seconds)
      A2A_CANCEL_ABORT_TIMEOUT_SECONDS_INPUT="$value"
      ;;
    a2a_enable_session_shell)
      A2A_ENABLE_SESSION_SHELL_INPUT="$value"
      ;;
    a2a_strict_isolation)
      A2A_STRICT_ISOLATION_INPUT="$value"
      ;;
    a2a_systemd_tasks_max)
      A2A_SYSTEMD_TASKS_MAX_INPUT="$value"
      ;;
    a2a_systemd_limit_nofile)
      A2A_SYSTEMD_LIMIT_NOFILE_INPUT="$value"
      ;;
    a2a_systemd_memory_max)
      A2A_SYSTEMD_MEMORY_MAX_INPUT="$value"
      ;;
    a2a_systemd_cpu_quota)
      A2A_SYSTEMD_CPU_QUOTA_INPUT="$value"
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
    opencode_log_level)
      OPENCODE_LOG_LEVEL_INPUT="$value"
      ;;
    repo_url)
      REPO_URL_INPUT="$value"
      ;;
    repo_branch)
      REPO_BRANCH_INPUT="$value"
      ;;
    a2a_install_mode|install_mode)
      A2A_INSTALL_MODE_INPUT="$value"
      ;;
    a2a_release_version|release_version)
      A2A_RELEASE_VERSION_INPUT="$value"
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
    enable_secret_persistence)
      ENABLE_SECRET_PERSISTENCE_INPUT="$value"
      ;;
    update_a2a)
      UPDATE_A2A_INPUT="$value"
      ;;
    force_restart)
      FORCE_RESTART_INPUT="$value"
      ;;
    deploy_healthcheck_timeout_seconds)
      DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS_INPUT="$value"
      ;;
    deploy_healthcheck_interval_seconds)
      DEPLOY_HEALTHCHECK_INTERVAL_SECONDS_INPUT="$value"
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

if [[ -z "$PROJECT_NAME" ]]; then
  cat >&2 <<USAGE
Usage:
  [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [<PROVIDER_SECRET_ENV>=<key>] \
  ./scripts/deploy.sh project=<name> [data_root=<path>] [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] \
  [a2a_streaming=<bool>] [a2a_log_level=<level>] [a2a_otel_instrumentation_enabled=<bool>] \
  [a2a_log_payloads=<bool>] [a2a_log_body_limit=<int>] [a2a_max_request_body_bytes=<int>] \
  [a2a_cancel_abort_timeout_seconds=<seconds>] [a2a_enable_session_shell=<bool>] \
  [a2a_strict_isolation=<bool>] [a2a_systemd_tasks_max=<int>] [a2a_systemd_limit_nofile=<int>] \
  [a2a_systemd_memory_max=<value>] [a2a_systemd_cpu_quota=<value>] \
  [deploy_healthcheck_timeout_seconds=<seconds>] [deploy_healthcheck_interval_seconds=<seconds>] \
  [install_mode=<source|release>] [release_version=<version>] \
  [opencode_provider_id=<id>] [opencode_model_id=<id>] [opencode_lsp=<bool>] [opencode_log_level=<level>] \
  [repo_url=<url>] [repo_branch=<branch>] \
  [opencode_timeout=<seconds>] [opencode_timeout_stream=<seconds>] [git_identity_name=<name>] [enable_secret_persistence=<bool>] \
  [git_identity_email=<email>] [update_a2a=true] [force_restart=true]

Provider secret env vars:
  ${PROVIDER_SECRET_ENV_LIST}
USAGE
  exit 1
fi

export OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-server}"
export OPENCODE_CORE_DIR="${OPENCODE_CORE_DIR:-/opt/.opencode}"
export UV_PYTHON_DIR="${UV_PYTHON_DIR:-/opt/uv-python}"
export UV_PYTHON_DIR_GROUP="${UV_PYTHON_DIR_GROUP-opencode}"
export DATA_ROOT="${DATA_ROOT:-/data/opencode-a2a}"
export A2A_INSTALL_MODE="${A2A_INSTALL_MODE:-source}"
export A2A_RELEASE_VERSION="${A2A_RELEASE_VERSION:-}"
export A2A_RELEASE_ROOT="${A2A_RELEASE_ROOT:-/opt/opencode-a2a-release}"
export A2A_TOOL_DIR="${A2A_TOOL_DIR:-${A2A_RELEASE_ROOT}/tool}"
export A2A_TOOL_BIN_DIR="${A2A_TOOL_BIN_DIR:-${A2A_RELEASE_ROOT}/bin}"
export DEPLOY_HELPER_DIR="${DEPLOY_HELPER_DIR:-}"
export A2A_BIN="${A2A_BIN:-}"
export DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS="${DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS:-30}"
export DEPLOY_HEALTHCHECK_INTERVAL_SECONDS="${DEPLOY_HEALTHCHECK_INTERVAL_SECONDS:-1}"

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
export_if_present "A2A_INSTALL_MODE" "$A2A_INSTALL_MODE_INPUT"
export_if_present "A2A_RELEASE_VERSION" "$A2A_RELEASE_VERSION_INPUT"
export_if_present "OPENCODE_TIMEOUT" "$OPENCODE_TIMEOUT_INPUT"
export_if_present "OPENCODE_TIMEOUT_STREAM" "$OPENCODE_TIMEOUT_STREAM_INPUT"
export_if_present "GIT_IDENTITY_NAME" "$GIT_IDENTITY_NAME_INPUT"
export_if_present "GIT_IDENTITY_EMAIL" "$GIT_IDENTITY_EMAIL_INPUT"
export_if_present "DATA_ROOT" "$DATA_ROOT_INPUT"
export_if_present "OPENCODE_LOG_LEVEL" "$OPENCODE_LOG_LEVEL_INPUT"

export OPENCODE_BIND_HOST="${OPENCODE_BIND_HOST:-127.0.0.1}"
export OPENCODE_LOG_LEVEL="${OPENCODE_LOG_LEVEL:-WARNING}"
export OPENCODE_EXTRA_ARGS="${OPENCODE_EXTRA_ARGS:-}"
export OPENCODE_LSP="${OPENCODE_LSP:-false}"
export ENABLE_SECRET_PERSISTENCE="${ENABLE_SECRET_PERSISTENCE:-false}"

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

export A2A_LOG_LEVEL="${A2A_LOG_LEVEL:-WARNING}"
export A2A_STREAMING="${A2A_STREAMING:-true}"
export A2A_OTEL_INSTRUMENTATION_ENABLED="${A2A_OTEL_INSTRUMENTATION_ENABLED:-false}"
export A2A_LOG_PAYLOADS="${A2A_LOG_PAYLOADS:-false}"
export A2A_LOG_BODY_LIMIT="${A2A_LOG_BODY_LIMIT:-0}"
export A2A_MAX_REQUEST_BODY_BYTES="${A2A_MAX_REQUEST_BODY_BYTES:-1048576}"
export A2A_CANCEL_ABORT_TIMEOUT_SECONDS="${A2A_CANCEL_ABORT_TIMEOUT_SECONDS:-2.0}"
export A2A_ENABLE_SESSION_SHELL="${A2A_ENABLE_SESSION_SHELL:-false}"
export A2A_STRICT_ISOLATION="${A2A_STRICT_ISOLATION:-false}"
export A2A_SYSTEMD_TASKS_MAX="${A2A_SYSTEMD_TASKS_MAX:-512}"
export A2A_SYSTEMD_LIMIT_NOFILE="${A2A_SYSTEMD_LIMIT_NOFILE:-65536}"
export_if_present "A2A_LOG_LEVEL" "$A2A_LOG_LEVEL_INPUT"
export_if_present "A2A_STREAMING" "$A2A_STREAMING_INPUT"
export_if_present "A2A_OTEL_INSTRUMENTATION_ENABLED" "$A2A_OTEL_INSTRUMENTATION_ENABLED_INPUT"
export_if_present "A2A_LOG_PAYLOADS" "$A2A_LOG_PAYLOADS_INPUT"
export_if_present "A2A_LOG_BODY_LIMIT" "$A2A_LOG_BODY_LIMIT_INPUT"
export_if_present "A2A_MAX_REQUEST_BODY_BYTES" "$A2A_MAX_REQUEST_BODY_BYTES_INPUT"
export_if_present "A2A_CANCEL_ABORT_TIMEOUT_SECONDS" "$A2A_CANCEL_ABORT_TIMEOUT_SECONDS_INPUT"
export_if_present "A2A_ENABLE_SESSION_SHELL" "$A2A_ENABLE_SESSION_SHELL_INPUT"
export_if_present "A2A_STRICT_ISOLATION" "$A2A_STRICT_ISOLATION_INPUT"
export_if_present "A2A_SYSTEMD_TASKS_MAX" "$A2A_SYSTEMD_TASKS_MAX_INPUT"
export_if_present "A2A_SYSTEMD_LIMIT_NOFILE" "$A2A_SYSTEMD_LIMIT_NOFILE_INPUT"
export_if_present "A2A_SYSTEMD_MEMORY_MAX" "$A2A_SYSTEMD_MEMORY_MAX_INPUT"
export_if_present "A2A_SYSTEMD_CPU_QUOTA" "$A2A_SYSTEMD_CPU_QUOTA_INPUT"
export_if_present "ENABLE_SECRET_PERSISTENCE" "$ENABLE_SECRET_PERSISTENCE_INPUT"
export_if_present "DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS" "$DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS_INPUT"
export_if_present "DEPLOY_HEALTHCHECK_INTERVAL_SECONDS" "$DEPLOY_HEALTHCHECK_INTERVAL_SECONDS_INPUT"

case "${A2A_INSTALL_MODE,,}" in
  source|release)
    A2A_INSTALL_MODE="${A2A_INSTALL_MODE,,}"
    ;;
  *)
    echo "A2A_INSTALL_MODE must be source or release; got: ${A2A_INSTALL_MODE}" >&2
    exit 1
    ;;
esac

if [[ "$A2A_INSTALL_MODE" == "release" ]]; then
  export DEPLOY_HELPER_DIR="${DEPLOY_HELPER_DIR:-${A2A_RELEASE_ROOT}/runtime}"
  export A2A_BIN="${A2A_BIN:-${A2A_TOOL_BIN_DIR}/opencode-a2a-server}"
  export OPENCODE_A2A_DIR="${DEPLOY_HELPER_DIR}"
else
  export DEPLOY_HELPER_DIR="${DEPLOY_HELPER_DIR:-${OPENCODE_A2A_DIR}/scripts/deploy}"
fi

UPDATE_A2A="false"
FORCE_RESTART="false"
if [[ -n "$UPDATE_A2A_INPUT" ]] && is_truthy "$UPDATE_A2A_INPUT"; then
  UPDATE_A2A="true"
fi
if [[ -n "$FORCE_RESTART_INPUT" ]] && is_truthy "$FORCE_RESTART_INPUT"; then
  FORCE_RESTART="true"
fi

if is_truthy "$A2A_ENABLE_SESSION_SHELL"; then
  echo "WARNING: A2A_ENABLE_SESSION_SHELL=true enables high-risk opencode.sessions.shell." >&2
  if ! is_truthy "$A2A_STRICT_ISOLATION"; then
    echo "WARNING: Recommend setting a2a_strict_isolation=true for shell-enabled systemd instances." >&2
  fi
fi

ensure_sudo_ready

if [[ "$A2A_INSTALL_MODE" == "release" ]]; then
  if [[ "$UPDATE_A2A" == "true" ]]; then
    export FORCE_A2A_RELEASE_INSTALL="true"
  fi
  "${SCRIPT_DIR}/deploy/install_release_runtime.sh"
elif [[ "$UPDATE_A2A" == "true" ]]; then
  "${SCRIPT_DIR}/deploy/update_a2a.sh"
fi

"${SCRIPT_DIR}/deploy/install_units.sh"
"${SCRIPT_DIR}/deploy/setup_instance.sh" "$PROJECT_NAME"
FORCE_RESTART="$FORCE_RESTART" "${SCRIPT_DIR}/deploy/enable_instance.sh" "$PROJECT_NAME"
