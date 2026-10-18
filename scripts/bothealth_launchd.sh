#!/usr/bin/env bash
# Launchd entry for the health job.
# ALERT_NTFY_TOPIC is read from an untracked file outside the repo.
# The value is exported for bothealth and is never printed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE="${KALSHI_ALERTS_ENV:-${HOME}/.kalshi-alerts.env}"

if [[ -f "$ENV_FILE" ]]; then
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?ALERT_NTFY_TOPIC=' "$ENV_FILE" | head -n 1 || true)"
    if [[ -n "$line" ]]; then
        value="${line#*=}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ ${#value} -ge 2 ]]; then
            first="${value%"${value#?}"}"
            last="${value#"${value%?}"}"
            if [[ "$first" == "$last" && ( "$first" == '"' || "$first" == "'" ) ]]; then
                value="${value#?}"
                value="${value%?}"
            fi
        fi
        if [[ -n "$value" ]]; then
            export ALERT_NTFY_TOPIC="$value"
        fi
    fi
fi

exec /bin/bash "$SCRIPT_DIR/bothealth.sh" "$@"
