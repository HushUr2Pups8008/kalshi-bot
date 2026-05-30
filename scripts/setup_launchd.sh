#!/usr/bin/env bash
# PLATFORM: macOS only.
# Windows: use scripts/setup_daily_task.ps1
# Linux:   add a crontab entry manually (see usage below).
#
# Installs a launchd agent that runs scripts/daily_review.py once per day.
#
# Usage:
#   bash scripts/setup_launchd.sh
#   bash scripts/setup_launchd.sh --time 14:30
#   bash scripts/setup_launchd.sh --uninstall
#
# The agent runs as the current user (Login Items / LaunchAgents), not root.
# To verify after install:
#   launchctl list | grep kalshibot
#
set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────

LABEL="com.kalshibot.dailyreview"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"

# Resolve repo root (parent of the directory containing this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Argument parsing ───────────────────────────────────────────────────────────

TIME="09:00"
UNINSTALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --time)
            TIME="$2"
            shift 2
            ;;
        --time=*)
            TIME="${1#*=}"
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        -h|--help)
            sed -n '/^# Usage/,/^$/p' "${BASH_SOURCE[0]}" | head -n 5
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ── Uninstall path ─────────────────────────────────────────────────────────────

if [[ "$UNINSTALL" == true ]]; then
    if launchctl list "$LABEL" &>/dev/null; then
        launchctl remove "$LABEL"
        echo "Removed launchd agent: $LABEL"
    else
        echo "Agent not loaded: $LABEL"
    fi
    if [[ -f "$PLIST_PATH" ]]; then
        rm "$PLIST_PATH"
        echo "Deleted plist: $PLIST_PATH"
    fi
    exit 0
fi

# ── Validate --time ────────────────────────────────────────────────────────────

if ! echo "$TIME" | grep -qE '^[0-9]{1,2}:[0-9]{2}$'; then
    echo "Error: --time must be in HH:MM format (e.g. 09:00 or 14:30)" >&2
    exit 1
fi

HOUR=$(echo "$TIME" | cut -d: -f1 | sed 's/^0//')
MINUTE=$(echo "$TIME" | cut -d: -f2 | sed 's/^0//')

# ── Resolve Python ─────────────────────────────────────────────────────────────

VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
DAILY_REVIEW_PY="$REPO_ROOT/scripts/daily_review.py"

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Error: virtualenv python not found at $VENV_PYTHON" >&2
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ ! -f "$DAILY_REVIEW_PY" ]]; then
    echo "Error: daily_review.py not found at $DAILY_REVIEW_PY" >&2
    exit 1
fi

# ── Write plist ────────────────────────────────────────────────────────────────

mkdir -p "$PLIST_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${DAILY_REVIEW_PY}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_ROOT}</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>
        <key>Minute</key>
        <integer>${MINUTE}</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${REPO_ROOT}/logs/app/daily_review_launchd.stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${REPO_ROOT}/logs/app/daily_review_launchd.stderr.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

echo "Wrote plist: $PLIST_PATH"

# ── Load agent ─────────────────────────────────────────────────────────────────

# Remove any previously loaded version before reloading
if launchctl list "$LABEL" &>/dev/null; then
    launchctl remove "$LABEL"
fi

launchctl load "$PLIST_PATH"

echo "Loaded launchd agent: $LABEL"
echo "Scheduled to run daily_review.py at ${TIME}"
echo ""
echo "Verify with:  launchctl list | grep kalshibot"
echo "Uninstall with:  bash scripts/setup_launchd.sh --uninstall"
