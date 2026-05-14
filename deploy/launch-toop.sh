#!/usr/bin/env bash
# Launches the توپ Telegram bot under LaunchAgent supervision.
# Sources .env, starts via uv. Stays in foreground so LaunchAgent KeepAlive works.

set -euo pipefail

REPO_DIR="/Users/aliirani/Downloads/Coding/personal/toop"
cd "$REPO_DIR"

# Reap stale processes (defensive — LaunchAgent KeepAlive handles most cases)
mkdir -p "$REPO_DIR/logs"
mkdir -p "$REPO_DIR/data"

# Load env
if [[ ! -f .env ]]; then
    echo "[$(date)] FATAL: $REPO_DIR/.env not found" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

# Trim old logs if they grew past 50MB (simple rotation, no logrotate dep)
LOG_FILE="$REPO_DIR/logs/toop.log"
if [[ -f "$LOG_FILE" ]]; then
    log_size=$(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
    if (( log_size > 52428800 )); then
        mv "$LOG_FILE" "${LOG_FILE}.1"
    fi
fi

echo "[$(date)] توپ launching"
exec /Users/aliirani/.local/bin/uv run python -m toop
