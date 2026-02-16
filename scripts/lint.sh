#!/usr/bin/env bash
set -euo pipefail

# Run the repo canonical lint pipeline to keep local and CI checks identical.
uv run pre-commit run --all-files
