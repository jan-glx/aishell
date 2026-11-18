#!/usr/bin/env bash
# Scan the complete, fully pinned MCP dependency lock without installing it.
# Install pip-audit in a separate audit environment; not the production venv.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v pip-audit >/dev/null || { echo 'pip-audit not on PATH; install it in an isolated venv first' >&2; exit 127; }
exec pip-audit --no-deps --requirement "$ROOT/requirements.lock"
