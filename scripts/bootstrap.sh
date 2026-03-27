#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")
VENV_DIR="$PROJECT_ROOT/.venv"
MODE="${1:-}"

log() {
    printf '%s\n' "$*"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

python_version_ok() {
    python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

python_version_str() {
    python3 - <<'PY'
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
}

if [ ! -f "$PROJECT_ROOT/pyproject.toml" ]; then
    fail "pyproject.toml not found; run this script from the armactl repo."
fi

if need_cmd sudo && [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

if ! need_cmd apt-get; then
    fail "This installer currently supports Ubuntu/Debian only."
fi

log "==> Installing system dependencies..."
$SUDO apt-get update
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-venv \
    python3-pip

if ! need_cmd python3; then
    fail "python3 is not available after apt install."
fi

if ! python_version_ok; then
    fail "Python 3.10+ is required, but python3 is $(python_version_str)."
fi

if [ ! -d "$VENV_DIR" ]; then
    log "==> Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    log "==> Reusing existing virtual environment at $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
VENV_ARM="$VENV_DIR/bin/armactl"

log "==> Upgrading pip tooling inside virtualenv..."
"$VENV_PY" -m pip install --upgrade pip setuptools wheel

cd "$PROJECT_ROOT"

case "$MODE" in
    --dev)
        log "==> Installing armactl with dev dependencies..."
        "$VENV_PY" -m pip install -e ".[dev]"
        ;;
    ""|--prod)
        log "==> Installing armactl..."
        "$VENV_PY" -m pip install -e .
        ;;
    *)
        fail "Unknown option: $MODE (allowed: --dev or --prod)"
        ;;
esac

if [ ! -x "$VENV_ARM" ]; then
    fail "armactl executable was not created in .venv"
fi

chmod +x "$PROJECT_ROOT/armactl" 2>/dev/null || true
chmod +x "$PROJECT_ROOT/scripts/run-tui" 2>/dev/null || true

log ""
log "Done."
log ""
log "Use repo-local launcher:"
log "  ./armactl --help"
log "  ./armactl detect"
log "  ./armactl install"
log ""
log "Or run TUI:"
log "  ./scripts/run-tui"
