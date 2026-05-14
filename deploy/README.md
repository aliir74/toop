# Deploying توپ on macOS via LaunchAgent

## One-time install

```bash
# 1. Make sure .env exists in the repo root with BOT_TOKEN, ADMIN_TELEGRAM_ID, GROUP_CHAT_ID.
# 2. Copy the plist into LaunchAgents:
cp deploy/com.aliirani.toop.plist ~/Library/LaunchAgents/

# 3. Load it (also starts the bot):
launchctl load -w ~/Library/LaunchAgents/com.aliirani.toop.plist

# 4. Verify it's running:
launchctl list | grep com.aliirani.toop
tail -f logs/toop.log
```

## Day-to-day commands

```bash
# Restart after pulling code changes
launchctl unload ~/Library/LaunchAgents/com.aliirani.toop.plist
launchctl load ~/Library/LaunchAgents/com.aliirani.toop.plist

# Stop indefinitely (the -w flag persists across reboots)
launchctl unload -w ~/Library/LaunchAgents/com.aliirani.toop.plist

# Inspect logs
tail -f logs/toop.log
```

## Architecture

- `com.aliirani.toop.plist` — LaunchAgent definition. `KeepAlive=true` and `ThrottleInterval=30` mean the bot auto-restarts within 30s if it crashes.
- `launch-toop.sh` — sources `.env`, rotates `logs/toop.log` once it exceeds 50MB, then `exec`s `uv run python -m toop` so PID inheritance lets LaunchAgent supervise the actual Python process.
- `logs/toop.log` — combined stdout/stderr. Manual rotation kicks in at 50MB.
- `data/toop.db` — SQLite file. Back up with `/backup_db` (admin command) before any risky migration.

## Updating to a new Python or dep version

```bash
# In repo root:
uv sync --extra dev  # updates .venv

# Then bounce the bot:
launchctl unload ~/Library/LaunchAgents/com.aliirani.toop.plist
launchctl load ~/Library/LaunchAgents/com.aliirani.toop.plist
```

## Troubleshooting

- **Bot doesn't start**: `tail logs/toop.log` first. The most common issue is a missing or invalid `.env` (causes `RuntimeError: Missing required env vars`).
- **KeepAlive loop**: if the bot crashes immediately and LaunchAgent keeps restarting, `launchctl unload -w` to stop it, fix the issue, then re-load.
- **Permission denied for `.env`**: ensure `.env` is `chmod 600` and owned by the user running LaunchAgent.
- **`uv` not found**: the plist hard-codes `/Users/aliirani/.local/bin/uv`. If you installed uv elsewhere, update both the plist and `launch-toop.sh`.
