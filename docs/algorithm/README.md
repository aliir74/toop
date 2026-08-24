# Algorithm change log

Every change to how توپ **scores players** or **splits teams** gets an entry here, plus a
dated file if it needs the working. The point is that when the group says "the bot is
being unfair", you can answer with what changed, when, and what it measurably did,
instead of re-deriving it from `git log`.

## What belongs here

- Changes to the balancing objective (`balance.py`, `_optimal_assign`)
- Changes to rating maths (`rating.py`: normalization, shrinkage, calibration)
- Changes to composite weights, including `.env` drift on the live VPS
- Changes to what the group is shown about fairness (bar thresholds, headline numbers)

Bug fixes with no effect on scores or splits do not belong here; the commit is enough.

## Entry format

One row in the ledger. If the change moved real numbers, add
`YYYY-MM-DD-<slug>.md` next to it with the before/after and how it was measured.

## Ledger

| Date | Change | Effect on splits | Detail |
|---|---|---|---|
| 2026-08-24 | Objective: `Σ w·\|gap\|` → `Σ w·gap²` | Worst single-skill gap on session 11: **0.73 → 0.54**; block **0.73 → 0.20**. Historically fixes s8 (1.79 → 0.69) and s10 (0.99 → 0.47) | [detail](2026-08-24-block-gap-and-weight-drift.md) |
| 2026-08-24 | Live weights corrected: retired `WEIGHT_DEFENSE` removed, all six set to ~1/6 | Composite totals compress (weight sum 1.267 → 1.000); **11 of 25 players change rank**. No change to the session-11 split itself | [detail](2026-08-24-block-gap-and-weight-drift.md) |
| 2026-08-24 | Fairness bar thresholds: green ≤0.40/amber ≤0.80 → green ≤0.30/amber ≤0.60; bar scale 1.5 → 1.0 | Display only. A session-11-shaped block gap now renders **red**, not amber | [detail](2026-08-24-block-gap-and-weight-drift.md) |
| 2026-08-23 | 🤷 ندیدمش hides the whole player for a week rather than one skill for one session | Fewer low-confidence forced ratings; slightly thinner coverage per cycle | [#25](https://github.com/aliir74/toop/pull/25) |
| 2026-08-22 | Objective: composite-delta → weighted per-skill gap (`Σ w·\|gap\|`) | Stopped opposite-sign skill gaps cancelling into a fake-balanced total | [#22](https://github.com/aliir74/toop/pull/22) |
| 2026-08-22 | Snapshot message gained per-skill balance bars | Display only | [#23](https://github.com/aliir74/toop/pull/23) |

## Known open issues

Carried from the 2026-08-24 audit, not yet fixed. Listed so they are not rediscovered
from scratch next time the group complains.

| # | Issue | Where |
|---|---|---|
| 1 | Headline is still the composite delta, which structurally cannot show a single lopsided skill (opposite-sign gaps cancel). Should lead with the worst skill gap. | `handlers/snapshot.py` `_format_teams` |
| 2 | No sentence naming the trade the bot made ("Team A is stronger at the net, Team B on serve"). Bars alone read as "trust me". | `i18n.py` + `handlers/snapshot.py` |
| 3 | `CALIBRATION_THRESHOLD=15` but the best-rated player has 11 ratings, so **nobody has ever been calibrated** and every message says `confidence: low`. Either drop the threshold to ~10 or drive voting up. | `.env` |
| 4 | Shrinkage `k=3` keeps only 40% of a 2-vote player's signal vs 79% for an 11-vote regular, flattening exactly the players the group has the strongest opinions about. Turning it off moves 4 of 14 players to the other team, so the split is sensitive to an unvalidated knob. | `rating.py` `refresh_ratings` |
| 5 | `NORM_MIN_RATINGS=8` gates rater z-scoring on **score count**, not distinct players. Morteza (14 scores on 3 players) and EM (27 on 5) clear it and get z-scored against a sample too narrow to mean anything, manufacturing spread. | `rating.py` `_rater_stats` |
| 6 | Rating coverage ranges from 2 to 14 voters per player. No code change substitutes for chasing the thin ones. | admin, in person |

## Re-running the analysis

The audit scripts read a **read-only copy** of the live DB. Never point them at the VPS
file directly.

```bash
scp de-rarecloud:/opt/toop/data/toop.db{,-wal,-shm} /tmp/toop-audit/
sqlite3 /tmp/toop-audit/toop.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

`sqlite3` is not installed on the VPS, so copy the WAL and checkpoint locally rather
than trying to `.backup` remotely.
