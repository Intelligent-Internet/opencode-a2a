#!/usr/bin/env bash
# Deploy an isolated OpenCode + A2A instance (systemd services).
# Usage: ./deploy.sh <project_name> <github_token> <a2a_bearer_token> [a2a_port] [a2a_host]
# Or:    ./deploy.sh project:<name> github_token:<token> a2a_bearer_token:<token> [a2a_port:<port>] [a2a_host:<host>]
# Requires: sudo access to write systemd units and create users/directories.
#
# Key environment variables (optional):
# - OPENCODE_A2A_DIR: path to opencode-a2a-serve repo (default: /opt/opencode-a2a/opencode-a2a-serve)
# - OPENCODE_CORE_DIR: path to opencode core (default: /opt/.opencode)
# - UV_PYTHON_DIR: path to uv python pool (default: /opt/uv-python)
# - DATA_ROOT: projects root (default: /data/projects)
# - OPENCODE_BIND_HOST/OPENCODE_BIND_PORT/OPENCODE_LOG_LEVEL/OPENCODE_EXTRA_ARGS
# - A2A_HOST/A2A_PORT/A2A_PUBLIC_URL/A2A_LOG_LEVEL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT_NAME=""
GH_TOKEN=""
A2A_BEARER_TOKEN=""
A2A_PORT_INPUT=""
A2A_HOST_INPUT=""
POSITIONAL_ARGS=()

for arg in "$@"; do
  if [[ "$arg" == *:* ]]; then
    key="${arg%%:*}"
    value="${arg#*:}"
  elif [[ "$arg" == *=* ]]; then
    key="${arg%%=*}"
    value="${arg#*=}"
  else
    POSITIONAL_ARGS+=("$arg")
    continue
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
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" && ${#POSITIONAL_ARGS[@]} -gt 0 ]]; then
  PROJECT_NAME="${POSITIONAL_ARGS[0]}"
fi
if [[ -z "$GH_TOKEN" && ${#POSITIONAL_ARGS[@]} -gt 1 ]]; then
  GH_TOKEN="${POSITIONAL_ARGS[1]}"
fi
if [[ -z "$A2A_BEARER_TOKEN" && ${#POSITIONAL_ARGS[@]} -gt 2 ]]; then
  A2A_BEARER_TOKEN="${POSITIONAL_ARGS[2]}"
fi
if [[ -z "$A2A_PORT_INPUT" && ${#POSITIONAL_ARGS[@]} -gt 3 ]]; then
  A2A_PORT_INPUT="${POSITIONAL_ARGS[3]}"
fi
if [[ -z "$A2A_HOST_INPUT" && ${#POSITIONAL_ARGS[@]} -gt 4 ]]; then
  A2A_HOST_INPUT="${POSITIONAL_ARGS[4]}"
fi

if [[ -z "$PROJECT_NAME" || -z "$GH_TOKEN" || -z "$A2A_BEARER_TOKEN" ]]; then
  echo "Usage: $0 <project_name> <github_token> <a2a_bearer_token> [a2a_port] [a2a_host]" >&2
  echo "   or: $0 project:<name> github_token:<token> a2a_bearer_token:<token> [a2a_port:<port>] [a2a_host:<host>]" >&2
  exit 1
fi

export OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-serve}"
export OPENCODE_CORE_DIR="${OPENCODE_CORE_DIR:-/opt/.opencode}"
export UV_PYTHON_DIR="${UV_PYTHON_DIR:-/opt/uv-python}"
export DATA_ROOT="${DATA_ROOT:-/data/projects}"

export OPENCODE_BIND_HOST="${OPENCODE_BIND_HOST:-127.0.0.1}"
export OPENCODE_LOG_LEVEL="${OPENCODE_LOG_LEVEL:-INFO}"
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
export A2A_PUBLIC_URL="${A2A_PUBLIC_URL:-http://${A2A_HOST}:${A2A_PORT}}"
export A2A_LOG_LEVEL="${A2A_LOG_LEVEL:-info}"

"${SCRIPT_DIR}/deploy/install_units.sh"
"${SCRIPT_DIR}/deploy/setup_instance.sh" "$PROJECT_NAME" "$GH_TOKEN" "$A2A_BEARER_TOKEN"
"${SCRIPT_DIR}/deploy/enable_instance.sh" "$PROJECT_NAME"
