#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")
VENV_DIR="$PROJECT_ROOT/.venv"
STAMP_FILE="$VENV_DIR/.armactl-pyproject.sha256"
MODE=""
CHECK_ONLY=0
SHOW_HELP=0
CHECK_REASON=""

log() {
    printf '%s\n' "$*"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

print_help() {
    cat <<'EOF'
Usage: ./scripts/bootstrap.sh [--prod|--web|--dev] [--check]

Prepare or verify the repo-local armactl virtual environment.

Modes:
  --prod    Install runtime CLI/TUI dependencies. This is the default.
  --web     Install runtime CLI/TUI plus web dashboard dependencies.
  --dev     Install development plus web dependencies.

Options:
  --check   Verify the existing .venv, dependency stamp, requested mode, and
            importability without running apt, sudo, pip, or mutating files.
  -h, --help
            Show this help text without entering the installer path.

Recovery contract:
  If --check reports stale or missing bootstrap state, run the same mode without
  --check from an interactive shell, for example:

      ./scripts/bootstrap.sh --web

  Do not hand-edit .venv/.armactl-pyproject.sha256.
EOF
}

parse_args() {
    for arg in "$@"; do
        case "$arg" in
            --prod|--web|--dev)
                if [ -n "$MODE" ]; then
                    fail "Only one bootstrap mode may be selected."
                fi
                MODE="$arg"
                ;;
            --check)
                CHECK_ONLY=1
                ;;
            -h|--help)
                SHOW_HELP=1
                ;;
            *)
                fail "Unknown option: $arg (allowed: --prod, --web, --dev, --check, --help)"
                ;;
        esac
    done
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

pyproject_hash() {
    sha256sum "$PROJECT_ROOT/pyproject.toml" | awk '{print $1}'
}

stamp_mode() {
    if [ -n "$MODE" ]; then
        printf '%s\n' "$MODE"
    else
        printf '%s\n' "--prod"
    fi
}

mode_satisfies() {
    installed_mode="${1:-}"
    requested_mode="${2:-}"

    case "$requested_mode" in
        ""|--prod)
            return 0
            ;;
        --web)
            [ "$installed_mode" = "--web" ] || [ "$installed_mode" = "--dev" ]
            return
            ;;
        --dev)
            [ "$installed_mode" = "--dev" ]
            return
            ;;
        *)
            return 1
            ;;
    esac
}

runtime_import_check() {
    requested_mode="$(stamp_mode)"
    case "$requested_mode" in
        --dev)
            "$VENV_DIR/bin/python" -c "import encodings, armactl, fastapi, uvicorn, argon2, multipart, pytest, ruff" >/dev/null 2>&1
            ;;
        --web)
            "$VENV_DIR/bin/python" -c "import encodings, armactl, fastapi, uvicorn, argon2, multipart" >/dev/null 2>&1
            ;;
        --prod)
            "$VENV_DIR/bin/python" -c "import encodings, armactl" >/dev/null 2>&1
            ;;
        *)
            return 1
            ;;
    esac
}

bootstrap_needs_refresh() {
    requested_mode="$(stamp_mode)"

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        CHECK_REASON="runtime Python is missing or not executable at $VENV_DIR/bin/python"
        return 0
    fi

    if [ ! -f "$STAMP_FILE" ]; then
        CHECK_REASON="dependency stamp is missing at $STAMP_FILE"
        return 0
    fi

    if ! read -r stamped_hash stamped_mode < "$STAMP_FILE"; then
        CHECK_REASON="dependency stamp is unreadable at $STAMP_FILE"
        return 0
    fi

    if [ -z "${stamped_hash:-}" ]; then
        CHECK_REASON="dependency stamp is malformed at $STAMP_FILE"
        return 0
    fi

    current_hash="$(pyproject_hash)"
    if [ "$stamped_hash" != "$current_hash" ]; then
        CHECK_REASON="pyproject.toml hash changed since the dependency stamp was written"
        return 0
    fi

    if ! mode_satisfies "${stamped_mode:-}" "$requested_mode"; then
        CHECK_REASON="dependency stamp mode '${stamped_mode:-unknown}' does not satisfy requested mode '$requested_mode'"
        return 0
    fi

    if ! runtime_import_check; then
        CHECK_REASON="runtime import check failed for requested mode '$requested_mode'"
        return 0
    fi

    return 1
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

parse_args "$@"

if [ "$SHOW_HELP" -eq 1 ]; then
    print_help
    exit 0
fi

if [ ! -f "$PROJECT_ROOT/pyproject.toml" ]; then
    fail "pyproject.toml not found; run this script from the armactl repo."
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    requested_mode="$(stamp_mode)"
    if bootstrap_needs_refresh; then
        log "armactl bootstrap check: refresh needed for $requested_mode."
        log "Reason: $CHECK_REASON."
        log ""
        log "Supported recovery:"
        log "  ./scripts/bootstrap.sh $requested_mode"
        log ""
        log "Then re-run the normal wrapper command. Do not edit $STAMP_FILE by hand."
        exit 1
    fi
    log "armactl bootstrap check: OK for $requested_mode."
    exit 0
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

case "$(stamp_mode)" in
    --dev)
        log "==> Installing armactl with dev and web dependencies..."
        "$VENV_PY" -m pip install -e ".[dev,web]"
        ;;
    --web)
        log "==> Installing armactl with web dependencies..."
        "$VENV_PY" -m pip install -e ".[web]"
        ;;
    --prod)
        log "==> Installing armactl..."
        "$VENV_PY" -m pip install -e .
        ;;
esac

if [ ! -x "$VENV_ARM" ]; then
    fail "armactl executable was not created in .venv"
fi

chmod +x "$PROJECT_ROOT/armactl" 2>/dev/null || true
chmod +x "$PROJECT_ROOT/scripts/run-tui" 2>/dev/null || true
chmod +x "$PROJECT_ROOT/scripts/run-web" 2>/dev/null || true
chmod +x "$PROJECT_ROOT/scripts/run-host-tests" 2>/dev/null || true
printf '%s %s\n' "$(pyproject_hash)" "$(stamp_mode)" > "$STAMP_FILE"

log ""
log "Done."
log ""
log "Use repo-local launcher:"
log "  ./armactl         # launch TUI"
log "  ./armactl --help"
log "  ./armactl detect"
log "  ./armactl install"
log ""
log "Or run TUI:"
log "  ./scripts/run-tui"
log ""
log "Or run the web smoke launcher:"
log "  ./scripts/run-web --dev --data-root /tmp/armactl-web-dev"
log ""
log "Run host checks:"
log "  ./scripts/run-host-tests"
