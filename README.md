# توپ (Toop)

Telegram bot for managing a weekly 6v6 volleyball group: peer-rated player skills, fair attendance rotation, and balanced team generation.

## Context

- **Group**: ~20 players, weekly Monday 6pm sessions, max 14 attendees (6v6 + 1 sub per team)
- **Two teams**, no in-session rotation
- **Peer-driven ratings**: pairwise comparisons across 3 axes (attack, defense, setting)
- **Private voting**: votes never surface; admin sees only completion health, not content
- **Admin-in-the-loop**: admin chases low-completion folks in person, finalizes attendees, reviews suggested teams before publishing

## Core flow

1. Continuous pairwise voting via 1:1 DM with bot (standing queue of ~5 prompts per voter)
2. Admin dashboard shows voting health (last-voted, lifetime votes, pending, coverage gaps)
3. RSVPs for upcoming Monday session
4. Bot snapshots ratings + attendees, suggests balanced teams via constraint-aware snake-draft
5. Admin reviews, optionally swaps, publishes teams to group chat

## Stack

- Python 3.12+
- `python-telegram-bot` (async, Bot API)
- SQLite for storage
- `pydantic-settings` for config
- Bradley-Terry per-axis ratings, composite weights `0.4 attack / 0.4 defense / 0.2 setting`

---

## Setup (first run)

1. **Clone & install:**
   ```bash
   cd ~/Downloads/Coding/personal/toop
   uv sync --extra dev
   ```

2. **Create a bot** via [@BotFather](https://t.me/BotFather), grab the token.

3. **Find your numeric IDs:**
   - Your `ADMIN_TELEGRAM_ID`: DM [@userinfobot](https://t.me/userinfobot).
   - `GROUP_CHAT_ID`: add the bot to your group, then in any browser hit `https://api.telegram.org/bot<TOKEN>/getUpdates` after someone sends a message — `chat.id` is what you want (negative for groups, starts with `-100` for supergroups).

4. **Copy `.env`:**
   ```bash
   cp .env.example .env
   # fill BOT_TOKEN, ADMIN_TELEGRAM_ID, GROUP_CHAT_ID
   chmod 600 .env
   ```

5. **Test locally:**
   ```bash
   uv run python -m toop
   ```
   You should see `توپ starting (admin=…, group=…)` and the bot replies to `/start` in DM.

6. **Deploy to your VPS:** see `deploy/README.md`. TL;DR: add `VPS_SSH=user@host` to `.env`, run `make deploy`.

---

## Admin command cheat sheet

| Command | What it does |
| --- | --- |
| `/start` | Friendly intro (DM vs group has different text) |
| `/add_player @username "Display Name"` | Add to roster. Triggers calibration bootstrap. User must have DM'd the bot at least once. |
| `/remove_player @username` | Soft-delete from roster |
| `/list_players` | Numbered roster, calibration markers |
| `/open_session [YYYY-MM-DD]` | Opens session; auto-posts RSVP buttons to group |
| `/close_session` | Marks the active session done |
| `/sessions` | Recent sessions + status |
| `/lock_in @username` | Force-include a player in the next snapshot |
| `/snapshot` | Pick attendees, fit ratings, generate teams. Status → snapshotted. |
| `/teams` | Preview the snapshot in DM |
| `/swap @a @b` | Swap two players across teams, live metrics |
| `/publish` | Post teams to group, write attendance rows, status → published |
| `/refresh_ratings` | Force a BT refit |
| `/health` | Per-player voting health (completion-only — no vote contents) |
| `/coverage` | 10 least-sampled pairs (where more votes would help most) |
| `/nudge` | Returns DM-able templates per low-completion voter — admin sends manually |
| `/version` | Commit SHA + uptime |
| `/backup_db` | Online SQLite backup → `data/backups/` |

## Voter commands (DM only)

| Command | What it does |
| --- | --- |
| `/start` | Intro message |
| `/vote` | Show the next prompt; cycles to next on each tap |

Group `/vote` always redirects to DM: "DM me to vote 🤫".

---

## Group-chat voter intro (paste this when you announce the bot)

```
🏐 New: توپ helps us balance teams.

DM @<your_bot_username> and tap /vote — it'll ask you to compare
two teammates at a time (attack / defense / setting). Your individual
answers stay private; only the running tally is used.

The more you vote, the more accurate our teams get. Aim for ~5
prompts a week — takes 30 seconds.
```

---

## Troubleshooting

- **"Couldn't find @username" when adding a player** — they need to DM the bot `/start` first so Telegram lets us resolve their numeric ID.
- **RSVP buttons don't show** — make sure the bot is a member of `GROUP_CHAT_ID` and has permission to send messages.
- **Vote callbacks silently fail** — most often `BOT_TOKEN` is wrong or the bot isn't running. Check `logs/toop.log`.
- **Scheduled snapshot didn't fire Monday noon** — JobQueue requires the bot process to be alive at the scheduled time. Verify with `make logs` (look for `auto_snapshot scheduled`) or trigger manually with `/snapshot`. Note `SNAPSHOT_HOUR` is interpreted as UTC inside the container.
- **"weights sum to 0.95"** warning at startup — composite weights in `.env` don't add to 1.0. Bot still runs; ratings just scale slightly differently. Adjust to taste.
- **All-new roster shows "low confidence"** — expected during calibration. As pairs accumulate votes, confidence rises. Front-loading 15-20 votes per voter in week 1 is the recommended pattern.

---

## Privacy

See `docs/PRIVACY.md` for a full audit. TL;DR: by schema design, `vote_aggregates` (outcomes) and `answered_prompts` (voter dedupe) are never joined. Admin sees completion health, never vote content.

## Plan

See `docs/plans/done/2026-05-14-toop-volleyball-team-balancing-bot.md` for the implementation history.
