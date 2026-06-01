# Deploying توپ to a VPS

This mirrors the `persian-translator-bot` pattern: SSH in, `git pull`, `docker compose up -d --build`.

## One-time VPS setup

Run these once on your VPS (Ubuntu/Debian assumed):

```bash
# Install Docker + compose if not already
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER     # log out + back in afterwards

# Make /opt writable by your user
sudo mkdir -p /opt
sudo chown $USER:$USER /opt
```

## Local prerequisites

1. SSH key auth working: `ssh user@your-vps` should connect without a password.
2. `.env` exists in the repo root with at least `BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `GROUP_CHAT_ID`, and `VPS_SSH=user@your-vps-ip`.

## Deploy

From the repo root on your laptop:

```bash
make deploy
# or directly:
./deploy.sh
```

The script:
1. Reads `VPS_SSH` from `.env`.
2. `scp`s your local `.env` to `/opt/toop/.env` on the VPS.
3. Clones (or `git pull --ff-only`s) the repo on the VPS at `/opt/toop`.
4. Runs `docker compose up -d --build` with `GIT_SHA` baked into the image so `/version` shows the right commit.

## Day-to-day

```bash
make logs    # tails docker compose logs over SSH
make ssh     # opens an interactive SSH session on the VPS

# Or directly on the VPS, after `make ssh`:
cd /opt/toop
docker compose ps
docker compose logs --tail=200
docker compose restart bot
docker compose down
```

## Backups

`/backup_db` writes a timestamped copy under `data/backups/` inside the container, which lands at `/opt/toop/data/backups/` on the VPS (the `./data:/app/data` volume mount). To pull a backup home:

```bash
scp user@vps:/opt/toop/data/backups/toop-*.db ~/Downloads/
```

## Troubleshooting

- **`Couldn't find @username` after deploy:** users have to have DM'd the bot at least once before `bot.get_chat` can resolve their handle. Send them the bot link and ask them to `/start`.
- **`docker: not found` on VPS:** rerun the one-time setup section. Don't forget to log out + back in so the `docker` group takes effect.
- **`git pull --ff-only` fails after a force-push:** `make ssh`, then `cd /opt/toop && git fetch && git reset --hard origin/main`. The deploy script already does `git reset --hard origin/main`, so this should be rare.
- **Bot crashes immediately:** `make logs` — the most common cause is `RuntimeError: Missing required env vars`, i.e. `.env` is missing fields. Edit `.env` locally, rerun `make deploy`.
- **`/version` shows `unknown`:** `GIT_SHA` build-arg wasn't passed. `make deploy` always sets it from `git rev-parse --short HEAD`; if you ran `docker compose build` manually on the VPS without it, redeploy via `make deploy` from your laptop.
