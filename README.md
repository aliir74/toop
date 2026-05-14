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

See `docs/plans/` for the implementation plan.
