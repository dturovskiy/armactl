#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")
VENV_DIR="$PROJECT_ROOT/.venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/armactl"
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
    fail "pyproject.toml not found. Put this script in scripts/bootstrap.sh inside the armactl repo."
fi

if need_cmd sudo && [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

if ! need_cmd apt-get; then
    fail "This installer currently supports Ubuntu/Debian only (apt-get required)."
fi

log "==> Installing system dependencies..."
$SUDO apt-get update
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-venv \
    python3-pip

if ! need_cmd python3; then
    fail "python3 is still not available after apt install."
fi

if ! python_version_ok; then
    fail "Python 3.10+ is required, but python3 is $(python_version_str)."
fi

if [ ! -d "$VENV_DIR" ]; then
    log "==> Creating virtual environment: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    log "==> Reusing existing virtual environment: $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
VENV_ARM="$VENV_DIR/bin/armactl"

if [ ! -x "$VENV_PY" ]; then
    fail "Virtualenv Python not found: $VENV_PY"
fi

log "==> Upgrading pip tooling inside virtualenv..."
"$VENV_PY" -m pip install --upgrade pip setuptools wheel

cd "$PROJECT_ROOT"

case "$MODE" in
    --dev)
        log "==> Installing armactl in editable mode with dev dependencies..."
        "$VENV_PY" -m pip install -e ".[dev]"
        ;;
    ""|--prod)
        log "==> Installing armactl in editable mode..."
        "$VENV_PY" -m pip install -e .
        ;;
    *)
        fail "Unknown option: $MODE (allowed: --dev or --prod)"
        ;;
esac

if [ ! -x "$VENV_ARM" ]; then
    fail "armactl executable was not created: $VENV_ARM"
fi

log "==> Creating launcher: $LAUNCHER"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec "$VENV_ARM" "\$@"
EOF
chmod +x "$LAUNCHER"

PATH_OK=0
case ":$PATH:" in
    *":$BIN_DIR:"*) PATH_OK=1 ;;
esac

log ""
log "Done."
log "armactl installed into: $VENV_DIR"

if [ "$PATH_OK" -eq 1 ]; then
    log "Run:"
    log "  armactl --help"
else
    log "Run in this shell:"
    log "  export PATH=\"$BIN_DIR:\$PATH\""
    log "  armactl --help"
    log ""
    log "To make it permanent, add this line to ~/.profile or ~/.bashrc:"
    log "  export PATH=\"$BIN_DIR:\$PATH\""
fi

log ""
log "Next steps:"
log "  armactl --help"
log "  armactl detect"
log "  armactl install"
