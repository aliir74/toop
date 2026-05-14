# توپ — Privacy Audit

**Last reviewed:** 2026-05-14
**Reviewer:** Ali (implementer)

## Invariant

> No code path — SQL or otherwise — joins a voter's identity (`voter_id`) with
> a specific vote outcome (which player they preferred in a comparison).

This is enforced at the schema layer by deliberately splitting the two
storage tables:

- `vote_aggregates(player_a, player_b, axis, a_wins, b_wins)` — outcome counts
  for a pair on an axis. **Has no `voter_id` column.**
- `answered_prompts(voter_id, player_a, player_b, axis, answered_at)` — voter
  dedupe ledger. **Has no `a_wins`, `b_wins`, or any outcome column.**

These two tables are populated together by `record_vote()` in a single
transaction, but they are never joined downstream.

## Code paths that touch vote data

Every SQL touchpoint, audited.

### Writes

| File:line | What | Privacy check |
| --- | --- | --- |
| `src/toop/voting_queue.py:record_vote` | Inserts a row into `vote_aggregates` (no voter_id) AND a row into `answered_prompts` (no outcome). | ✅ Each table only gets the column it's allowed to know. |
| `src/toop/voting_queue.py:mark_dont_know` | Writes only to `answered_prompts` (no outcome to record). | ✅ |
| `src/toop/voting_queue.py:add_snooze` | Writes to `snoozes`, deletes from `pending_prompts`. Does not touch outcome data. | ✅ |
| `src/toop/voting_queue.py:refill_queue` | Writes new candidate prompts to `pending_prompts`. Reads aggregates for ordering — never voter info. | ✅ |

### Reads — outcome side (`vote_aggregates`)

| File:line | What it reads | Privacy check |
| --- | --- | --- |
| `src/toop/voting_queue.py` refill_queue SQL | `a_wins + b_wins` per (pair, axis) for info-gain ordering. | ✅ No voter info in the result row. |
| `src/toop/rating.py:_load_aggregates_for_axis` | Per-axis aggregate counts for BT fitter. | ✅ Outcomes only feed the rating model; never surface to admin or other users. |
| `src/toop/rating.py:_vote_count_per_player` | Per-player vote totals across an axis (for calibration). | ✅ Returns counts only, no voter info. |
| `src/toop/handlers/health.py:build_coverage` | Per-pair aggregate totals across 3 axes. | ✅ Counts only, no voter info. |

### Reads — voter side (`answered_prompts`)

| File:line | What it reads | Privacy check |
| --- | --- | --- |
| `src/toop/voting_queue.py` refill_queue SQL | `NOT EXISTS` subquery to exclude already-answered prompts for the active voter. | ✅ Returns nothing to caller — it just filters the candidate list. |
| `src/toop/handlers/health.py:HEALTH_SQL` | `MAX(answered_at)`, `COUNT(*)`, `COUNT(*) WHERE answered_at >= -30d` per voter. | ✅ Counts and timestamps only — no pair, no axis, no outcome. |
| `src/toop/handlers/voting.py:_build_nudge_templates` | `COUNT(*) FROM answered_prompts` per voter for lifetime metric. | ✅ Count only. |

### No SQL anywhere joins `vote_aggregates` ↔ `answered_prompts`

Verified via grep: no query mentions both table names in the same `FROM`/`JOIN`
clause. The closest is `refill_queue` which LEFT JOINs `vote_aggregates` on
(pair, axis) and uses `answered_prompts` only in a `NOT EXISTS` subquery on
the same (pair, axis) tuple — the subquery never surfaces voter rows.

## Logs / observability

| Surface | Privacy check |
| --- | --- |
| `record_vote` and callbacks emit no log line with the chosen winner. | ✅ |
| Voting handler logs reference `voter_id` only on errors (missing player) and never alongside outcome data. | ✅ |
| `/health` and `/coverage` outputs render counts only, no per-vote contents. | ✅ |
| `/nudge` admin command outputs DM-able templates with lifetime count, never with vote contents. | ✅ |
| `/teams` / `/publish` render team rosters — these are public team assignments, not private votes. ✅ |

## Admin surfaces audited

| Command | Data touched | Risk |
| --- | --- | --- |
| `/health` | `players` + `answered_prompts` counts. | None. |
| `/coverage` | `vote_aggregates` totals. | None — counts only, no voter info. |
| `/nudge` | `players` + `answered_prompts` count. | None. |
| `/teams`, `/publish`, `/swap` | `snapshots`, `player_ratings`. | Team assignments are public by design. Composite scores are derived aggregates, not individual votes. |
| `/refresh_ratings` | Recomputes BT from `vote_aggregates`. | None. |
| `/sessions`, `/list_players`, `/add_player`, etc. | Roster + session metadata. | None. |

## Things deliberately NOT done

- ❌ No leaderboard or public ratings in MVP.
- ❌ No admin command that surfaces "who voted what".
- ❌ No DB column or index that would enable a join of voter_id with a winner.
- ❌ No auto-DMs to players based on their voting history (admin handles nudges manually via `/nudge` templates).

## Sign-off

I walked every handler and SQL path above and confirmed:
- ✅ No SQL query returns both a `voter_id` and a vote outcome (`a_wins`/`b_wins`) in the same row.
- ✅ No log line emits both `voter_id` and the chosen winner.
- ✅ No admin command surfaces an individual voter's choices.

— Ali, 2026-05-14
