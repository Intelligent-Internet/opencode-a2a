#!/usr/bin/env bash

# Shared provider secret environment keys for deploy scripts.
PROVIDER_SECRET_ENV_KEYS=(
  GOOGLE_GENERATIVE_AI_API_KEY
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  AZURE_OPENAI_API_KEY
  OPENROUTER_API_KEY
)

join_provider_secret_env_keys() {
  local separator="${1:- | }"
  local result=""
  local key=""
  for key in "${PROVIDER_SECRET_ENV_KEYS[@]}"; do
    if [[ -n "$result" ]]; then
      result+="${separator}"
    fi
    result+="${key}"
  done
  echo "$result"
}

provider_secret_env_for_cli_key() {
  case "${1,,}" in
    google_generative_ai_api_key|google_api_key)
      echo "GOOGLE_GENERATIVE_AI_API_KEY"
      ;;
    openai_api_key)
      echo "OPENAI_API_KEY"
      ;;
    anthropic_api_key)
      echo "ANTHROPIC_API_KEY"
      ;;
    azure_openai_api_key)
      echo "AZURE_OPENAI_API_KEY"
      ;;
    openrouter_api_key)
      echo "OPENROUTER_API_KEY"
      ;;
    *)
      return 1
      ;;
  esac
}

required_provider_secret_env_key() {
  case "${1,,}" in
    google)
      echo "GOOGLE_GENERATIVE_AI_API_KEY"
      ;;
    openai)
      echo "OPENAI_API_KEY"
      ;;
    anthropic)
      echo "ANTHROPIC_API_KEY"
      ;;
    azure|azure-openai|azure_openai|azureopenai)
      echo "AZURE_OPENAI_API_KEY"
      ;;
    openrouter)
      echo "OPENROUTER_API_KEY"
      ;;
    *)
      return 1
      ;;
  esac
}
