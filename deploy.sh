#!/usr/bin/env bash
# Deploys توپ to the VPS. Mirrors the persian-translator-bot pattern.
# Requires VPS_SSH=user@host in .env.
set -euo pipefail

DEPLOY_PATH="/opt/toop"
REPO_URL="https://github.com/aliir74/toop.git"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    VPS_SSH=$(grep -E "^VPS_SSH=" "$SCRIPT_DIR/.env" | cut -d'=' -f2-)
fi

if [[ -z "${VPS_SSH:-}" ]]; then
    echo "Error: VPS_SSH not set. Add VPS_SSH=user@host to .env"
    exit 1
fi

echo "Deploying to $VPS_SSH:$DEPLOY_PATH ..."

scp "$SCRIPT_DIR/.env" "$VPS_SSH:$DEPLOY_PATH/.env" 2>/dev/null || true

GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

ssh "$VPS_SSH" bash -s -- "$DEPLOY_PATH" "$REPO_URL" "$GIT_SHA" <<'REMOTE'
set -euo pipefail
DEPLOY_PATH="$1"
REPO_URL="$2"
GIT_SHA="$3"

if [[ ! -d "$DEPLOY_PATH" ]]; then
    echo "Cloning repo..."
    sudo mkdir -p "$(dirname "$DEPLOY_PATH")"
    sudo chown "$USER:$USER" "$(dirname "$DEPLOY_PATH")"
    git clone "$REPO_URL" "$DEPLOY_PATH"
else
    echo "Pulling latest..."
    cd "$DEPLOY_PATH"
    git fetch origin
    git reset --hard origin/main
fi

cd "$DEPLOY_PATH"
mkdir -p data

echo "Building and starting (GIT_SHA=$GIT_SHA)..."
GIT_SHA="$GIT_SHA" docker compose up -d --build

echo "Done! Container status:"
docker compose ps
REMOTE

echo "Deploy complete."
