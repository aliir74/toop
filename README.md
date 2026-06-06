# توپ (Toop)

Telegram bot for managing a weekly 6v6 volleyball group: peer-rated player skills, fair attendance rotation, and balanced team generation.

## Context

- **Group**: ~20 players, weekly Monday 6pm sessions, max 14 attendees (6v6 + 1 sub per team)
- **Two teams**, no in-session rotation
- **Peer-driven ratings**: absolute 1–5 scoring across 6 indicators (حمله/attack, دریافت/receive, دفاع روی تور/block, پاسور/setting, سرویس/serve, جاگیری-تحرک/positioning)
- **Voter-linked scores**: each voter's 1–5 scores are stored with their identity so per-rater leniency/severity can be normalized out. Scores are not surfaced to the group, but (unlike the retired pairwise model) they are visible to the admin in the database.
- **Admin-in-the-loop**: admin chases low-completion folks in person, finalizes attendees, reviews suggested teams before publishing

## Core flow

1. Continuous scoring via 1:1 DM with bot: rate one teammate on one indicator at a time, 1–5 (re-tap to change a score)
2. Admin dashboard shows voting health (last-voted, lifetime ratings, pending, coverage gaps)
3. RSVPs for upcoming Monday session
4. Bot snapshots ratings + attendees, suggests balanced teams via constraint-aware snake-draft
5. Admin reviews, optionally swaps, publishes teams to group chat

## Stack

- Python 3.12+
- `python-telegram-bot` (async, Bot API)
- SQLite for storage
- `pydantic-settings` for config
- Rater-normalized 1–5 ratings (per-rater z-score + shrinkage), equal default composite weights (1/6 per indicator, env-tunable)

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
| `/add_player @username "Display Name"` | Add to roster by @handle. Triggers calibration bootstrap. User must have DM'd the bot at least once. |
| `/add_player <telegram_id> "Display Name"` | Add to roster by numeric id — the only way to add a player who has **no** Telegram username. The id must already be a known contact (they've DM'd `/start`); grab the ready-to-copy line from `/contacts`. |
| `/contacts` | Everyone who's DM'd the bot, flagging who's 🆕 not yet on the roster and emitting a copy-paste `/add_player <id> "Name"` line for each |
| `/add_ghost "Display Name"` | Add an accountless "ghost" player others can vote on **before** they join Telegram. Seeds calibration prompts. Link them to a real account later with `/link_player`. |
| `/link_player <ghost_id> <@username\|real_id>` | Merge a ghost into a real account once that person joins. Migrates all their votes, ratings, RSVPs, and attendance. The real account must have DM'd the bot first. |
| `/remove_player @username` | Soft-delete from roster |
| `/pause_voting <@username\|id> <2w\|10d>` | Temporarily pull a player from the rating pool — others stop being asked to rate them (they can still vote). Auto-expires. |
| `/disable_voting <@username\|id>` | Pull a player from the rating pool indefinitely (until `/enable_voting`). |
| `/enable_voting <@username\|id>` | Restore a player to the rating pool, clearing any pause or disable. |
| `/dk_report` | Per-player "don't know" rate, highest first — who the group can least confidently rate (pause candidates). |
| `/list_players` | Numbered roster, calibration markers, plus 👻 ghost / ⏸ paused / 🚫 disabled flags |
| `/rename` | Tap a player from inline buttons, then type the new display name (DM-only). Updates `display_name` only. |
| `/rename <@username\|telegram_id> "New Name"` | One-shot rename, skips the buttons |
| `/open_session [YYYY-MM-DD]` | Opens session; auto-posts RSVP buttons to group |
| `/close_session` | Marks the active session done |
| `/sessions` | Recent sessions + status |
| `/lock_in @username` | Force-include a player in the next snapshot |
| `/lock_in <telegram_id>` | Force-include by numeric id — the only way to lock in a player who has **no** Telegram username. The id must already be an active roster member (add via `/add_player <id>` first). |
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

Group `/vote` keeps the group clean: the bot DMs the sender their next prompt (or, if it can't DM them, posts a short self-deleting nudge) and removes the `/vote` command from the group when it has permission. It never leaves a standing reply quoting the command.

---

## Group-chat voter intro (paste this when you announce the bot)

```
🏐 New: توپ helps us balance teams.

DM @<your_bot_username> and tap /vote — it'll ask you to rate one
teammate at a time on one skill, from خیلی ضعیف to عالی. You can
re-tap any time to change a score.

The more you rate, the more accurate our teams get. Takes 30 seconds.
```

---

## Troubleshooting

- **"Couldn't find @username" when adding a player** — they need to DM the bot `/start` first so Telegram lets us resolve their numeric ID. Run `/contacts` to see who has already DM'd and is therefore addable. If they have **no** Telegram username at all (so there's no `@handle` to resolve), add them by id instead: `/contacts` prints a ready-to-copy `/add_player <id> "Name"` line for every non-roster contact.
- **RSVP buttons don't show** — make sure the bot is a member of `GROUP_CHAT_ID` and has permission to send messages.
- **Vote callbacks silently fail** — most often `BOT_TOKEN` is wrong or the bot isn't running. Check `logs/toop.log`.
- **Scheduled snapshot didn't fire Monday noon** — JobQueue requires the bot process to be alive at the scheduled time. Verify with `make logs` (look for `auto_snapshot scheduled`) or trigger manually with `/snapshot`. Note `SNAPSHOT_HOUR` is interpreted as UTC inside the container.
- **"weights sum to 0.95"** warning at startup — composite weights in `.env` don't add to 1.0. Bot still runs; ratings just scale slightly differently. Adjust to taste.
- **All-new roster shows "low confidence"** — expected during calibration. As players accumulate scores, confidence rises. Front-loading ratings in week 1 is the recommended pattern.

---

## Privacy

The rating model is **voter-linked by design** (the `scores` table carries `voter_id` plus the raw 1–5 score). This is a deliberate trade vs the retired pairwise model: storing each rater's scores is what lets the refit normalize out per-rater leniency/severity (a generous or harsh rater no longer skews a player's standing). Scores are never surfaced to the group, but the admin can see them in the database. Only the admin has DB access.

"🤷 ندیدمش" (don't know) taps are recorded in `score_skips` with no score. A daily job DMs the admin a `/pause_voting` suggestion for players whom voters most often can't rate (skip rate crossing `DK_ALERT_MIN_PROMPTS` + `DK_ALERT_RATE`); it reports only counts.

## Plan

See `docs/plans/done/2026-05-14-toop-volleyball-team-balancing-bot.md` for the implementation history.
