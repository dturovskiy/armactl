#!/usr/bin/env bash
# Bootstrap development environment.
# Usage: ./scripts/bootstrap-dev.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "==> Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing armactl in editable mode with dev dependencies..."
pip install -e ".[dev]"

echo "==> Running tests..."
pytest

echo ""
echo "Done! Activate the venv with:"
echo "  source .venv/bin/activate"
