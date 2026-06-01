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

GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

# Step 1: ensure the repo is present and up to date on the VPS. This must run
# before the .env scp so the destination directory exists on a fresh VPS.
ssh "$VPS_SSH" bash -s -- "$DEPLOY_PATH" "$REPO_URL" <<'REMOTE'
set -euo pipefail
DEPLOY_PATH="$1"
REPO_URL="$2"

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
REMOTE

# Step 2: now that the repo dir exists, copy .env into place (docker compose
# needs it before `up`). On a clean first deploy this previously ran before the
# clone and failed with "env file /opt/toop/.env not found".
scp "$SCRIPT_DIR/.env" "$VPS_SSH:$DEPLOY_PATH/.env"

# Step 3: build and start with the .env in place.
ssh "$VPS_SSH" bash -s -- "$DEPLOY_PATH" "$GIT_SHA" <<'REMOTE'
set -euo pipefail
DEPLOY_PATH="$1"
GIT_SHA="$2"

cd "$DEPLOY_PATH"

echo "Building and starting (GIT_SHA=$GIT_SHA)..."
GIT_SHA="$GIT_SHA" docker compose up -d --build

echo "Done! Container status:"
docker compose ps
REMOTE

echo "Deploy complete."
