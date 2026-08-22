#!/usr/bin/env bash
# Install (or refresh) repo-managed launchd plists for the local machine.
#
# Usage:
#   bash ops/launchd/install.sh                 # generate + install
#   bash ops/launchd/install.sh --print         # print substituted plists, do not install
#   bash ops/launchd/install.sh --uninstall     # bootout + remove installed plists
#   bash ops/launchd/install.sh --allow-drift   # TTY prompt before installing despite equivalence diff
#   bash ops/launchd/install.sh --allow-drift-confirm ALLOW-DRIFT
#
# Detects the local repo root and venv from the script location (resolves through
# symlinks). Substitutes @REPO_ROOT@, @VENV_PYTHON@, @GOVERNANCE_LLM_MODEL@ into
# the *.plist.template files and writes them to ~/Library/LaunchAgents/.
#
# GOVERNANCE_LLM_MODEL defaults to qwen2.5:7b so governance cannot evict
# the politics money-path model. Override with the env var:
#   GOVERNANCE_LLM_MODEL=qwen3:14b bash ops/launchd/install.sh
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
EQUIVALENCE_AUDIT="$REPO_ROOT/scripts/launchd_template_equivalence_audit.py"

# Default per-machine settings; override via environment.
GOVERNANCE_LLM_MODEL="${GOVERNANCE_LLM_MODEL:-qwen2.5:7b}"

# Templates we manage. Keep this list in sync with the *.plist.template files.
TEMPLATES=(
    "com.jake.kalshi-bot"
    "com.jake.kalshi-bothealth"
    "com.jake.kalshi-match-feedback-aggregator"
    "com.kalshi.db-backup"
    "com.kalshi.governance.fast"
    "com.kalshi.governance.deep"
)
LEGACY_LABELS=(
    "com.jake.kalshi-daily-review"
)

# ── Mode parsing ──────────────────────────────────────────────────────────────
MODE="install"
ALLOW_DRIFT=0
ALLOW_DRIFT_CONFIRM=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --print)       MODE="print"; shift ;;
        --uninstall)   MODE="uninstall"; shift ;;
        --allow-drift) ALLOW_DRIFT=1; shift ;;
        --allow-drift-confirm) ALLOW_DRIFT=1; ALLOW_DRIFT_CONFIRM="${2:-}"; shift 2 ;;
        *)
            echo "unknown argument: $1" >&2
            echo "usage: $0 [--print | --uninstall | --allow-drift | --allow-drift-confirm ALLOW-DRIFT]" >&2
            exit 2
            ;;
    esac
done

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "ERROR: venv python not found at $VENV_PYTHON" >&2
    echo "       run: python3.11 -m venv .venv && pip install -r requirements.txt" >&2
    exit 1
fi

if [[ ! -f "$EQUIVALENCE_AUDIT" ]]; then
    echo "ERROR: equivalence audit missing: $EQUIVALENCE_AUDIT" >&2
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
        if [[ "$ALLOW_DRIFT" != "1" ]]; then
            if ! "$VENV_PYTHON" "$EQUIVALENCE_AUDIT" --installed; then
                echo >&2
                echo "ERROR: rendered templates drift from installed launchd plists." >&2
                echo "Refusing install. Re-run with --allow-drift only after operator approval." >&2
                exit 1
            fi
        elif [[ -t 0 ]]; then
            echo "WARNING: --allow-drift bypasses production-config equivalence gate."
            read -r -p "Type ALLOW-DRIFT to continue: " confirmation
            if [[ "$confirmation" != "ALLOW-DRIFT" ]]; then
                echo "aborted"
                exit 1
            fi
        elif [[ "$ALLOW_DRIFT_CONFIRM" != "ALLOW-DRIFT" ]]; then
            echo "ERROR: --allow-drift in non-TTY mode requires --allow-drift-confirm ALLOW-DRIFT" >&2
            exit 1
        fi
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
        for label in "${LEGACY_LABELS[@]}"; do
            target="gui/$(id -u)/$label"
            local_dest="$INSTALL_DIR/$label.plist"
            if launchctl print "$target" >/dev/null 2>&1; then
                echo "  bootout legacy: $target"
                launchctl bootout "gui/$(id -u)" "$local_dest" 2>/dev/null || \
                    launchctl bootout "$target" 2>/dev/null || true
            fi
            if [[ -f "$local_dest" ]]; then
                rm -f "$local_dest"
                echo "  removed legacy: $local_dest"
            fi
        done
        echo
        echo "Done. Plists are generated but NOT loaded into launchctl."
        echo "To bootstrap a service, run e.g.:"
        echo "  launchctl bootstrap gui/\$(id -u) $INSTALL_DIR/com.jake.kalshi-bot.plist"
        ;;
    uninstall)
        for label in "${TEMPLATES[@]}" "${LEGACY_LABELS[@]}"; do
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
