#!/usr/bin/env bash
# Shared shell helpers for deploy/bootstrap scripts.

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}
