#!/usr/bin/env bash
set -euo pipefail
WORKFLOW_HOME="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${WORKFLOW_HOME}/scripts:${PYTHONPATH:-}"
exec uv run python -m scan "$@"
