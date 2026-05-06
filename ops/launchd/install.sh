#!/usr/bin/env bash
# Install (or refresh) repo-managed launchd plists for the local machine.
#
# Usage:
#   bash ops/launchd/install.sh                 # generate + install
#   bash ops/launchd/install.sh --print         # print substituted plists, do not install
#   bash ops/launchd/install.sh --uninstall     # bootout + remove installed plists
#
# Detects the local repo root and venv from the script location (resolves through
# symlinks). Substitutes @REPO_ROOT@, @VENV_PYTHON@, @GOVERNANCE_LLM_MODEL@ into
# the *.plist.template files and writes them to ~/Library/LaunchAgents/.
#
# GOVERNANCE_LLM_MODEL defaults to qwen3:14b. Override with the env var:
#   GOVERNANCE_LLM_MODEL=qwen3:8b bash ops/launchd/install.sh
#
# This script does NOT bootstrap (load) the plists into launchd. Ship/runbook
# steps do that explicitly. Generation only.

set -euo pipefail

# ── Resolve script and repo paths ─────────────────────────────────────────────
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_PATH/../.." && pwd -P)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
TEMPLATE_DIR="$REPO_ROOT/ops/launchd"
INSTALL_DIR="$HOME/Library/LaunchAgents"

# Default per-machine settings; override via environment.
GOVERNANCE_LLM_MODEL="${GOVERNANCE_LLM_MODEL:-qwen3:14b}"

# Templates we manage. Keep this list in sync with the *.plist.template files.
TEMPLATES=(
    "com.jake.kalshi-bot"
    "com.jake.kalshi-bothealth"
    "com.jake.kalshi-soak-check"
    "com.kalshi.db-backup"
    "com.kalshi.governance.fast"
    "com.kalshi.governance.deep"
)

# ── Mode parsing ──────────────────────────────────────────────────────────────
MODE="install"
case "${1:-}" in
    --print)     MODE="print" ;;
    --uninstall) MODE="uninstall" ;;
    "")          MODE="install" ;;
    *)
        echo "unknown argument: $1" >&2
        echo "usage: $0 [--print | --uninstall]" >&2
        exit 2
        ;;
esac

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "ERROR: venv python not found at $VENV_PYTHON" >&2
    echo "       run: python3.14 -m venv .venv && pip install -r requirements.txt" >&2
    exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
substitute() {
    # $1 = template path (without .template suffix, but the .template file is read)
    local label="$1"
    local template_file="$TEMPLATE_DIR/$label.plist.template"
    if [[ ! -f "$template_file" ]]; then
        echo "ERROR: template missing: $template_file" >&2
        return 1
    fi
    # sed -e style substitution. Path values are run through escape_sed first
    # because they may contain characters sensitive to sed's replacement syntax.
    local repo_esc venv_esc model_esc
    repo_esc=$(printf '%s' "$REPO_ROOT" | sed 's/[\&/]/\\&/g')
    venv_esc=$(printf '%s' "$VENV_PYTHON" | sed 's/[\&/]/\\&/g')
    model_esc=$(printf '%s' "$GOVERNANCE_LLM_MODEL" | sed 's/[\&/]/\\&/g')
    sed \
        -e "s/@REPO_ROOT@/$repo_esc/g" \
        -e "s/@VENV_PYTHON@/$venv_esc/g" \
        -e "s/@GOVERNANCE_LLM_MODEL@/$model_esc/g" \
        "$template_file"
}

# ── Modes ─────────────────────────────────────────────────────────────────────
case "$MODE" in
    print)
        for label in "${TEMPLATES[@]}"; do
            echo "# ── $label.plist (would be written to $INSTALL_DIR) ──"
            substitute "$label"
            echo
        done
        ;;
    install)
        mkdir -p "$INSTALL_DIR"
        echo "Repo root:           $REPO_ROOT"
        echo "Venv python:         $VENV_PYTHON"
        echo "Governance LLM:      $GOVERNANCE_LLM_MODEL"
        echo "Install destination: $INSTALL_DIR"
        echo
        for label in "${TEMPLATES[@]}"; do
            local_dest="$INSTALL_DIR/$label.plist"
            substitute "$label" > "$local_dest"
            chmod 644 "$local_dest"
            if plutil -lint "$local_dest" >/dev/null 2>&1; then
                echo "  generated: $local_dest  [plutil: OK]"
            else
                echo "  ERROR: plutil -lint failed on $local_dest" >&2
                plutil -lint "$local_dest" >&2 || true
                exit 1
            fi
        done
        echo
        echo "Done. Plists are generated but NOT loaded into launchctl."
        echo "To bootstrap a service, run e.g.:"
        echo "  launchctl bootstrap gui/\$(id -u) $INSTALL_DIR/com.jake.kalshi-bot.plist"
        ;;
    uninstall)
        for label in "${TEMPLATES[@]}"; do
            target="gui/$(id -u)/$label"
            local_dest="$INSTALL_DIR/$label.plist"
            if launchctl print "$target" >/dev/null 2>&1; then
                echo "  bootout: $target"
                launchctl bootout "gui/$(id -u)" "$local_dest" 2>/dev/null || \
                    launchctl bootout "$target" 2>/dev/null || true
            fi
            if [[ -f "$local_dest" ]]; then
                rm -f "$local_dest"
                echo "  removed: $local_dest"
            fi
        done
        ;;
esac
